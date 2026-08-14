from __future__ import annotations

from pathlib import Path

import pytest

from src.cell import CellLibrary
from src.circuit import Circuit
from src.config import Config
from src.netlist import NetListParser
from src.optimization_heuristics import OptimizationHeuristic
from src.optimizer import CircuitOptimizer, OptimizationResult


ROOT = Path(__file__).resolve().parent.parent
VALID_CIRCUITS = ROOT / "Input_Files" / "circuits" / "valid"
VALID_CIRCUIT_NAMES = tuple(
    path.name for path in sorted(VALID_CIRCUITS.iterdir()) if path.is_dir()
)
COMPARISON_TOLERANCE = 1.0e-12


def load_circuit(name: str) -> Circuit:
    directory = VALID_CIRCUITS / name
    config = Config(directory / "config.json")
    library = CellLibrary(config.cell_library_path)
    nets, gates = NetListParser(directory / "netlist.txt", library).parse()
    return Circuit(nets, gates, config, library)


@pytest.mark.parametrize("circuit_name", VALID_CIRCUIT_NAMES)
def test_slack_weighted_is_not_worse_than_brute_force(
    circuit_name: str,
) -> None:
    circuit = load_circuit(circuit_name)
    weighted = CircuitOptimizer(
        circuit,
        OptimizationHeuristic.SLACK_WEIGHTED_CAPACITANCE,
    ).optimize()
    brute_force = CircuitOptimizer(
        circuit,
        OptimizationHeuristic.BRUTE_FORCE,
    ).optimize()

    _assert_constraints_hold(weighted)
    _assert_not_worse(weighted, brute_force)


def _assert_constraints_hold(result: OptimizationResult) -> None:
    constraints = result.circuit.config.design_constraints
    assert result.circuit.area <= constraints.maximum_area
    assert result.circuit.power <= constraints.maximum_power_uW


def _assert_not_worse(
    weighted: OptimizationResult,
    brute_force: OptimizationResult,
) -> None:
    weighted_wns = weighted.timing.wns
    brute_force_wns = brute_force.timing.wns

    if brute_force_wns >= -COMPARISON_TOLERANCE:
        assert weighted_wns >= -COMPARISON_TOLERANCE
        assert weighted.cost <= brute_force.cost + COMPARISON_TOLERANCE
        return

    assert weighted_wns >= brute_force_wns - COMPARISON_TOLERANCE
    if weighted_wns > brute_force_wns + COMPARISON_TOLERANCE:
        return

    assert (
        weighted.timing.tns
        >= brute_force.timing.tns - COMPARISON_TOLERANCE
    )
    if (
        weighted.timing.tns
        > brute_force.timing.tns + COMPARISON_TOLERANCE
    ):
        return

    assert weighted.cost <= brute_force.cost + COMPARISON_TOLERANCE
