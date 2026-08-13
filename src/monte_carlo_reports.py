"""Monte Carlo report schemas and persistence."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import asdict

from .circuit import Circuit
from .experiment_artifacts import ArtifactWriter
from .monte_carlo import MonteCarloRun, VariationSample
from .optimizer import OptimizationResult


MONTE_CARLO_HEADERS: dict[str, tuple[str, ...]] = {
    "monte_carlo_samples": (
        "sample", "circuit_state", "global_z", "resamples",
        "circuit_delay_ns", "wns_ns", "tns_ns", "violation",
        "critical_path_id", "critical_path",
    ),
    "monte_carlo_variations": (
        "sample", "gate", "global_z", "local_z",
        "delay_scale_factor", "resamples",
    ),
    "monte_carlo_critical_paths": (
        "circuit_state", "rank", "path_id", "path", "count", "probability",
    ),
    "monte_carlo_gate_criticality": (
        "circuit_state", "gate", "count", "criticality_probability",
    ),
    "monte_carlo_statistics": (
        "circuit_state", "samples", "delay_mean_ns",
        "delay_standard_deviation_ns", "delay_minimum_ns", "delay_maximum_ns",
        "wns_mean_ns", "wns_standard_deviation_ns", "wns_minimum_ns",
        "wns_maximum_ns", "tns_mean_ns", "tns_standard_deviation_ns",
        "tns_minimum_ns", "tns_maximum_ns", "violation_probability",
        "timing_yield", "elapsed_seconds",
    ),
}


def save_skipped_monte_carlo(
    writer: ArtifactWriter,
    circuit: Circuit,
) -> dict[str, object]:
    summary: dict[str, object] = {
        "status": "skipped",
        "reason": "disabled_by_configuration",
        "configuration": {
            "enabled": False,
            "samples": circuit.config.monte_carlo.samples,
            "random_seed": circuit.config.monte_carlo.random_seed,
        },
    }
    writer.write_json("monte_carlo_summary", summary)
    for name, header in MONTE_CARLO_HEADERS.items():
        writer.write_csv(name, header, ())
    return summary


def save_completed_monte_carlo(
    writer: ArtifactWriter,
    circuit: Circuit,
    optimization: OptimizationResult,
    variations: Sequence[VariationSample],
    pre: MonteCarloRun,
    post: MonteCarloRun,
) -> dict[str, object]:
    runs = (pre, post)
    writer.write_csv(
        "monte_carlo_samples",
        MONTE_CARLO_HEADERS["monte_carlo_samples"],
        _sample_rows(variations, pre, post),
    )
    writer.write_csv(
        "monte_carlo_variations",
        MONTE_CARLO_HEADERS["monte_carlo_variations"],
        _variation_rows(variations),
    )
    writer.write_csv(
        "monte_carlo_critical_paths",
        MONTE_CARLO_HEADERS["monte_carlo_critical_paths"],
        _critical_path_rows(runs),
    )
    writer.write_csv(
        "monte_carlo_gate_criticality",
        MONTE_CARLO_HEADERS["monte_carlo_gate_criticality"],
        _gate_criticality_rows(runs),
    )
    writer.write_csv(
        "monte_carlo_statistics",
        MONTE_CARLO_HEADERS["monte_carlo_statistics"],
        (_statistics_row(run) for run in runs),
    )
    summary = _summary_record(circuit, optimization, pre, post)
    writer.write_json("monte_carlo_summary", summary)
    return summary


def _sample_rows(
    variations: Sequence[VariationSample],
    pre: MonteCarloRun,
    post: MonteCarloRun,
) -> Iterable[tuple[object, ...]]:
    for variation, pre_sample, post_sample in zip(
        variations, pre.samples, post.samples, strict=True
    ):
        for state, sample in ((pre.state, pre_sample), (post.state, post_sample)):
            yield (
                sample.sample,
                state,
                variation.global_z,
                variation.resamples,
                sample.circuit_delay,
                sample.wns,
                sample.tns,
                sample.violation,
                sample.critical_path_id,
                sample.critical_path,
            )


def _variation_rows(
    variations: Sequence[VariationSample],
) -> Iterable[tuple[object, ...]]:
    for variation in variations:
        for gate_name, local_z in variation.local_z.items():
            yield (
                variation.sample,
                gate_name,
                variation.global_z,
                local_z,
                variation.scale_factors[gate_name],
                variation.resamples,
            )


def _critical_path_rows(
    runs: Sequence[MonteCarloRun],
) -> Iterable[tuple[object, ...]]:
    for run in runs:
        ordered = sorted(
            run.critical_path_counts.items(),
            key=lambda item: (-item[1], item[0][0]),
        )
        for rank, ((path_id, path), count) in enumerate(ordered, start=1):
            yield run.state, rank, path_id, path, count, count / len(run.samples)


def _gate_criticality_rows(
    runs: Sequence[MonteCarloRun],
) -> Iterable[tuple[object, ...]]:
    for run in runs:
        ordered = sorted(
            run.gate_criticality_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )
        for gate_name, count in ordered:
            yield run.state, gate_name, count, count / len(run.samples)


def _statistics_row(run: MonteCarloRun) -> tuple[object, ...]:
    return (
        run.state,
        len(run.samples),
        run.delay.mean,
        run.delay.standard_deviation,
        run.delay.minimum,
        run.delay.maximum,
        run.wns.mean,
        run.wns.standard_deviation,
        run.wns.minimum,
        run.wns.maximum,
        run.tns.mean,
        run.tns.standard_deviation,
        run.tns.minimum,
        run.tns.maximum,
        run.violation_probability,
        run.timing_yield,
        run.elapsed_seconds,
    )


def _summary_record(
    circuit: Circuit,
    optimization: OptimizationResult,
    pre: MonteCarloRun,
    post: MonteCarloRun,
) -> dict[str, object]:
    config = circuit.config.monte_carlo
    return {
        "status": "completed",
        "configuration": {
            "samples": config.samples,
            "global_sigma": config.global_sigma,
            "local_sigma": config.local_sigma,
            "random_seed": config.random_seed,
            "negative_delay_policy": config.negative_delay_policy,
            "supply_voltage": circuit.config.operating_conditions.supply_voltage,
            "temperature_c": circuit.config.operating_conditions.temperature_c,
        },
        "optimization": {
            "heuristic": optimization.heuristic.value,
            "total_iterations": optimization.total_iterations,
            "accepted_iterations": optimization.accepted_iterations,
            "termination": optimization.termination.value,
        },
        "pre_optimization": _run_summary(pre),
        "post_optimization": _run_summary(post),
        "comparison": {
            "delay_mean_change_ns": post.delay.mean - pre.delay.mean,
            "delay_standard_deviation_change_ns": (
                post.delay.standard_deviation - pre.delay.standard_deviation
            ),
            "violation_probability_change": (
                post.violation_probability - pre.violation_probability
            ),
            "timing_yield_improvement": post.timing_yield - pre.timing_yield,
        },
    }


def _run_summary(run: MonteCarloRun) -> dict[str, object]:
    most_probable = max(
        run.critical_path_counts.items(),
        key=lambda item: (item[1], item[0][0]),
    )
    (path_id, path), count = most_probable
    return {
        "delay_ns": asdict(run.delay),
        "wns_ns": asdict(run.wns),
        "tns_ns": asdict(run.tns),
        "violation_probability": run.violation_probability,
        "timing_yield": run.timing_yield,
        "elapsed_seconds": run.elapsed_seconds,
        "most_probable_critical_path": {
            "path_id": path_id,
            "path": path,
            "count": count,
            "probability": count / len(run.samples),
        },
    }

