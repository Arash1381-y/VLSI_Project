"""Pure conversions from analysis domain objects to report records."""

from __future__ import annotations

from dataclasses import dataclass

from vlsi_sta.domain.circuit import Circuit
from vlsi_sta.optimization.optimizer import OptimizationResult
from vlsi_sta.analysis.sta import CriticalPath, TimingAnalysisResult


CANONICAL_OPTIMIZATION = "slack_weighted_capacitance"
EFFORT_GAP_OPTIMIZATION = "criticality_effort_gap"
GREEDY_OPTIMIZATION = "random_greedy"


@dataclass(frozen=True)
class TimedOptimization:
    name: str
    result: OptimizationResult
    elapsed_seconds: float


def critical_path_record(
    rank: int,
    critical_path: CriticalPath,
) -> dict[str, object]:
    path = critical_path.path
    transitions = tuple(
        transition.value for transition in critical_path.transitions
    )
    return {
        "rank": rank,
        "slack_ns": critical_path.slack,
        "input_net": path.input_net.name,
        "output_net": path.output_net.name,
        "input_transition": transitions[0],
        "output_transition": transitions[-1],
        "gate_count": len(path.steps),
        "gates": " -> ".join(step.gate.name for step in path.steps),
        "input_pins": " -> ".join(str(step.input_pin) for step in path.steps),
        "transitions": " -> ".join(transitions),
    }


def circuit_compliance(
    circuit: Circuit,
    timing: TimingAnalysisResult,
) -> dict[str, bool]:
    constraints = circuit.config.design_constraints
    return {
        "timing_compliant": timing.wns >= 0.0,
        "power_compliant": circuit.power <= constraints.maximum_power_uW,
        "area_compliant": circuit.area <= constraints.maximum_area,
    }


def circuit_specification(
    circuit: Circuit,
    timing: TimingAnalysisResult,
    cost: float,
) -> dict[str, object]:
    return {
        "circuit_delay_ns": timing.circuit_delay,
        "wns_ns": timing.wns,
        "tns_ns": timing.tns,
        "area": circuit.area,
        "power_uW": circuit.power,
        "leakage_power_uW": circuit.leakage_power,
        "dynamic_power_uW": circuit.dynamic_power,
        "cost": cost,
        "compliance": circuit_compliance(circuit, timing),
    }


def optimization_role(name: str) -> str:
    return {
        CANONICAL_OPTIMIZATION: "logical_effort_guided",
        EFFORT_GAP_OPTIMIZATION: "criticality_effort_gap",
        GREEDY_OPTIMIZATION: "greedy_baseline",
    }[name]


def optimization_record(run: TimedOptimization) -> dict[str, object]:
    result = run.result
    return {
        "method": run.name,
        "heuristic": result.heuristic.value,
        "final": circuit_specification(result.circuit, result.timing, result.cost),
        "runtime_seconds": run.elapsed_seconds,
        "maximum_iterations": result.circuit.config.optimization.maximum_iterations,
        "total_iterations": result.total_iterations,
        "accepted_iterations": result.accepted_iterations,
        "sta_calls": result.sta_calls,
        "termination": result.termination.value,
        "random_seed": result.random_seed,
    }


def optimization_differences(
    logical: TimedOptimization,
    greedy: TimedOptimization,
) -> dict[str, float | int]:
    logical_result = logical.result
    greedy_result = greedy.result
    return {
        "circuit_delay_ns": (
            greedy_result.timing.circuit_delay
            - logical_result.timing.circuit_delay
        ),
        "wns_ns": greedy_result.timing.wns - logical_result.timing.wns,
        "tns_ns": greedy_result.timing.tns - logical_result.timing.tns,
        "power_uW": greedy_result.circuit.power - logical_result.circuit.power,
        "area": greedy_result.circuit.area - logical_result.circuit.area,
        "runtime_seconds": greedy.elapsed_seconds - logical.elapsed_seconds,
        "sta_calls": greedy_result.sta_calls - logical_result.sta_calls,
    }
