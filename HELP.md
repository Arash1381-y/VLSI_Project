# Running the Circuit Analyzer and Interactive Viewer

## 1. Requirements

- Python 3.10 or newer
- A graphical web browser for the interactive viewer
- Python packages declared in `pyproject.toml`

From the project directory, install the package and its `vlsi-sta` command:

```bash
python3 -m pip install -e .
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

Add optimization convergence and outcome plots for every valid circuit with:

```bash
./run_circuit.sh --all --plot-optimization
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

Generate the optimization comparison plots immediately after analysis with:

```bash
./run_circuit.sh c01_inverter_chain --plot-optimization
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
./run_circuit.sh examples/circuits/valid/c06_deep_critical_path
```

That directory must contain both `netlist.txt` and `config.json`.

## 3. Run the analyzer without the helper script

The equivalent direct Python command is:

```bash
vlsi-sta analyze \
  examples/circuits/valid/c01_inverter_chain/netlist.txt \
  examples/circuits/valid/c01_inverter_chain/config.json
```

The configuration selects the shared cell library and controls timing,
optimization, power, area, and Monte Carlo settings.

## 4. Open the interactive circuit viewer

First run the analyzer so an output directory contains
`circuit_topology.json`. Start the viewer without a circuit argument:

```bash
vlsi-sta view
```

Use the `Dir` button in the left toolbar to select any generated circuit
output directory. Directory contents remain local to the browser. You can
still open a particular output directly from the command line:

```bash
vlsi-sta view \
  outputs/c01_inverter_chain
```

The viewer prints its local URL and opens it in the default browser. Its main
controls are:

- use `Dir` in the left toolbar to open another experiment output directory;
- drag the handle on the inspector's left edge to widen the comparison panel;
  its text scales with its width, arrow keys resize it when focused, and a
  double-click resets the default width; at larger widths the original and
  optimized summaries and gate details appear side by side; optimized values
  show signed percentage changes, except WNS/TNS which show absolute deltas;
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
vlsi-sta view \
  outputs/c01_inverter_chain \
  --no-browser
```

Choose a different port when the default port `8765` is occupied:

```bash
vlsi-sta view \
  outputs/c01_inverter_chain \
  --port 9000
```

Stop the viewer with `Ctrl+C` in its terminal.

## 5. Generate optimization comparison plots

The plots compare slack-weighted capacitance, criticality/effort-gap, and
random greedy using total attempted iterations on the horizontal axis.

Generate plots for every circuit under `outputs`:

```bash
vlsi-sta plot optimization outputs \
  --output-dir outputs/plots/all
```

Or plot one circuit only:

```bash
vlsi-sta plot optimization outputs/c06_deep_critical_path
```

## 6. Run checks

Run the automated tests:

```bash
python3 -m pytest -q
```

Run Pylance/Pyright-compatible type checking:

```bash
python3 -m pyright
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

### `python3 is not available` or is too old

Install Python 3.10 or newer and ensure `python3` is available on `PATH`. To use
a different compatible executable, set `PYTHON_BIN`, for example
`PYTHON_BIN=/custom/path/python3 ./run_circuit.sh c01_inverter_chain`.

### `circuit_topology.json` is missing or has an unsupported schema

Rerun the circuit analyzer with the current code, then launch the viewer against
the newly generated output directory.

### The viewer URL does not open automatically

Copy the printed `http://127.0.0.1:<port>` URL into a browser manually.

### An invalid example exits with status 2

This is expected. Invalid examples only produce `run.log` and
`validation_report.json`; timing and optimization are not executed.
