# Running the Circuit Analyzer and Interactive Viewer

## 1. Requirements

- Python 3.10
- A graphical web browser for the interactive viewer
- Python packages listed in `requirements.txt`

From the project directory, install the runtime dependencies:

```bash
python3.10 -m pip install -r requirements.txt
```

## 2. Run a bundled circuit

List all valid and invalid examples:

```bash
./run_circuit.sh --list
```

Run the complete configured experiment pipeline for every bundled circuit,
including the invalid validation examples:

```bash
./run_circuit.sh --all
```

By default, each circuit writes to its normal `outputs/<circuit-name>`
directory. To place the complete batch under a separate output root, use:

```bash
./run_circuit.sh --all --output-root outputs/all-experiments
```

Add `--debug` to either form to enable debug logging for every circuit. Valid
circuits run the complete analysis pipeline. Invalid examples are expected to
exit nonzero and count as successful when their report contains
`valid: false` and at least one structured validation error. Batch mode
continues after unexpected failures, prints separate analyzed/rejected/failure
counts, and exits nonzero if any result is unexpected.

Run a valid circuit by directory name:

```bash
./run_circuit.sh c01_inverter_chain
```

Results are written to:

```text
outputs/c01_inverter_chain/
```

Choose another output directory with:

```bash
./run_circuit.sh c01_inverter_chain --output-dir outputs/my_experiment
```

Enable detailed logs with:

```bash
./run_circuit.sh c01_inverter_chain --debug
```

You can also pass an entire circuit directory:

```bash
./run_circuit.sh Input_Files/circuits/valid/c06_deep_critical_path
```

That directory must contain both `netlist.txt` and `config.json`.

## 3. Run the analyzer without the helper script

The equivalent direct Python command is:

```bash
python3.10 -m src.main \
  Input_Files/circuits/valid/c01_inverter_chain/netlist.txt \
  Input_Files/circuits/valid/c01_inverter_chain/config.json
```

The configuration selects the shared cell library and controls timing,
optimization, power, area, and Monte Carlo settings.

## 4. Open the interactive circuit viewer

First run the analyzer so the circuit output directory contains
`circuit_topology.json`. Then start the viewer with that directory:

```bash
python3.10 -m scripts.circuit_visualizer \
  outputs/c01_inverter_chain
```

The viewer prints its local URL and opens it in the default browser. Its main
controls are:

- use `Analysis` for the compact topology view or `Schematic` for actual gate
  symbols with pin-aware orthogonal wiring;
- drag to pan;
- mouse wheel or `+`/`-` to zoom;
- `Fit`, `F`, or `0` to fit the complete graph;
- click a node to inspect its nets and original/optimized gate data;
- `Escape` to clear the selection;
- `N` control to show or hide net labels;
- `V` to switch between analysis and schematic views;
- `Original` and `Optimized` filters to show or hide each timing state;
- orange dashed and teal solid lines for original and optimized critical paths;
- amber halos for gates resized to X2 and wider purple halos for X4.

The schematic view is prepared in the background with a layered layout that
minimizes crossings, fixes every wire to its actual input pin, shares fanout
routes, and uses orthogonal bends. You can open it directly with:

```text
http://127.0.0.1:8765/?view=schematic
```

To start the server without opening a browser:

```bash
python3.10 -m scripts.circuit_visualizer \
  outputs/c01_inverter_chain \
  --no-browser
```

Choose a different port when the default port `8765` is occupied:

```bash
python3.10 -m scripts.circuit_visualizer \
  outputs/c01_inverter_chain \
  --port 9000
```

Stop the viewer with `Ctrl+C` in its terminal.

## 5. Generate optimization comparison plots

Generate plots for every circuit under `outputs`:

```bash
python3.10 -m scripts.plot_optimization outputs \
  --output-dir outputs/plots/all
```

Or plot one circuit only:

```bash
python3.10 -m scripts.plot_optimization outputs/c06_deep_critical_path
```

## 6. Run checks

Run the automated tests:

```bash
python3.10 -m pytest -q
```

Run Pylance/Pyright-compatible type checking:

```bash
python3.10 -m pyright
```

## 7. Create a project ZIP

Packaging uses the current Git commit, so commit all intended changes first:

```bash
./scripts/package_project.sh
```

The default archive is `../impl.zip`. It contains an `impl/` top-level
directory and excludes `.git`, generated `outputs`, caches, and existing ZIP
artifacts. Choose another destination or replace an existing archive with:

```bash
./scripts/package_project.sh dist/submission.zip --force
```

## 8. Common errors

### `python3.10 is not available`

Install Python 3.10 or ensure its executable is available on `PATH`.

### `circuit_topology.json` is missing or has an unsupported schema

Rerun the circuit analyzer with the current code, then launch the viewer against
the newly generated output directory.

### The viewer URL does not open automatically

Copy the printed `http://127.0.0.1:<port>` URL into a browser manually.

### An invalid example exits with status 2

This is expected. Invalid examples only produce `run.log` and
`validation_report.json`; timing and optimization are not executed.
