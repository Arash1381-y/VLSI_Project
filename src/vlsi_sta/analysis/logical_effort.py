"""Logical-effort path estimation and gate-sizing candidate selection."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from vlsi_sta.domain.cell import Cell
from vlsi_sta.domain.circuit import Circuit
from vlsi_sta.input.netlist import CircuitPath, Gate, NetlistError, PathStep
from vlsi_sta.analysis.sta import CriticalPath


OptimizationChoice = tuple[Gate, tuple[Cell, ...]]


@dataclass(frozen=True)
class LogicalEffortStage:
    step: PathStep
    logical_effort: float
    branching_effort: float
    electrical_effort: float
    stage_effort: float
    parasitic_delay: float
    input_capacitance: float
    total_output_load: float
    on_path_load: float
    target_input_capacitance: float
    target_size_ratio: float
    candidates: tuple[Cell, ...]


@dataclass(frozen=True)
class LogicalEffortPathAnalysis:
    path: CircuitPath
    path_logical_effort: float
    branching_effort: float
    electrical_effort: float
    total_effort: float
    parasitic_delay: float
    optimal_stage_effort: float
    minimum_normalized_delay: float
    minimum_delay: float
    stages: tuple[LogicalEffortStage, ...]


def compute_path_delay(circuit: Circuit, path: CircuitPath) -> float:
    """Estimate one path's delay using logical effort."""

    _validate_circuit_path(circuit, path)
    tau = circuit.cell_library.logical_effort_tau
    normalized_delay = 0.0
    for step in path.steps:
        gate = step.gate
        cell = circuit.cell_for(gate)
        input_pin = cell.input_pins[step.input_pin]
        if input_pin.capacitance <= 0.0:
            raise NetlistError(
                f"cell {cell.name!r} input pin {input_pin.name!r} "
                "has non-positive capacitance"
            )

        total_output_load = circuit.fanout_capacitances[gate.name]
        electrical_effort = total_output_load / input_pin.capacitance
        normalized_delay += (
            input_pin.logical_effort * electrical_effort
            + cell.parasitic_delay
        )

    return tau * normalized_delay


def get_optimization_candidates(
    circuit: Circuit,
    paths: Sequence[CriticalPath],
) -> list[OptimizationChoice]:
    """Return equivalent library-cell choices for gates on timing paths."""

    candidates_by_gate: dict[str, tuple[Gate, dict[str, Cell]]] = {}
    for critical_path in paths:
        for stage in analyze_path_logical_effort(circuit, critical_path.path).stages:
            step = stage.step
            gate, candidates = candidates_by_gate.setdefault(
                step.gate.name,
                (step.gate, {}),
            )
            for cell in stage.candidates:
                candidates[cell.name] = cell

    return [
        (
            gate,
            tuple(
                sorted(
                    candidates.values(),
                    key=lambda cell: cell.size_factor,
                )
            ),
        )
        for gate, candidates in candidates_by_gate.values()
    ]


def logical_effort_targets(
    circuit: Circuit,
    path: CircuitPath,
) -> list[tuple[PathStep, float]]:
    """Return per-stage input targets for the circuit's current loads."""

    analysis = analyze_path_logical_effort(circuit, path)
    return [
        (stage.step, stage.target_input_capacitance)
        for stage in analysis.stages
    ]


def analyze_path_logical_effort(
    circuit: Circuit,
    path: CircuitPath,
) -> LogicalEffortPathAnalysis:
    """Return complete logical-effort calculations for one circuit path."""

    _validate_circuit_path(circuit, path)
    if not path.steps:
        raise NetlistError("cannot size an empty circuit path")

    endpoint_load = circuit.config.output_load(path.output_net.name)
    if endpoint_load <= 0.0:
        raise NetlistError(
            f"output {path.output_net.name!r} must have a positive load "
            "for logical-effort sizing"
        )

    stage_terms = [
        (
            step,
            _stage_logical_effort(circuit, step),
            _stage_branching_effort(circuit, path, index, endpoint_load),
            _on_path_load(circuit, path, index, endpoint_load),
        )
        for index, step in enumerate(path.steps)
    ]
    first_step = path.steps[0]
    first_input_capacitance = circuit.cell_for(first_step.gate).input_pins[
        first_step.input_pin
    ].capacitance

    path_logical_effort = math.prod(term[1] for term in stage_terms)
    branching_effort = math.prod(term[2] for term in stage_terms)
    electrical_effort = endpoint_load / first_input_capacitance
    path_effort = path_logical_effort * branching_effort * electrical_effort
    optimal_effort = path_effort ** (1.0 / len(path.steps))
    parasitic_delay = sum(
        circuit.cell_for(step.gate).parasitic_delay for step in path.steps
    )
    minimum_normalized_delay = len(path.steps) * optimal_effort + parasitic_delay
    stages: list[LogicalEffortStage] = []
    for step, logical_effort, branch, on_path_load in stage_terms:
        cell = circuit.cell_for(step.gate)
        input_capacitance = cell.input_pins[step.input_pin].capacitance
        total_load = circuit.fanout_capacitances[step.gate.name]
        electrical_effort_stage = on_path_load / input_capacitance
        target_capacitance = logical_effort * total_load / optimal_effort
        candidates = circuit.cell_library.sizing_candidates(
            cell.family,
            step.input_pin,
            target_capacitance,
        )
        stages.append(
            LogicalEffortStage(
                step=step,
                logical_effort=logical_effort,
                branching_effort=branch,
                electrical_effort=electrical_effort_stage,
                stage_effort=(
                    logical_effort * branch * electrical_effort_stage
                ),
                parasitic_delay=cell.parasitic_delay,
                input_capacitance=input_capacitance,
                total_output_load=total_load,
                on_path_load=on_path_load,
                target_input_capacitance=target_capacitance,
                target_size_ratio=target_capacitance / input_capacitance,
                candidates=candidates,
            )
        )

    return LogicalEffortPathAnalysis(
        path=path,
        path_logical_effort=path_logical_effort,
        branching_effort=branching_effort,
        electrical_effort=electrical_effort,
        total_effort=path_effort,
        parasitic_delay=parasitic_delay,
        optimal_stage_effort=optimal_effort,
        minimum_normalized_delay=minimum_normalized_delay,
        minimum_delay=circuit.cell_library.logical_effort_tau
        * minimum_normalized_delay,
        stages=tuple(stages),
    )


def _stage_logical_effort(circuit: Circuit, step: PathStep) -> float:
    cell = circuit.cell_for(step.gate)
    input_pin = cell.input_pins[step.input_pin]
    if input_pin.capacitance <= 0.0 or input_pin.logical_effort <= 0.0:
        raise NetlistError(
            f"cell {cell.name!r} input pin {input_pin.name!r} "
            "must have positive capacitance and logical effort"
        )
    return input_pin.logical_effort


def _stage_branching_effort(
    circuit: Circuit,
    path: CircuitPath,
    stage_index: int,
    endpoint_load: float,
) -> float:
    step = path.steps[stage_index]
    on_path_load = _on_path_load(circuit, path, stage_index, endpoint_load)
    total_load = circuit.fanout_capacitances[step.gate.name]
    if on_path_load <= 0.0 or total_load <= 0.0:
        raise NetlistError(
            f"gate {step.gate.name!r} must drive a positive load "
            "for logical-effort sizing"
        )
    return total_load / on_path_load


def _on_path_load(
    circuit: Circuit,
    path: CircuitPath,
    stage_index: int,
    endpoint_load: float,
) -> float:
    if stage_index == len(path.steps) - 1:
        return endpoint_load
    next_step = path.steps[stage_index + 1]
    return circuit.cell_for(next_step.gate).input_pins[
        next_step.input_pin
    ].capacitance


def _validate_circuit_path(circuit: Circuit, path: CircuitPath) -> None:
    if circuit.netlist.get(path.input_net.name) is not path.input_net:
        raise NetlistError("circuit path input does not belong to this circuit")
    if circuit.netlist.get(path.output_net.name) is not path.output_net:
        raise NetlistError("circuit path output does not belong to this circuit")

    current_net = path.input_net
    for step in path.steps:
        if circuit.gates.get(step.gate.name) is not step.gate:
            raise NetlistError(
                f"gate {step.gate.name!r} does not belong to this circuit"
            )
        if not 0 <= step.input_pin < len(step.gate.inputs):
            raise NetlistError(
                f"gate {step.gate.name!r} has no input pin {step.input_pin}"
            )
        if step.gate.inputs[step.input_pin] != current_net.name:
            raise NetlistError("circuit path contains disconnected steps")
        current_net = circuit.netlist[step.gate.output]
    if current_net is not path.output_net:
        raise NetlistError("circuit path does not reach its declared output")
