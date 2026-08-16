"""Optimization history, summary, and comparison report generation."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from vlsi_sta.domain.circuit import Circuit
from vlsi_sta.reporting.artifacts import ArtifactWriter
from vlsi_sta.optimization.optimizer import OptimizationIteration
from vlsi_sta.reporting.models import (
    CANONICAL_OPTIMIZATION,
    TimedOptimization,
    optimization_differences,
    optimization_role,
)
from vlsi_sta.analysis.sta import TimingAnalysisResult


HISTORY_HEADER = (
    "iteration", "phase", "accepted", "changed_gate", "previous_cell",
    "new_cell", "rejection_reason", "before_cost", "after_cost",
    "cost_improvement", "before_wns_ns", "after_wns_ns", "wns_delta_ns",
    "before_tns_ns", "after_tns_ns", "tns_delta_ns", "before_delay_ns",
    "after_delay_ns", "delay_delta_ns", "before_area", "after_area",
    "area_delta", "before_power_uW", "after_power_uW", "power_delta_uW",
    "termination", "elapsed_seconds",
)

SUMMARY_HEADER = (
    "method", "role", "heuristic", "random_seed", "maximum_iterations",
    "total_iterations", "accepted_iterations", "sta_calls", "elapsed_seconds",
    "termination", "timing_met", "wns_ns", "tns_ns", "circuit_delay_ns",
    "area", "power_uW", "cost",
)

COMPARISON_HEADER = (
    "state", "method", "role", "reference_method", "heuristic",
    "random_seed", "maximum_iterations", "total_iterations",
    "accepted_iterations", "sta_calls", "elapsed_seconds", "termination",
    "timing_compliant", "power_compliant", "area_compliant",
    "wns_ns", "tns_ns",
    "circuit_delay_ns", "area", "leakage_power_uW", "dynamic_power_uW",
    "power_uW", "cost", "wns_delta_from_canonical_ns",
    "tns_delta_from_canonical_ns", "delay_delta_from_canonical_ns",
    "area_delta_from_canonical", "power_delta_from_canonical_uW",
    "cost_delta_from_canonical", "runtime_delta_from_canonical_seconds",
    "sta_calls_delta_from_canonical",
)


def save_optimization_reports(
    writer: ArtifactWriter,
    circuit: Circuit,
    nominal_timing: TimingAnalysisResult,
    runs: Sequence[TimedOptimization],
) -> None:
    for run in runs:
        writer.write_csv(
            f"optimization_{run.name}",
            HISTORY_HEADER,
            _history_rows(run),
        )
    writer.write_csv(
        "optimization_summary",
        SUMMARY_HEADER,
        (_summary_row(run) for run in runs),
    )
    writer.write_csv(
        "optimization_comparison",
        COMPARISON_HEADER,
        _comparison_rows(circuit, nominal_timing, runs),
    )


def _history_rows(run: TimedOptimization) -> Iterable[tuple[object, ...]]:
    history = run.result.history
    last_index = len(history) - 1
    for index, current in enumerate(history):
        previous = _previous_accepted_state(history, index)
        yield _history_row(
            previous,
            current,
            run.result.termination.value if index == last_index else None,
            run.elapsed_seconds if index == last_index else None,
        )


def _previous_accepted_state(
    history: tuple[OptimizationIteration, ...],
    index: int,
) -> OptimizationIteration:
    current = history[index]
    if current.accepted is not True or index == 0:
        return current
    return history[index - 1]


def _history_row(
    previous: OptimizationIteration,
    current: OptimizationIteration,
    termination: str | None,
    elapsed_seconds: float | None,
) -> tuple[object, ...]:
    return (
        current.iteration,
        current.phase.value,
        current.accepted,
        current.changed_gate,
        current.previous_cell,
        current.new_cell,
        current.rejection_reason,
        previous.cost,
        current.cost,
        previous.cost - current.cost,
        previous.wns,
        current.wns,
        current.wns - previous.wns,
        previous.tns,
        current.tns,
        current.tns - previous.tns,
        previous.circuit_delay,
        current.circuit_delay,
        current.circuit_delay - previous.circuit_delay,
        previous.area,
        current.area,
        current.area - previous.area,
        previous.power,
        current.power,
        current.power - previous.power,
        termination,
        elapsed_seconds,
    )


def _summary_row(run: TimedOptimization) -> tuple[object, ...]:
    result = run.result
    return (
        run.name,
        optimization_role(run.name),
        result.heuristic.value,
        result.random_seed,
        result.circuit.config.optimization.maximum_iterations,
        result.total_iterations,
        result.accepted_iterations,
        result.sta_calls,
        run.elapsed_seconds,
        result.termination.value,
        result.timing.wns >= 0.0,
        result.timing.wns,
        result.timing.tns,
        result.timing.circuit_delay,
        result.circuit.area,
        result.circuit.power,
        result.cost,
    )


def _comparison_rows(
    circuit: Circuit,
    nominal_timing: TimingAnalysisResult,
    runs: Sequence[TimedOptimization],
) -> Iterable[tuple[object, ...]]:
    by_name = {run.name: run for run in runs}
    canonical = by_name[CANONICAL_OPTIMIZATION]
    yield _pre_optimization_comparison_row(circuit, nominal_timing, canonical)
    for run in runs:
        yield _post_optimization_comparison_row(canonical, run)


def _pre_optimization_comparison_row(
    circuit: Circuit,
    timing: TimingAnalysisResult,
    canonical: TimedOptimization,
) -> tuple[object, ...]:
    constraints = circuit.config.design_constraints
    return (
        "pre_optimization",
        "pre_optimization",
        "baseline",
        CANONICAL_OPTIMIZATION,
        "",
        "",
        circuit.config.optimization.maximum_iterations,
        0,
        0,
        0,
        0.0,
        "",
        timing.wns >= 0.0,
        circuit.power <= constraints.maximum_power_uW,
        circuit.area <= constraints.maximum_area,
        timing.wns,
        timing.tns,
        timing.circuit_delay,
        circuit.area,
        circuit.leakage_power,
        circuit.dynamic_power,
        circuit.power,
        canonical.result.history[0].cost,
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
    )


def _post_optimization_comparison_row(
    canonical: TimedOptimization,
    run: TimedOptimization,
) -> tuple[object, ...]:
    result = run.result
    circuit = result.circuit
    constraints = circuit.config.design_constraints
    differences = optimization_differences(canonical, run)
    return (
        "post_optimization",
        run.name,
        optimization_role(run.name),
        CANONICAL_OPTIMIZATION,
        result.heuristic.value,
        result.random_seed,
        circuit.config.optimization.maximum_iterations,
        result.total_iterations,
        result.accepted_iterations,
        result.sta_calls,
        run.elapsed_seconds,
        result.termination.value,
        result.timing.wns >= 0.0,
        circuit.power <= constraints.maximum_power_uW,
        circuit.area <= constraints.maximum_area,
        result.timing.wns,
        result.timing.tns,
        result.timing.circuit_delay,
        circuit.area,
        circuit.leakage_power,
        circuit.dynamic_power,
        circuit.power,
        result.cost,
        differences["wns_ns"],
        differences["tns_ns"],
        differences["circuit_delay_ns"],
        differences["area"],
        differences["power_uW"],
        result.cost - canonical.result.cost,
        differences["runtime_seconds"],
        differences["sta_calls"],
    )
