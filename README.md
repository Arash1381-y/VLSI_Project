# VLSI Static Timing Analysis

For installation, analyzer commands, viewer controls, plotting, tests, and
troubleshooting, see [HELP.md](HELP.md).

Run one bundled circuit with:

```bash
./run_circuit.sh c01_inverter_chain
```

Add `--plot-optimization` to generate convergence and outcome plots after a
successful run. The same flag works with `./run_circuit.sh --all`.

Each invocation writes an independent directory under `outputs/<circuit_name>`.
Invalid inputs write `validation_report.json` and `run.log`, then exit with a
nonzero status without running analysis.

## Code structure

- `src/vlsi_sta/domain/` owns circuit and cell models with numeric helpers.
- `src/vlsi_sta/input/` parses and validates configurations and netlists.
- `src/vlsi_sta/analysis/` contains STA, logical-effort, and Monte Carlo analyses.
- `src/vlsi_sta/optimization/` contains optimizers and selection heuristics.
- `src/vlsi_sta/reporting/` owns report schemas, serialization, plots, and graph
  output.
- `src/vlsi_sta/app/` coordinates normal analysis workflows and provides the
  unified CLI.
- `src/vlsi_sta/benchmarking/` contains benchmark configuration, generation,
  evaluation, and plotting. Generation and evaluation expose stable package APIs
  while their implementations live in separate subpackages.
- `src/vlsi_sta/viewer/` packages the topology viewer server and static assets.
- `examples/` contains bundled valid/invalid circuits and benchmark configuration.
- `scripts/` is reserved for repository automation such as release packaging.

Install the package in editable mode to expose the command-line entry point:

```bash
python3.10 -m pip install -e .
vlsi-sta --help
```

## Interactive topology viewer

Every analysis exports `circuit_topology.json`, a versioned graph contract. It
contains one shared topology (primary inputs, gates, primary outputs, and named
nets) plus original and canonical-optimized overlays. The overlays provide gate
cell, load capacitance, rise/fall delay, and ranked critical paths.

Open one circuit output directory with:

```bash
vlsi-sta view outputs/c15_high_fanout_sizing_fabric
```

The local viewer opens in a browser and supports mouse/touch panning, cursor-
anchored wheel zoom, fit-to-view, optional net labels, node selection, connected-
net isolation, original/optimized visibility filters, dual-state gate inspection,
critical-path coloring, target-size highlighting for resized gates, and keyboard
controls (`+`, `-`, `F`, `0`, and `Escape`). X2 targets use an amber halo; X4
targets use a wider purple halo. Resize highlighting is shown only while both
original and optimized states are enabled.
Use `--no-browser`, `--host`, or `--port` when running it remotely or in
automation.

## Output contract

Every valid circuit produces these core artifacts:

- `validation_report.json` and `run.log`: structured input validation and the
  complete execution log.
- `fanout_capacitances.csv` and `gate_delays.csv`: topologically ordered,
  one-row-per-gate physical data.
- `circuit_topology.json`: shared topology with original/optimized gate metrics
  and critical-path overlays for interactive or external graph consumers.
- `timing_analysis.csv`: one topologically ordered row per gate, including load,
  rise/fall delay, arrival time, required time, transition slack, and node slack.
- `timing_summary.csv` and `critical_paths.csv`: overall circuit timing metrics
  and the configured top-K transition-aware critical paths.
- `logical_effort_paths.csv`, `logical_effort_stages.csv`, and
  `logical_effort_candidates.csv`: normalized path, stage, and discrete sizing
  records for `G`, `B`, `H`, `F`, `P`, optimal effort, theoretical delay, and
  target capacitance.
- `optimization_*.csv`, `optimization_summary.csv`, and
  `optimization_comparison.csv`: complete iteration histories and the
  slack-weighted, criticality/effort-gap, and random-greedy comparison,
  including STA calls.
- `circuit_graph_pre_optimization.png` and
  `circuit_graph_post_optimization.png`: slack-colored DAGs with critical paths.
- `monte_carlo_*.csv` and `monte_carlo_summary.json`: paired statistical timing
  data, or consistently named empty tables plus a skipped summary when disabled.
- `summary.json`: the aggregate nominal, optimized, statistical, constraint, and
  artifact manifest.

CSV column names carry units (`_ns`, `_fF`, and `_uW`). Remaining JSON files
represent hierarchical validation, topology, statistical-summary, or aggregate
run data. Computation and serialization retain Python floating-point precision;
rounding is limited to plot labels.

The specialized gate tables use these columns:

| File | Columns |
| --- | --- |
| `fanout_capacitances.csv` | `gate`, `cell`, `output_net`, `is_primary_output`, `fanout_capacitance_fF` |
| `gate_delays.csv` | `gate`, `cell`, `output_net`, `delay_rise_ns`, `delay_fall_ns` |
| `timing_summary.csv` | Circuit delay, WNS, TNS, compliance, runtime, and critical-path counts |

Logical-effort data is normalized by `path_rank` and `stage_index`. Candidate
cells occupy separate rows in `logical_effort_candidates.csv`, avoiding encoded
lists inside CSV fields.

`timing_analysis.csv` uses the following columns:

| Column | Meaning |
| --- | --- |
| `node`, `cell`, `output_net` | Gate instance, selected library cell, and driven net |
| `is_primary_output` | Whether the driven net is a circuit output |
| `cload_fF` | Total fanout plus external output load |
| `delay_rise_ns`, `delay_fall_ns` | Gate propagation delays |
| `at_rise_ns`, `at_fall_ns` | Rise/fall arrival times at the output net |
| `rt_rise_ns`, `rt_fall_ns` | Rise/fall required times at the output net |
| `slack_rise_ns`, `slack_fall_ns` | Required time minus arrival time |
| `node_slack_ns` | Minimum of the rise and fall slacks |

## Sizing benchmark generator and evaluator

The standalone benchmarking package creates deterministic, repairable sizing
problems without changing the analyzer CLI or storing benchmark state on a
`Circuit`. Start from [examples/benchmark_config.json](examples/benchmark_config.json)
and run:

```bash
vlsi-sta benchmark generate examples/benchmark_config.json
vlsi-sta benchmark evaluate benchmarks/complex_repair_suite
```

Both commands show concise INFO-level progress by default: suite totals, the
current case number, and completed-case counts. Detailed generation attempts,
beam-search expansions, perturbations, optimizer runs, oracle replay decisions,
and subsystem analysis logs are available at DEBUG. Select quieter or more
detailed output with:

```bash
vlsi-sta benchmark --log-level WARNING generate examples/benchmark_config.json
vlsi-sta benchmark --log-level DEBUG evaluate benchmarks/complex_repair_suite
```

The configuration is schema-versioned and strict: unknown/missing fields,
invalid ranges or enums, unsupported family/size combinations, empty sources,
and incompatible seed circuits are rejected before generation. All random seeds
are derived from SHA-256 values, so they do not depend on Python's randomized
`hash()` implementation.

Generation supports structured levelized DAGs and immutable topologies imported
from valid seed circuits. It establishes an independent best-known reference by
multi-start beam search over every gate and adjacent legal size, then downsizes
one or more gates until the serialized initial circuit has negative WNS while
remaining inside its area and power limits. The reference is certified only as
`best_known_multi_start_beam`; global optimality is never implied. A suite uses
this layout:

```text
benchmarks/<suite>/
  suite_config.json
  cell_library.json
  suite_manifest.json
  generation_cases.csv
  generation_failures.csv
  cases/<case-id>/
    netlist.txt
    config.json
    reference_assignment.json
    benchmark_manifest.json
    planted_mutations.csv
```

Each case is written atomically after its initial and reference invariants have
been replayed through the production parser, `Circuit`, and STA implementation.
If retries are exhausted, generation exits nonzero and preserves the suite-level
failure diagnostics without leaving a partial case directory.

Evaluation verifies the copied library digest and reference assignment, runs all
configured heuristics, repeats random greedy with deterministic seeds, and
enforces the configured per-run timeout. Independent cases run in parallel using
`evaluation.parallel_workers` (default: `4`), while their report fragments are
merged in manifest order for reproducible output. For every recorded optimizer
decision, an independent counterfactual oracle evaluates every allowed same-family
replacement on every gate—not only reported critical-path gates. It ranks timing
repair by WNS, tied-WNS TNS, and cost; after repair it ranks cost reduction while
preserving timing, area, and power. Exact planted-assignment recovery is retained
as a diagnostic, while compliant timing repair is the primary result.

Every evaluation run creates:

- `case_runs.csv`: final metrics, constraint-repair status, runtime, iteration and
  STA-call counts, reference distance, and timeout/error state.
- `optimizer_iterations.csv`: replayable optimizer histories.
- `oracle_states.csv`: one row per unique gate-cell assignment encountered while
  replaying a case, including its stable state ID and assignment digest.
- `oracle_candidates.csv`: every counterfactual gate/cell candidate once per
  unique oracle state, with timing, physical, cost, feasibility, beneficial, and
  tied-best status. Repeated rejected decisions reference the existing state
  instead of duplicating all candidate rows.
- `gate_selection_scores.csv`: gate/move hits, WNS/TNS/cost regret, off-path
  opportunities, planted-gate diagnostics, and the corresponding oracle state ID.
- `heuristic_summary.csv`: Wilson intervals, failure and timeout rates, random
  runtime dispersion, selection accuracy, regret, reference, and effort metrics.
- `evaluation_summary.json`: hashes, hierarchical aggregates grouped by source,
  size, depth, fanout, reconvergence, violation severity, and headroom, plus the
  artifact map.

Generate suite-level comparison plots from an evaluation directory with:

```bash
vlsi-sta benchmark plot \
  benchmarks/<suite>/evaluation/<run-id>
```

The plots are written to `<run-id>/plots` by default. They cover repair-rate
confidence intervals, convergence by total attempted iteration, per-case repair
reliability, runtime scaling, final cost, STA work, and oracle-based gate-selection
quality. Use `--output-dir` to choose another destination and `--dpi` to control
image resolution.
