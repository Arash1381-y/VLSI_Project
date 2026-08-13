#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CIRCUITS_ROOT="$PROJECT_ROOT/Input_Files/circuits"

usage() {
    cat <<'EOF'
Usage:
  ./run_circuit.sh <circuit-name-or-directory> [--debug] [--output-dir DIR]
  ./run_circuit.sh --list

Examples:
  ./run_circuit.sh c01_inverter_chain
  ./run_circuit.sh c05_multi_output --debug
  ./run_circuit.sh c05_multi_output --output-dir outputs/experiment-01
  ./run_circuit.sh Input_Files/circuits/valid/c10_three_input_gates
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
