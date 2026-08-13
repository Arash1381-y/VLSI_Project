"""Critical-path gate-selection strategies for sizing optimization."""

from __future__ import annotations

import math
from collections.abc import Sequence
from enum import Enum

from .circuit import Circuit
from .logical_effort import (
    OptimizationChoice,
    logical_effort_targets,
)
from .sta import CriticalPath


class OptimizationHeuristic(str, Enum):
    """Available strategies for selecting critical-path gates."""

    BRUTE_FORCE = "brute_force"
    SLACK_WEIGHTED_CAPACITANCE = "slack_weighted_capacitance"
    RANDOM_GREEDY = "random_greedy"


def select_optimization_choices(
    circuit: Circuit,
    paths: Sequence[CriticalPath],
    choices: Sequence[OptimizationChoice],
    heuristic: OptimizationHeuristic,
    gate_limit: int,
) -> list[OptimizationChoice]:
    """Select eligible critical-path gate choices using one strategy."""

    if heuristic is OptimizationHeuristic.BRUTE_FORCE:
        return list(choices)
    if heuristic is OptimizationHeuristic.SLACK_WEIGHTED_CAPACITANCE:
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
        return ordered[:gate_limit]
    raise ValueError(f"unsupported optimization heuristic: {heuristic!r}")


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
            current_capacitance = step.gate.cell.input_pins[
                step.input_pin
            ].capacitance
            mismatch = abs(math.log(target_capacitance / current_capacitance))
            scores[step.gate.name] = (
                scores.get(step.gate.name, 0.0) + urgency * mismatch
            )

    return scores
