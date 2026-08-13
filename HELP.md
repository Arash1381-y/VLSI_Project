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

- drag to pan;
- mouse wheel or `+`/`-` to zoom;
- `Fit`, `F`, or `0` to fit the complete graph;
- click a node to inspect its nets and original/optimized gate data;
- `Escape` to clear the selection;
- `N` control to show or hide net labels;
- `Original` and `Optimized` filters to show or hide each timing state;
- orange dashed and teal solid lines for original and optimized critical paths;
- amber halos for gates resized to X2 and wider purple halos for X4.

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

## 7. Common errors

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
