"""Optimization history, summary, and comparison report generation."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from .circuit import Circuit
from .experiment_artifacts import ArtifactWriter
from .optimizer import OptimizationIteration
from .report_models import (
    CANONICAL_OPTIMIZATION,
    GREEDY_OPTIMIZATION,
    TimedOptimization,
    circuit_specification,
    optimization_differences,
    optimization_record,
    optimization_role,
)
from .sta import TimingAnalysisResult


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
    writer.write_json(
        "optimization_comparison",
        _comparison_record(circuit, nominal_timing, runs),
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


def _comparison_record(
    circuit: Circuit,
    nominal_timing: TimingAnalysisResult,
    runs: Sequence[TimedOptimization],
) -> dict[str, object]:
    by_name = {run.name: run for run in runs}
    logical = by_name[CANONICAL_OPTIMIZATION]
    greedy = by_name[GREEDY_OPTIMIZATION]
    initial_cost = logical.result.history[0].cost
    return {
        "canonical_method": CANONICAL_OPTIMIZATION,
        "difference_definition": "greedy_baseline - logical_effort_guided",
        "pre_optimization": circuit_specification(
            circuit, nominal_timing, initial_cost
        ),
        "logical_effort_guided": optimization_record(logical),
        "greedy_baseline": optimization_record(greedy),
        "differences": optimization_differences(logical, greedy),
    }

