# VLSI Static Timing Analysis Project

## Overview

This archive contains the input data for the static timing analysis and gate-sizing assignment. It includes one shared cell library, 15 structurally valid circuits, and 6 deliberately invalid netlists for parser and validation tests.

A circuit listed as `valid` is structurally correct; it is not necessarily timing-clean. Several benchmarks intentionally start with negative slack so that the optimization stage has meaningful work to perform.

## Directory layout

```text
examples/
├── README.md
├── cell_library.json
└── circuits/
    ├── valid/<circuit_name>/
    │   ├── netlist.txt
    │   └── config.json
    └── invalid/<case_name>/
        ├── netlist.txt
        └── config.json
```

Each circuit directory is a self-contained test case. Process its `netlist.txt` and `config.json` together with the shared `cell_library.json`. The `cell_library` entry in each configuration file is a relative path resolved from the configuration file's directory.

## Input files

### `cell_library.json`

The cell library contains 36 cells from 12 logic families. Each family is available in `X1`, `X2`, and `X4` sizes. The file defines pin capacitance, timing sense, intrinsic rise/fall delay, equivalent output resistance, logical effort, parasitic delay, internal capacitance, leakage power, and area.

The library uses the following units:

| Quantity | Unit |
| --- | --- |
| Time | ns |
| Capacitance | fF |
| Resistance | kOhm |
| Leakage power | uW |
| Area | normalized unit |

### `netlist.txt`

The gate-level netlist uses the following grammar:

```text
INPUT <signal_name>
<instance_name> <cell_name> <input_1> ... <input_n> <output>
OUTPUT <signal_name>
```

For a gate line, the last signal is the output and all preceding signals are inputs. Identifiers are case-sensitive. Blank lines and text following `#` are comments and must be ignored. The legal input count and pin order of each cell are defined by the cell library.

### `config.json`

Each configuration file provides:

- rise and fall arrival times for all primary inputs;
- rise and fall required times and external loads for all primary outputs;
- the number of timing paths to report;
- supply voltage, operating frequency, temperature, and activity factors;
- maximum area and power limits;
- allowed cell sizes and optimization settings; and
- Monte Carlo settings.

Monte Carlo analysis is enabled only for `c12_monte_carlo_switching`. The same configuration block is present but disabled in the other test cases so that all configuration files follow one schema.

## Valid benchmarks

| ID | Main purpose |
| --- | --- |
| `c01_inverter_chain` | Hand-checkable load, delay, and slack calculations |
| `c02_basic_nand_path` | Short path with a repairable timing violation |
| `c03_fanout_branch` | Fanout loading and branching effort |
| `c04_reconvergent_paths` | Reconvergent timing paths |
| `c05_multi_output` | Independent output loads and constraints |
| `c06_deep_critical_path` | Forward/backward propagation on a deep path |
| `c07_parallel_paths` | Asymmetric rise/fall arrivals and constraints |
| `c08_non_unate_logic` | Non-unate XOR and XNOR timing arcs |
| `c09_high_fanout` | High fanout and multiple violating endpoints |
| `c10_three_input_gates` | Three-input cells and pin handling |
| `c11_gate_sizing_stress` | Large endpoint load and multi-stage sizing |
| `c12_monte_carlo_switching` | Statistical timing yield and critical-path switching |
| `c13_large_reconvergent_network` | 74-gate, six-output reconvergent network |
| `c14_layered_reconvergent_fabric` | 104-gate, six-layer network with eight outputs |
| `c15_high_fanout_sizing_fabric` | 118-gate high-fanout sizing benchmark |

## Invalid netlists

The six cases under `circuits/invalid` test undefined cells, undriven internal signals, multiple drivers, combinational loops, incorrect input counts, and undriven primary outputs. These cases must be rejected during validation; timing analysis and optimization must not run after a validation error.

Do not modify the supplied inputs. Make a copy if you need additional experiments. For the large circuits, avoid enumerating every possible path; retaining only the best `K` partial paths at each node and transition is sufficient for the required report.

## Generated reports

The project runner creates one `outputs/<circuit_name>` directory per circuit.
The stable artifact contract and timing CSV column definitions are documented in
the project-level `README.md`. Monte Carlo files use the same names for every
valid circuit; disabled runs contain header-only CSV tables and a JSON summary
with `status` set to `skipped`.
