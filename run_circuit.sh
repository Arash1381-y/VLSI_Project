#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CIRCUITS_ROOT="$PROJECT_ROOT/Input_Files/circuits"

usage() {
    cat <<'EOF'
Usage:
  ./run_circuit.sh <circuit-name-or-directory> [--debug] [--output-dir DIR]
  ./run_circuit.sh --all [--debug] [--output-root DIR]
  ./run_circuit.sh --list

Examples:
  ./run_circuit.sh c01_inverter_chain
  ./run_circuit.sh c05_multi_output --debug
  ./run_circuit.sh c05_multi_output --output-dir outputs/experiment-01
  ./run_circuit.sh Input_Files/circuits/valid/c10_three_input_gates
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

    if ! command -v python3.10 >/dev/null 2>&1; then
        echo "error: python3.10 is not available in PATH" >&2
        return 127
    fi

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
                python3.10 -m src.main
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
                ((analyzed += 1))
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
    python3.10 - "$1" <<'PY'
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
if ! command -v python3.10 >/dev/null 2>&1; then
    echo "error: python3.10 is not available in PATH" >&2
    exit 127
fi

cd -- "$PROJECT_ROOT"
exec python3.10 -m src.main "$netlist_path" "$config_path" "$@"
