from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from src.cell import CellLibrary
from src.circuit import Circuit
from src.config import Config
from src.experiments import DEFAULT_EXPERIMENTS, Experiments
from src.logical_effort import analyze_path_logical_effort
from src.netlist import NetListParser
from src.optimization_heuristics import OptimizationHeuristic
from src.optimizer import CircuitOptimizer
from src.sta import analyze_timing
from src.topology import circuit_analysis_graph, circuit_topology
from src.visualization import build_circuit_graph
from src.visualization import _GraphStyle, _gate_node_area, _gate_outline_width


ROOT = Path(__file__).resolve().parent.parent
CIRCUITS = ROOT / "Input_Files" / "circuits"


def load_circuit(name: str) -> Circuit:
    directory = CIRCUITS / "valid" / name
    config = Config(directory / "config.json")
    library = CellLibrary(config.cell_library_path)
    nets, gates = NetListParser(directory / "netlist.txt", library).parse()
    return Circuit(nets, gates, config, library)


@pytest.mark.parametrize(
    ("name", "code"),
    (
        ("e01_undefined_cell", "undefined_cell"),
        ("e02_undriven_signal", "undriven_signal"),
        ("e03_multiple_drivers", "multiple_drivers"),
        ("e04_combinational_loop", "combinational_loop"),
        ("e05_wrong_input_count", "invalid_input_count"),
        ("e06_undriven_output", "undriven_output"),
    ),
)
def test_invalid_circuit_writes_only_validation_artifacts(
    tmp_path: Path,
    name: str,
    code: str,
) -> None:
    directory = CIRCUITS / "invalid" / name
    output = tmp_path / name
    completed = subprocess.run(
        (
            sys.executable,
            "-m",
            "src.main",
            str(directory / "netlist.txt"),
            str(directory / "config.json"),
            "--output-dir",
            str(output),
        ),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0
    assert {path.name for path in output.iterdir()} == {
        "run.log",
        "validation_report.json",
    }
    report = json.loads((output / "validation_report.json").read_text())
    assert report["valid"] is False
    assert report["errors"][0]["code"] == code


def test_timing_result_and_csv_expose_complete_gate_timing(tmp_path: Path) -> None:
    circuit = load_circuit("c01_inverter_chain")
    result = analyze_timing(circuit)
    Experiments(
        tmp_path,
        ("fanout_capacitances", "gate_delays", "timing_analysis"),
        CIRCUITS / "valid" / "c01_inverter_chain" / "netlist.txt",
    ).run(circuit)

    with (tmp_path / "timing_analysis.csv").open(newline="") as timing_file:
        rows = list(csv.DictReader(timing_file))
    assert [row["node"] for row in rows] == ["G1", "G2", "G3"]
    for row in rows:
        gate = circuit.gates[row["node"]]
        assert gate.output is not None
        net_name = gate.output.name
        assert float(row["cload_fF"]) == circuit.fanout_capacitances[gate.name]
        assert float(row["at_rise_ns"]) == result.arrival_times[net_name].rise
        assert float(row["rt_fall_ns"]) == result.required_times[net_name].fall
        assert float(row["node_slack_ns"]) == result.node_slack(net_name)


def test_logical_effort_report_identities_and_branching() -> None:
    circuit = load_circuit("c03_fanout_branch")
    timing = analyze_timing(circuit)
    analyses = [
        analyze_path_logical_effort(circuit, critical.path)
        for critical in timing.critical_paths
    ]
    assert analyses
    assert any(analysis.branching_effort > 1.0 for analysis in analyses)
    for analysis in analyses:
        assert analysis.total_effort == pytest.approx(
            analysis.path_logical_effort
            * analysis.branching_effort
            * analysis.electrical_effort
        )
        assert analysis.optimal_stage_effort == pytest.approx(
            analysis.total_effort ** (1.0 / len(analysis.stages))
        )
        assert analysis.minimum_delay == pytest.approx(
            circuit.cell_library.logical_effort_tau
            * (
                len(analysis.stages) * analysis.optimal_stage_effort
                + analysis.parasitic_delay
            )
        )
        for stage in analysis.stages:
            assert all(
                cell.family == stage.step.gate.cell.family
                for cell in stage.candidates
            )


def test_optimizer_reports_exact_sta_call_count() -> None:
    circuit = load_circuit("c06_deep_critical_path")
    from src import optimizer as optimizer_module

    original = optimizer_module.analyze_timing
    call_count = 0

    def counted(circuit_to_analyze: Circuit):
        nonlocal call_count
        call_count += 1
        return original(circuit_to_analyze)

    with patch("src.optimizer.analyze_timing", side_effect=counted):
        result = CircuitOptimizer(
            circuit,
            OptimizationHeuristic.SLACK_WEIGHTED_CAPACITANCE,
        ).optimize()
    assert result.sta_calls == call_count
    assert result.total_iterations == result.history[-1].iteration


def test_complete_core_artifact_manifest_and_summary(tmp_path: Path) -> None:
    circuit = load_circuit("c01_inverter_chain")
    netlist = CIRCUITS / "valid" / "c01_inverter_chain" / "netlist.txt"
    Experiments(tmp_path, DEFAULT_EXPERIMENTS, netlist).run(circuit)
    required = {
        "fanout_capacitances.json",
        "gate_delays.json",
        "timing_analysis.json",
        "timing_analysis.csv",
        "critical_paths.csv",
        "logical_effort_analysis.json",
        "optimization_slack_weighted_capacitance.csv",
        "optimization_random_greedy.csv",
        "optimization_summary.csv",
        "optimization_comparison.json",
        "circuit_topology.json",
        "circuit_graph_pre_optimization.png",
        "circuit_graph_post_optimization.png",
        "monte_carlo_summary.json",
        "summary.json",
    }
    assert required <= {path.name for path in tmp_path.iterdir()}
    assert (tmp_path / "circuit_graph_pre_optimization.png").read_bytes().startswith(
        b"\x89PNG\r\n\x1a\n"
    )
    summary = json.loads((tmp_path / "summary.json").read_text())
    post = summary["post_optimization"]
    compliance = summary["compliance"]
    assert compliance["timing_compliant"] == (post["wns_ns"] >= 0.0)
    assert compliance["all_constraints_compliant"] == all(
        compliance[key]
        for key in ("timing_compliant", "power_compliant", "area_compliant")
    )

    graph, positions = build_circuit_graph(circuit)
    assert nx_is_dag(graph)
    assert set(graph.nodes) == set(positions)


def test_visual_gate_size_and_outline_increase_with_cell_strength() -> None:
    style = _GraphStyle((14.0, 9.0), 2600, 1600, 8, 12)
    areas = [_gate_node_area(style, factor) for factor in (1.0, 2.0, 4.0)]
    outlines = [_gate_outline_width(factor) for factor in (1.0, 2.0, 4.0)]

    assert areas[0] < areas[1] < areas[2]
    assert outlines[0] < outlines[1] < outlines[2]


def test_topology_export_contains_only_connectivity_data() -> None:
    circuit = load_circuit("c03_fanout_branch")
    topology = circuit_topology(circuit)

    assert topology["schema_version"] == 1
    nodes = topology["nodes"]
    nets = topology["nets"]
    assert isinstance(nodes, list)
    assert isinstance(nets, list)
    assert any(net["name"] == "N1" and len(net["targets"]) > 1 for net in nets)
    forbidden = {"delay", "capacitance", "slack", "power", "area", "size"}
    keys = {key.lower() for record in nodes + nets for key in record}
    assert not any(word in key for key in keys for word in forbidden)


def test_analysis_graph_compares_gate_metrics_and_critical_paths() -> None:
    circuit = load_circuit("c03_fanout_branch")
    timing = analyze_timing(circuit)
    graph = circuit_analysis_graph(circuit, timing, circuit, timing)

    assert graph["schema_version"] == 2
    states = graph["states"]
    assert isinstance(states, dict)
    for state in (states["original"], states["optimized"]):
        gate = state["gates"]["G1"]
        assert set(gate) == {
            "cell",
            "size",
            "load_capacitance",
            "delay_rise",
            "delay_fall",
        }
        assert state["critical_paths"]
        assert state["critical_paths"][0]["nets"]


def nx_is_dag(graph: object) -> bool:
    import networkx as nx

    return bool(nx.is_directed_acyclic_graph(graph))
