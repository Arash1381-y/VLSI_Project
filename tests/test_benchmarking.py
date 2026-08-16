from __future__ import annotations

import csv
import json
import logging
from pathlib import Path

import pytest

from src.benchmarking import BenchmarkConfig, BenchmarkConfigError
from src.benchmarking.evaluation import evaluate_suite
from src.benchmarking.generation import generate_suite
from src.cell import CellLibrary
from src.circuit import Circuit
from src.config import Config
from src.netlist import NetListParser, NetType
from src.sta import analyze_timing


PROJECT = Path(__file__).resolve().parents[1]
LIBRARY = PROJECT / "Input_Files" / "cell_library.json"


def _document(output_root: Path, suite: str = "repair_suite") -> dict[str, object]:
    return {
        "schema_version": 1,
        "suite": {
            "name": suite,
            "output_root": str(output_root),
            "random_seed": 1405,
            "maximum_attempts_per_case": 20,
        },
        "cell_library": str(LIBRARY),
        "sources": {
            "generated": {
                "case_count": 1,
                "topology": {
                    "gate_count": [6, 6],
                    "logic_levels": [3, 3],
                    "primary_inputs": [2, 2],
                    "primary_outputs": [1, 1],
                    "maximum_fanout": 6,
                    "reconvergence_probability": 0.35,
                    "branch_probability": 0.4,
                    "family_weights": {
                        "INV": 1.0,
                        "NAND2": 2.0,
                        "NOR2": 2.0,
                    },
                },
            }
        },
        "electrical": {
            "input_arrival_rise_ns": [0.0, 0.0],
            "input_arrival_fall_ns": [0.0, 0.0],
            "output_load_fF": [4.0, 4.0],
            "required_time_margin_ns": [0.001, 0.01],
            "supply_voltage": 1.0,
            "frequency_hz": 100000000,
            "temperature_c": 25.0,
            "default_activity_factor": 0.1,
            "activity_override_probability": 0.0,
            "activity_override_range": [0.05, 0.3],
        },
        "reference_search": {
            "algorithm": "multi_start_beam",
            "allowed_sizes": ["X1", "X2", "X4"],
            "beam_width": 12,
            "restarts": 4,
            "maximum_expansions": 300,
            "stagnation_limit": 100,
            "provisional_area_headroom": 0.5,
            "provisional_power_headroom": 0.5,
            "final_area_headroom": [0.05, 0.1],
            "final_power_headroom": [0.05, 0.1],
        },
        "perturbation": {
            "direction": "downsize_only",
            "changed_gate_count": [1, 3],
            "steps_per_gate": [1, 2],
            "target_wns_ns": [-1.0, -0.000001],
            "require_reference_path_gate": True,
            "require_critical_path_change": False,
            "maximum_attempts": 200,
        },
        "analyzer": {
            "top_k_paths": 5,
            "separate_rise_fall": True,
            "maximum_iterations": 20,
            "minimum_cost_improvement": 0.000001,
            "weights": {
                "delay": 1.0,
                "power": 0.2,
                "area": 0.2,
                "timing_violation": 5.0,
            },
        },
        "evaluation": {
            "heuristics": ["brute_force", "random_greedy"],
            "random_greedy_repetitions": 2,
            "candidate_scope": "all_allowed_variants",
            "maximum_seconds_per_run": 10,
        },
    }


def _write_config(path: Path, document: dict[str, object]) -> BenchmarkConfig:
    path.write_text(json.dumps(document), encoding="utf-8")
    return BenchmarkConfig.load(path)


def _load_case(case_directory: Path) -> Circuit:
    config = Config(case_directory / "config.json")
    library = CellLibrary(config.cell_library_path)
    nets, gates, cells = NetListParser(
        case_directory / "netlist.txt", library
    ).parse()
    return Circuit(nets, gates, cells, config, library)


def test_benchmark_config_is_strict_and_frozen(tmp_path: Path) -> None:
    document = _document(tmp_path / "out")
    document["unexpected"] = True
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(BenchmarkConfigError, match="unknown fields: unexpected"):
        BenchmarkConfig.load(path)

    document.pop("unexpected")
    reference = document["reference_search"]
    assert isinstance(reference, dict)
    reference["restarts"] = 3
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(BenchmarkConfigError, match="restarts must be at least 4"):
        BenchmarkConfig.load(path)


def test_generation_is_replayable_valid_and_deterministic(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="src.benchmarking.generation")
    first = generate_suite(
        _write_config(
            tmp_path / "first.json",
            _document(tmp_path / "first-output"),
        )
    )
    second = generate_suite(
        _write_config(
            tmp_path / "second.json",
            _document(tmp_path / "second-output"),
        )
    )
    first_case = first.generated_cases[0].directory
    second_case = second.generated_cases[0].directory
    for filename in (
        "netlist.txt", "config.json", "reference_assignment.json",
        "planted_mutations.csv",
    ):
        assert (first_case / filename).read_bytes() == (second_case / filename).read_bytes()

    circuit = _load_case(first_case)
    timing = analyze_timing(circuit)
    constraints = circuit.config.design_constraints
    assert timing.wns < 0.0
    assert circuit.area <= constraints.maximum_area
    assert circuit.power <= constraints.maximum_power_uW

    assignment = json.loads(
        (first_case / "reference_assignment.json").read_text(encoding="utf-8")
    )
    reference = circuit
    for gate, cell_name in assignment.items():
        reference = reference.with_gate_cell(gate, circuit.cell_library[cell_name])
    reference_timing = analyze_timing(reference)
    assert reference_timing.wns >= 0.0
    assert reference.area <= constraints.maximum_area
    assert reference.power <= constraints.maximum_power_uW

    fanouts: dict[str, int] = {}
    for gate in circuit.gates.values():
        for input_net in gate.inputs:
            fanouts[input_net] = fanouts.get(input_net, 0) + 1
    assert max(fanouts.values()) <= 6
    assert all(
        net.loads or net.net_type is NetType.OUTPUT
        for net in circuit.netlist.values()
        if net.driver is not None
    )
    assert "Case 1/1 started" in caplog.text
    assert "Case 1/1 completed" in caplog.text
    assert "Benchmark suite generation completed" in caplog.text


def test_evaluation_writes_oracle_and_replays_optimizer_history(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="src.benchmarking.evaluation")
    document = _document(tmp_path / "output")
    sources = document["sources"]
    assert isinstance(sources, dict)
    generated = sources["generated"]
    assert isinstance(generated, dict)
    generated["case_count"] = 2
    evaluation = document["evaluation"]
    assert isinstance(evaluation, dict)
    evaluation["parallel_workers"] = 2
    suite = generate_suite(
        _write_config(tmp_path / "benchmark.json", document)
    ).suite_directory
    result = evaluate_suite(suite)
    assert result.case_run_count == 6
    expected = {
        "case_runs.csv", "optimizer_iterations.csv", "oracle_candidates.csv",
        "oracle_states.csv", "gate_selection_scores.csv", "heuristic_summary.csv",
        "evaluation_summary.json",
    }
    assert expected == {path.name for path in result.evaluation_directory.iterdir()}

    with (result.evaluation_directory / "case_runs.csv").open(
        encoding="utf-8", newline=""
    ) as source:
        runs = list(csv.DictReader(source))
    assert len(runs) == 6
    assert {row["heuristic"] for row in runs} == {"brute_force", "random_greedy"}
    assert all(row["timed_out"] == "False" for row in runs)

    with (result.evaluation_directory / "oracle_candidates.csv").open(
        encoding="utf-8", newline=""
    ) as source:
        candidates = list(csv.DictReader(source))
    with (result.evaluation_directory / "oracle_states.csv").open(
        encoding="utf-8", newline=""
    ) as source:
        oracle_states = list(csv.DictReader(source))
    with (result.evaluation_directory / "gate_selection_scores.csv").open(
        encoding="utf-8", newline=""
    ) as source:
        selection_scores = list(csv.DictReader(source))
    state_ids = {row["oracle_state_id"] for row in oracle_states}
    assert len(state_ids) == len(oracle_states)
    assert {row["oracle_state_id"] for row in candidates} <= state_ids
    assert {row["oracle_state_id"] for row in selection_scores} <= state_ids
    assert len(candidates) == len({
        (row["oracle_state_id"], row["gate"], row["cell"])
        for row in candidates
    })
    circuit = _load_case(suite / "cases" / "case_0001")
    replaceable_gates = {
        gate.name
        for gate in circuit.gates.values()
        if len(circuit.cell_library.variants(circuit.cell_for(gate).family)) > 1
    }
    assert replaceable_gates <= {row["gate"] for row in candidates}

    summary = json.loads(
        (result.evaluation_directory / "evaluation_summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert set(summary["groupings"]) == {
        "source_type", "gate_count", "depth", "maximum_fanout",
        "reconvergence", "violation_severity", "constraint_headroom",
    }
    assert "Case 1/2 started" in caplog.text
    assert "Case 2/2 completed" in caplog.text
    assert "Benchmark evaluation completed" in caplog.text


def test_seeded_generation_preserves_topology_and_families(tmp_path: Path) -> None:
    document = _document(tmp_path / "output", suite="seeded_suite")
    document["sources"] = {
        "seeded": [{
            "directory": str(
                PROJECT
                / "Input_Files"
                / "circuits"
                / "valid"
                / "c06_deep_critical_path"
            ),
            "case_count": 1,
        }]
    }
    generated = generate_suite(
        _write_config(tmp_path / "seeded.json", document)
    ).generated_cases[0].directory

    seed_library = CellLibrary(LIBRARY)
    _, seed_gates, _ = NetListParser(
        PROJECT
        / "Input_Files"
        / "circuits"
        / "valid"
        / "c06_deep_critical_path"
        / "netlist.txt",
        seed_library,
    ).parse()
    emitted = _load_case(generated)
    assert tuple(emitted.gates) == tuple(seed_gates)
    for name, gate in emitted.gates.items():
        seed_gate = seed_gates[name]
        assert (gate.inputs, gate.output, gate.cell_family) == (
            seed_gate.inputs,
            seed_gate.output,
            seed_gate.cell_family,
        )
    assert analyze_timing(emitted).wns < 0.0
