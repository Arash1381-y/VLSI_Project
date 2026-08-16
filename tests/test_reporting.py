from __future__ import annotations

import csv
import json
import subprocess
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

import pytest

from src.cell import CellLibrary
from src.circuit import Circuit, replace_gate_cell, replacement_area_and_power
from src.config import Config
from src.experiments import (
    DEFAULT_EXPERIMENTS,
    OBSOLETE_ARTIFACT_FILENAMES,
    Experiments,
)
from src.logical_effort import analyze_path_logical_effort
from src.netlist import NetListParser
from src.optimization_heuristics import (
    OptimizationHeuristic,
    criticality_effort_gap_scores,
)
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
    nets, gates, gate_cells = NetListParser(
        directory / "netlist.txt",
        library,
    ).parse()
    return Circuit(nets, gates, gate_cells, config, library)


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
        net_name = gate.output
        assert float(row["cload_fF"]) == circuit.fanout_capacitances[gate.name]
        assert float(row["at_rise_ns"]) == result.arrival_times[net_name].rise
        assert float(row["rt_fall_ns"]) == result.required_times[net_name].fall
        assert float(row["node_slack_ns"]) == result.node_slack(net_name)

    with (tmp_path / "fanout_capacitances.csv").open(newline="") as file:
        fanout_rows = list(csv.DictReader(file))
    with (tmp_path / "gate_delays.csv").open(newline="") as file:
        delay_rows = list(csv.DictReader(file))
    with (tmp_path / "timing_summary.csv").open(newline="") as file:
        summary_rows = list(csv.DictReader(file))
    assert [row["gate"] for row in fanout_rows] == ["G1", "G2", "G3"]
    assert [row["gate"] for row in delay_rows] == ["G1", "G2", "G3"]
    assert len(summary_rows) == 1
    assert float(summary_rows[0]["wns_ns"]) == result.wns
    assert int(summary_rows[0]["reported_path_count"]) == len(
        result.critical_paths
    )


def test_logical_effort_report_identities_and_branching(tmp_path: Path) -> None:
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
                cell.family == circuit.cell_for(stage.step.gate).family
                for cell in stage.candidates
            )

    Experiments(
        tmp_path,
        ("timing_analysis", "logical_effort_analysis"),
        CIRCUITS / "valid" / "c03_fanout_branch" / "netlist.txt",
    ).run(circuit)
    with (tmp_path / "logical_effort_paths.csv").open(newline="") as file:
        path_rows = list(csv.DictReader(file))
    with (tmp_path / "logical_effort_stages.csv").open(newline="") as file:
        stage_rows = list(csv.DictReader(file))
    with (tmp_path / "logical_effort_candidates.csv").open(newline="") as file:
        candidate_rows = list(csv.DictReader(file))

    assert len(path_rows) == len(analyses)
    assert len(stage_rows) == sum(len(item.stages) for item in analyses)
    for row in path_rows:
        assert float(row["F_total_effort"]) == pytest.approx(
            float(row["G_path_logical_effort"])
            * float(row["B_branching_effort"])
            * float(row["H_electrical_effort"])
        )
    for row in candidate_rows:
        gate = circuit.gates[row["gate"]]
        candidate = circuit.cell_library.find(row["candidate_cell"])
        assert candidate.family == gate.cell_family


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


def test_replacement_area_and_power_deltas_match_full_circuit() -> None:
    circuit = load_circuit("c03_fanout_branch")

    for gate in circuit.gates.values():
        current_cell = circuit.cell_for(gate)
        for replacement in circuit.cell_library.variants(current_cell.family):
            expected_area, expected_power = replacement_area_and_power(
                circuit,
                gate.name,
                replacement,
            )
            candidate = replace_gate_cell(circuit, gate.name, replacement)
            assignments = dict(circuit.gate_cells)
            assignments[gate.name] = replacement
            rebuilt = Circuit(
                circuit.netlist,
                circuit.gates,
                assignments,
                circuit.config,
                circuit.cell_library,
            )
            assert expected_area == pytest.approx(candidate.area, abs=1.0e-12)
            assert expected_power == pytest.approx(candidate.power, abs=1.0e-12)
            assert candidate.area == pytest.approx(rebuilt.area, abs=1.0e-12)
            assert candidate.leakage_power == pytest.approx(
                rebuilt.leakage_power, abs=1.0e-12
            )
            assert candidate.dynamic_power == pytest.approx(
                rebuilt.dynamic_power, abs=1.0e-12
            )
            assert candidate.fanout_capacitances == pytest.approx(
                rebuilt.fanout_capacitances, abs=1.0e-12
            )
            for gate_name, delay in rebuilt.gate_delays.items():
                assert candidate.gate_delays[gate_name].rise == pytest.approx(
                    delay.rise, abs=1.0e-12
                )
                assert candidate.gate_delays[gate_name].fall == pytest.approx(
                    delay.fall, abs=1.0e-12
                )


def test_topology_is_frozen_and_shared_by_sizing_variants() -> None:
    circuit = load_circuit("c03_fanout_branch")
    gate = next(iter(circuit.gates.values()))
    net = circuit.output_net(gate)
    replacement = next(
        cell
        for cell in circuit.cell_library.variants(circuit.cell_for(gate).family)
        if cell is not circuit.cell_for(gate)
    )

    with pytest.raises(FrozenInstanceError):
        setattr(gate, "name", "changed")
    with pytest.raises(FrozenInstanceError):
        setattr(net, "name", "changed")
    with pytest.raises(FrozenInstanceError):
        setattr(circuit.topology, "gates", {})

    variant = replace_gate_cell(circuit, gate.name, replacement)
    assert variant.topology is circuit.topology
    assert variant.netlist is circuit.netlist
    assert variant.gates is circuit.gates
    assert variant.topological_order is circuit.topological_order
    assert variant.cell_for(gate) is replacement
    assert circuit.cell_for(gate) is not replacement


def test_optimizer_prefilters_infeasible_replacements_before_copying() -> None:
    circuit = load_circuit("c13_large_reconvergent_network")

    with patch(
        "src.optimizer.replace_gate_cell",
        wraps=replace_gate_cell,
    ) as copied:
        result = CircuitOptimizer(
            circuit,
            OptimizationHeuristic.SLACK_WEIGHTED_CAPACITANCE,
        ).optimize()

    attempted_replacements = sum(
        entry.changed_gate is not None for entry in result.history
    )
    assert copied.call_count < attempted_replacements
    assert result.timing.wns >= 0.0


def test_criticality_effort_gap_uses_signed_stage_overshoot() -> None:
    circuit = load_circuit("c03_fanout_branch")
    timing = analyze_timing(circuit)
    scores = criticality_effort_gap_scores(circuit, timing.critical_paths)
    expected: dict[str, float] = {}
    minimum_slack = min(path.slack for path in timing.critical_paths)
    maximum_slack = max(path.slack for path in timing.critical_paths)
    slack_range = maximum_slack - minimum_slack

    for path in timing.critical_paths:
        urgency = 1.0
        if slack_range > 0.0:
            urgency += (maximum_slack - path.slack) / slack_range
        analysis = analyze_path_logical_effort(circuit, path.path)
        for stage in analysis.stages:
            score = urgency * (
                stage.stage_effort - analysis.optimal_stage_effort
            )
            gate_name = stage.step.gate.name
            expected[gate_name] = max(expected.get(gate_name, -float("inf")), score)

    assert scores == pytest.approx(expected)


def test_ranked_one_shot_choices_repair_c13_with_pi_connected_gates() -> None:
    circuit = load_circuit("c13_large_reconvergent_network")

    result = CircuitOptimizer(
        circuit,
        OptimizationHeuristic.SLACK_WEIGHTED_CAPACITANCE,
    ).optimize()

    assert result.circuit.cell_for("G7").name == "NOR3_X2"
    assert result.circuit.cell_for("G6").name == "NAND3_X2"
    assert result.timing.wns >= 0.0
    assert result.circuit.area <= circuit.config.design_constraints.maximum_area
    assert (
        result.circuit.power
        <= circuit.config.design_constraints.maximum_power_uW
    )
    assert result.sta_calls <= result.total_iterations + 1
    assert result.total_iterations == sum(
        entry.changed_gate is not None for entry in result.history
    )

    choices_since_acceptance: set[tuple[str, str]] = set()
    for entry in result.history:
        if entry.changed_gate is None or entry.new_cell is None:
            continue
        choice = (entry.changed_gate, entry.new_cell)
        assert choice not in choices_since_acceptance
        choices_since_acceptance.add(choice)
        if entry.accepted:
            choices_since_acceptance.clear()


def test_random_greedy_can_resize_pi_connected_gates() -> None:
    circuit = load_circuit("c13_large_reconvergent_network")

    result = CircuitOptimizer(
        circuit,
        OptimizationHeuristic.RANDOM_GREEDY,
        random_seed=0,
    ).optimize()

    assert any(
        entry.changed_gate == "G7" and entry.new_cell == "NOR3_X2"
        for entry in result.history
    )
    assert result.sta_calls <= result.total_iterations + 1
    assert result.total_iterations == sum(
        entry.changed_gate is not None for entry in result.history
    )

    choices_since_acceptance: set[tuple[str, str]] = set()
    for entry in result.history:
        if entry.changed_gate is None or entry.new_cell is None:
            continue
        choice = (entry.changed_gate, entry.new_cell)
        assert choice not in choices_since_acceptance
        choices_since_acceptance.add(choice)
        if entry.accepted:
            choices_since_acceptance.clear()


def test_complete_core_artifact_manifest_and_summary(tmp_path: Path) -> None:
    circuit = load_circuit("c01_inverter_chain")
    netlist = CIRCUITS / "valid" / "c01_inverter_chain" / "netlist.txt"
    Experiments(tmp_path, DEFAULT_EXPERIMENTS, netlist).run(circuit)
    required = {
        "fanout_capacitances.csv",
        "gate_delays.csv",
        "timing_summary.csv",
        "timing_analysis.csv",
        "critical_paths.csv",
        "logical_effort_paths.csv",
        "logical_effort_stages.csv",
        "logical_effort_candidates.csv",
        "optimization_slack_weighted_capacitance.csv",
        "optimization_criticality_effort_gap.csv",
        "optimization_random_greedy.csv",
        "optimization_summary.csv",
        "optimization_comparison.csv",
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
    assert set(summary["optimizers"]) == {
        "logical_effort_guided",
        "criticality_effort_gap",
        "greedy_baseline",
    }
    with (tmp_path / "optimization_summary.csv").open(newline="") as file:
        optimizer_rows = list(csv.DictReader(file))
    assert {row["method"] for row in optimizer_rows} == {
        "slack_weighted_capacitance",
        "criticality_effort_gap",
        "random_greedy",
    }
    with (tmp_path / "optimization_comparison.csv").open(newline="") as file:
        comparison_rows = list(csv.DictReader(file))
    assert [row["state"] for row in comparison_rows] == [
        "pre_optimization",
        "post_optimization",
        "post_optimization",
        "post_optimization",
    ]
    canonical_row = next(
        row
        for row in comparison_rows
        if row["method"] == "slack_weighted_capacitance"
    )
    assert float(canonical_row["wns_delta_from_canonical_ns"]) == 0.0
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


def test_runner_plot_flag_uses_current_optimization_reports(
    tmp_path: Path,
) -> None:
    output = tmp_path / "plotted-circuit"
    output.mkdir()
    for filename in OBSOLETE_ARTIFACT_FILENAMES:
        (output / filename).write_text("stale", encoding="utf-8")
    completed = subprocess.run(
        (
            "bash",
            str(ROOT / "run_circuit.sh"),
            "c01_inverter_chain",
            "--output-dir",
            str(output),
            "--plot-optimization",
        ),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert not any(
        (output / filename).exists()
        for filename in OBSOLETE_ARTIFACT_FILENAMES
    )
    for filename in (
        "optimization_convergence.png",
        "optimization_outcomes.png",
    ):
        assert (output / filename).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


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
        summary = state["summary"]
        assert summary["wns"] == timing.wns
        assert summary["tns"] == timing.tns
        assert summary["circuit_delay"] == timing.circuit_delay
        assert summary["area"] == circuit.area
        assert summary["power"] == circuit.power
        assert summary["leakage_power"] == circuit.leakage_power
        assert summary["dynamic_power"] == circuit.dynamic_power
        assert summary["all_constraints_compliant"] == all(
            (
                summary["timing_compliant"],
                summary["area_compliant"],
                summary["power_compliant"],
            )
        )
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
