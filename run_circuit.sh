#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CIRCUITS_ROOT="$PROJECT_ROOT/examples/circuits"
PYTHON_BIN=${PYTHON_BIN:-python3}

require_python() {
    local current_version
    if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
        echo "error: Python executable '$PYTHON_BIN' is not available in PATH" >&2
        return 127
    fi
    if ! "$PYTHON_BIN" -c \
        'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
        current_version=$(
            "$PYTHON_BIN" -c \
                'import sys; print(".".join(map(str, sys.version_info[:3])))'
        )
        echo "error: Python 3.10 or newer is required; found $current_version" >&2
        return 2
    fi
}

usage() {
    cat <<'EOF'
Usage:
  ./run_circuit.sh <circuit-name-or-directory> [--debug] [--plot-optimization] [--output-dir DIR]
  ./run_circuit.sh --all [--debug] [--plot-optimization] [--output-root DIR]
  ./run_circuit.sh --list

Examples:
  ./run_circuit.sh c01_inverter_chain
  ./run_circuit.sh c05_multi_output --debug
  ./run_circuit.sh c05_multi_output --output-dir outputs/experiment-01
  ./run_circuit.sh c05_multi_output --plot-optimization
  ./run_circuit.sh examples/circuits/valid/c10_three_input_gates
  ./run_circuit.sh --all
  ./run_circuit.sh --all --output-root outputs/all-experiments
EOF
}

list_circuits() {
    local category circuit_dir
    for category in valid invalid; do
        echo "$category:"
        for circuit_dir in "$CIRCUITS_ROOT/$category"/*; do
            [[ -d "$circuit_dir" ]] || continue
            echo "  $(basename -- "$circuit_dir")"
        done
    done
}

run_all_circuits() {
    local output_root=""
    local debug=false
    local plot_optimization=false
    local category circuit_dir circuit_name output_dir exit_status
    local analyzed=0
    local rejected=0
    local failed=0
    local -a command

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --debug)
                debug=true
                shift
                ;;
            --plot|--plot-optimization)
                plot_optimization=true
                shift
                ;;
            --output-root)
                if [[ $# -lt 2 ]]; then
                    echo "error: --output-root requires a directory" >&2
                    return 2
                fi
                output_root=$2
                shift 2
                ;;
            -h|--help)
                usage
                return 0
                ;;
            *)
                echo "error: unsupported --all option: $1" >&2
                echo "Use --output-root instead of --output-dir in --all mode." >&2
                return 2
                ;;
        esac
    done

    require_python || return $?

    cd -- "$PROJECT_ROOT"
    if [[ -z "$output_root" ]]; then
        output_root="$PROJECT_ROOT/outputs"
    fi

    for category in valid invalid; do
        for circuit_dir in "$CIRCUITS_ROOT/$category"/*; do
            [[ -d "$circuit_dir" ]] || continue
            circuit_name=$(basename -- "$circuit_dir")
            output_dir="$output_root/$circuit_name"
            if [[ "$category" == "valid" ]]; then
                echo "==> Running all experiments for $circuit_name"
            else
                echo "==> Running validation rejection for $circuit_name"
            fi

            command=(
                env PYTHONPATH="$PROJECT_ROOT/src${PYTHONPATH:+:$PYTHONPATH}" \
                "$PYTHON_BIN" -m vlsi_sta analyze
                "$circuit_dir/netlist.txt"
                "$circuit_dir/config.json"
                --output-dir "$output_dir"
            )
            if [[ "$debug" == true ]]; then
                command+=(--debug)
            fi

            if "${command[@]}"; then
                exit_status=0
            else
                exit_status=$?
            fi

            if [[ "$category" == "valid" && $exit_status -eq 0 ]]; then
                if [[ "$plot_optimization" == true ]] \
                    && ! env PYTHONPATH="$PROJECT_ROOT/src${PYTHONPATH:+:$PYTHONPATH}" \
                    "$PYTHON_BIN" -m vlsi_sta plot optimization "$output_dir"; then
                    ((failed += 1))
                    echo "error: optimization plotting failed for $circuit_name" >&2
                else
                    ((analyzed += 1))
                fi
            elif [[ "$category" == "invalid" && $exit_status -ne 0 ]] \
                && validation_report_rejected "$output_dir/validation_report.json"; then
                ((rejected += 1))
                echo "    Correctly rejected $circuit_name"
            else
                ((failed += 1))
                echo "error: unexpected result for $category circuit $circuit_name" >&2
            fi
        done
    done

    echo "Completed all circuits: $analyzed valid analyzed, "\
"$rejected invalid correctly rejected, $failed failed."
    ((failed == 0))
}

validation_report_rejected() {
    "$PYTHON_BIN" - "$1" <<'PY'
import json
import sys
from pathlib import Path

try:
    report = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    raise SystemExit(1)

raise SystemExit(
    0
    if report.get("valid") is False and bool(report.get("errors"))
    else 1
)
PY
}

if [[ $# -eq 0 ]]; then
    usage
    exit 2
fi

case "$1" in
    -h|--help)
        usage
        exit 0
        ;;
    -l|--list)
        list_circuits
        exit 0
        ;;
    -a|--all)
        shift
        run_all_circuits "$@"
        exit $?
        ;;
esac

target=$1
shift

plot_optimization=false
requested_output_dir=""
forwarded_arguments=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --plot|--plot-optimization)
            plot_optimization=true
            shift
            ;;
        --output-dir)
            if [[ $# -lt 2 ]]; then
                echo "error: --output-dir requires a directory" >&2
                exit 2
            fi
            requested_output_dir=$2
            forwarded_arguments+=("$1" "$2")
            shift 2
            ;;
        --output-dir=*)
            requested_output_dir=${1#--output-dir=}
            forwarded_arguments+=("$1")
            shift
            ;;
        *)
            forwarded_arguments+=("$1")
            shift
            ;;
    esac
done

if [[ -d "$target" ]]; then
    circuit_dir=$target
elif [[ -d "$PROJECT_ROOT/$target" ]]; then
    circuit_dir="$PROJECT_ROOT/$target"
else
    matches=()
    for category in valid invalid; do
        candidate="$CIRCUITS_ROOT/$category/$target"
        [[ -d "$candidate" ]] && matches+=("$candidate")
    done

    if [[ ${#matches[@]} -eq 0 ]]; then
        echo "error: circuit '$target' was not found" >&2
        echo "Run '$0 --list' to see the available circuits." >&2
        exit 2
    fi
    if [[ ${#matches[@]} -gt 1 ]]; then
        echo "error: circuit name '$target' is ambiguous; pass its directory instead" >&2
        exit 2
    fi
    circuit_dir=${matches[0]}
fi

netlist_path="$circuit_dir/netlist.txt"
config_path="$circuit_dir/config.json"

if [[ ! -f "$netlist_path" ]]; then
    echo "error: missing netlist: $netlist_path" >&2
    exit 2
fi
if [[ ! -f "$config_path" ]]; then
    echo "error: missing configuration: $config_path" >&2
    exit 2
fi
require_python || exit $?

cd -- "$PROJECT_ROOT"
if env PYTHONPATH="$PROJECT_ROOT/src${PYTHONPATH:+:$PYTHONPATH}" \
    "$PYTHON_BIN" -m vlsi_sta analyze \
    "$netlist_path" "$config_path" "${forwarded_arguments[@]}"; then
    :
else
    exit_status=$?
    exit "$exit_status"
fi

if [[ "$plot_optimization" == true ]]; then
    if [[ -n "$requested_output_dir" ]]; then
        plot_input=$requested_output_dir
    else
        plot_input=$("$PYTHON_BIN" - "$config_path" "$PROJECT_ROOT/outputs" <<'PY'
import json
import sys
from pathlib import Path

config_path = Path(sys.argv[1])
output_root = Path(sys.argv[2])
configuration = json.loads(config_path.read_text(encoding="utf-8"))
print(output_root / configuration["circuit_name"])
PY
)
    fi
    env PYTHONPATH="$PROJECT_ROOT/src${PYTHONPATH:+:$PYTHONPATH}" \
        "$PYTHON_BIN" -m vlsi_sta plot optimization "$plot_input"
fi
