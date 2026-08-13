# VLSI Static Timing Analysis

For installation, analyzer commands, viewer controls, plotting, tests, and
troubleshooting, see [HELP.md](HELP.md).

Run one bundled circuit with:

```bash
./run_circuit.sh c01_inverter_chain
```

Each invocation writes an independent directory under `outputs/<circuit_name>`.
Invalid inputs write `validation_report.json` and `run.log`, then exit with a
nonzero status without running analysis.

## Code structure

- `main.py` is the minimal executable entry point; `cli.py` defines arguments,
  while `application.py` coordinates validation, loading, and experiment startup.
- `circuit.py`, `netlist.py`, `cell.py`, and `config.py` own immutable input and
  derived circuit data.
- `sta.py`, `logical_effort.py`, `optimizer.py`, and `monte_carlo.py` contain
  analysis algorithms and return result values rather than storing run state on
  the circuit.
- `experiments.py` coordinates the configured workflow and caches results that
  later steps reuse.
- `*_reports.py`, `report_models.py`, and `experiment_artifacts.py` convert
  results into stable report schemas and handle serialization.
- `visualization.py` builds and renders the deterministic circuit DAG.
- `scripts/` contains optional post-processing tools that consume generated
  artifacts but are not part of the circuit-analysis runtime. For example:

  ```bash
  python3.10 -m scripts.plot_optimization outputs
  ```

## Interactive topology viewer

Every analysis exports `circuit_topology.json`, a versioned graph contract. It
contains one shared topology (primary inputs, gates, primary outputs, and named
nets) plus original and canonical-optimized overlays. The overlays provide gate
cell, load capacitance, rise/fall delay, and ranked critical paths.

Open one circuit output directory with:

```bash
python3.10 -m scripts.circuit_visualizer outputs/c15_high_fanout_sizing_fabric
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
- `fanout_capacitances.json` and `gate_delays.json`: unit-tagged physical data.
- `circuit_topology.json`: shared topology with original/optimized gate metrics
  and critical-path overlays for interactive or external graph consumers.
- `timing_analysis.csv`: one topologically ordered row per gate, including load,
  rise/fall delay, arrival time, required time, transition slack, and node slack.
- `timing_analysis.json` and `critical_paths.csv`: circuit timing metrics and the
  configured top-K transition-aware critical paths.
- `logical_effort_analysis.json`: path and stage values for `G`, `B`, `H`, `F`,
  `P`, optimal effort, theoretical delay, target capacitance, and discrete cells.
- `optimization_*.csv`, `optimization_summary.csv`, and
  `optimization_comparison.json`: complete iteration histories and the
  logical-effort-guided versus random-greedy comparison, including STA calls.
- `circuit_graph_pre_optimization.png` and
  `circuit_graph_post_optimization.png`: slack-colored DAGs with critical paths.
- `monte_carlo_*.csv` and `monte_carlo_summary.json`: paired statistical timing
  data, or consistently named empty tables plus a skipped summary when disabled.
- `summary.json`: the aggregate nominal, optimized, statistical, constraint, and
  artifact manifest.

CSV column names carry units (`_ns`, `_fF`, and `_uW`). JSON files either carry
unit metadata or use unit-suffixed property names. Computation and serialization
retain Python floating-point precision; rounding is limited to plot labels.

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
