"""Critical-path gate-selection strategies for sizing optimization."""

from __future__ import annotations

import math
from collections.abc import Iterator, Sequence
from enum import Enum

from .cell import Cell
from .circuit import Circuit
from .logical_effort import (
    OptimizationChoice,
    analyze_path_logical_effort,
    logical_effort_targets,
)
from .netlist import Gate
from .sta import CriticalPath


class OptimizationHeuristic(str, Enum):
    """Available strategies for selecting critical-path gates."""

    BRUTE_FORCE = "brute_force"
    SLACK_WEIGHTED_CAPACITANCE = "slack_weighted_capacitance"
    CRITICALITY_EFFORT_GAP = "criticality_effort_gap"
    RANDOM_GREEDY = "random_greedy"


def iter_ranked_optimization_choices(
    circuit: Circuit,
    paths: Sequence[CriticalPath],
    choices: Sequence[OptimizationChoice],
) -> Iterator[tuple[Gate, Cell]]:
    """Yield every exact sizing choice once in heuristic rank order."""

    scores = _slack_weighted_capacitance_scores(circuit, paths)
    gate_position = {
        gate_name: position
        for position, gate_name in enumerate(circuit.gates)
    }
    ordered = sorted(
        choices,
        key=lambda choice: (
            -scores.get(choice[0].name, 0.0),
            gate_position[choice[0].name],
        ),
    )
    for gate, candidate_cells in ordered:
        for candidate_cell in candidate_cells:
            yield gate, candidate_cell


def iter_criticality_effort_gap_choices(
    circuit: Circuit,
    paths: Sequence[CriticalPath],
    choices: Sequence[tuple[Gate, Cell]],
) -> Iterator[tuple[Gate, Cell]]:
    """Yield one-step choices by path urgency and stage-effort overshoot."""

    scores = criticality_effort_gap_scores(circuit, paths)
    gate_position = {
        gate_name: position
        for position, gate_name in enumerate(circuit.gates)
    }
    yield from sorted(
        choices,
        key=lambda choice: (
            -scores.get(choice[0].name, -math.inf),
            gate_position[choice[0].name],
        ),
    )


def criticality_effort_gap_scores(
    circuit: Circuit,
    paths: Sequence[CriticalPath],
) -> dict[str, float]:
    """Score gates using urgency * (current stage effort - optimal effort)."""

    if not paths:
        return {}

    minimum_slack = min(path.slack for path in paths)
    maximum_slack = max(path.slack for path in paths)
    slack_range = maximum_slack - minimum_slack
    scores: dict[str, float] = {}
    for critical_path in paths:
        urgency = 1.0
        if slack_range > 0.0:
            urgency += (maximum_slack - critical_path.slack) / slack_range

        analysis = analyze_path_logical_effort(
            circuit,
            critical_path.path,
        )
        for stage in analysis.stages:
            effort_gap = stage.stage_effort - analysis.optimal_stage_effort
            score = urgency * effort_gap
            gate_name = stage.step.gate.name
            scores[gate_name] = max(scores.get(gate_name, -math.inf), score)
    return scores


def _slack_weighted_capacitance_scores(
    circuit: Circuit,
    paths: Sequence[CriticalPath],
) -> dict[str, float]:
    """Score gates by target-capacitance mismatch and endpoint urgency."""

    if not paths:
        return {}

    minimum_slack = min(path.slack for path in paths)
    maximum_slack = max(path.slack for path in paths)
    slack_range = maximum_slack - minimum_slack
    scores: dict[str, float] = {}

    for critical_path in paths:
        urgency = 1.0
        if slack_range > 0.0:
            urgency += (maximum_slack - critical_path.slack) / slack_range

        for step, target_capacitance in logical_effort_targets(
            circuit,
            critical_path.path,
        ):
            current_capacitance = circuit.cell_for(step.gate).input_pins[
                step.input_pin
            ].capacitance
            mismatch = abs(math.log(target_capacitance / current_capacitance))
            scores[step.gate.name] = (
                scores.get(step.gate.name, 0.0) + urgency * mismatch
            )

    return scores
