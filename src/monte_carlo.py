"""Paired Monte Carlo timing analysis under global and local variations."""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from random import Random
from statistics import fmean, pstdev
from time import perf_counter

from .cell import RiseFall
from .circuit import Circuit
from .config import MonteCarloConfig
from .sta import CriticalPath, analyze_timing


logger = logging.getLogger(__name__)
MAX_RESAMPLE_ATTEMPTS = 100_000


class MonteCarloError(RuntimeError):
    """Raised when a valid Monte Carlo sample cannot be generated."""


@dataclass(frozen=True)
class VariationSample:
    sample: int
    global_z: float
    local_z: Mapping[str, float]
    scale_factors: Mapping[str, float]
    resamples: int


@dataclass(frozen=True)
class TimingSample:
    sample: int
    circuit_delay: float
    wns: float
    tns: float
    violation: bool
    critical_path_id: str
    critical_path: str
    critical_gates: tuple[str, ...]


@dataclass(frozen=True)
class DistributionStatistics:
    mean: float
    standard_deviation: float
    minimum: float
    maximum: float


@dataclass(frozen=True)
class MonteCarloRun:
    state: str
    samples: tuple[TimingSample, ...]
    delay: DistributionStatistics
    wns: DistributionStatistics
    tns: DistributionStatistics
    violation_probability: float
    timing_yield: float
    critical_path_counts: Mapping[tuple[str, str], int]
    gate_criticality_counts: Mapping[str, int]
    elapsed_seconds: float


def generate_variations(
    circuit: Circuit,
    config: MonteCarloConfig,
) -> tuple[VariationSample, ...]:
    """Generate one reusable set of valid global/local variation samples."""

    random_generator = Random(config.random_seed)
    gate_names = tuple(circuit.gates)
    variations: list[VariationSample] = []
    for sample_index in range(1, config.samples + 1):
        for attempt in range(MAX_RESAMPLE_ATTEMPTS):
            global_z = random_generator.gauss(0.0, 1.0)
            local_z = {
                gate_name: random_generator.gauss(0.0, 1.0)
                for gate_name in gate_names
            }
            factors = {
                gate_name: (
                    1.0
                    + config.global_sigma * global_z
                    + config.local_sigma * local_z[gate_name]
                )
                for gate_name in gate_names
            }
            if all(factor > 0.0 for factor in factors.values()):
                variations.append(
                    VariationSample(
                        sample=sample_index,
                        global_z=global_z,
                        local_z=local_z,
                        scale_factors=factors,
                        resamples=attempt,
                    )
                )
                break
        else:
            raise MonteCarloError(
                f"could not generate positive delays for sample {sample_index} "
                f"after {MAX_RESAMPLE_ATTEMPTS} attempts"
            )
    return tuple(variations)


def run_monte_carlo(
    circuit: Circuit,
    variations: Sequence[VariationSample],
    state: str,
) -> MonteCarloRun:
    """Evaluate one circuit state using an existing paired variation set."""

    logger.info(
        "Running %s Monte Carlo timing analysis (%d samples)",
        state,
        len(variations),
    )
    started_at = perf_counter()
    samples: list[TimingSample] = []
    path_counts: Counter[tuple[str, str]] = Counter()
    gate_counts: Counter[str] = Counter()

    for variation in variations:
        sampled_delays = {
            gate_name: RiseFall(
                nominal.rise * variation.scale_factors[gate_name],
                nominal.fall * variation.scale_factors[gate_name],
            )
            for gate_name, nominal in circuit.gate_delays.items()
        }
        timing = analyze_timing(circuit, sampled_delays)
        critical = timing.critical_paths[0]
        path_id, path_display = _critical_path_identity(critical)
        critical_gates = tuple(gate.name for gate in critical.path.gates)
        samples.append(
            TimingSample(
                sample=variation.sample,
                circuit_delay=timing.circuit_delay,
                wns=timing.wns,
                tns=timing.tns,
                violation=timing.wns < 0.0,
                critical_path_id=path_id,
                critical_path=path_display,
                critical_gates=critical_gates,
            )
        )
        path_counts[(path_id, path_display)] += 1
        gate_counts.update(critical_gates)

    violation_count = sum(sample.violation for sample in samples)
    violation_probability = violation_count / len(samples)
    elapsed_seconds = perf_counter() - started_at
    logger.info(
        "%s Monte Carlo completed in %.6f s with timing yield %.6f",
        state,
        elapsed_seconds,
        1.0 - violation_probability,
    )
    return MonteCarloRun(
        state=state,
        samples=tuple(samples),
        delay=_statistics(sample.circuit_delay for sample in samples),
        wns=_statistics(sample.wns for sample in samples),
        tns=_statistics(sample.tns for sample in samples),
        violation_probability=violation_probability,
        timing_yield=1.0 - violation_probability,
        critical_path_counts=dict(path_counts),
        gate_criticality_counts=dict(gate_counts),
        elapsed_seconds=elapsed_seconds,
    )


def _statistics(values: Iterable[float]) -> DistributionStatistics:
    numeric_values = tuple(values)
    if not numeric_values:
        raise MonteCarloError("cannot summarize an empty Monte Carlo run")
    return DistributionStatistics(
        mean=fmean(numeric_values),
        standard_deviation=pstdev(numeric_values),
        minimum=min(numeric_values),
        maximum=max(numeric_values),
    )


def _critical_path_identity(critical: CriticalPath) -> tuple[str, str]:
    path = critical.path
    transition_values = tuple(
        transition.value for transition in critical.transitions
    )
    steps = tuple(
        f"{step.gate.name}[{step.input_pin}]"
        for step in path.steps
    )
    path_id = "|".join(
        (
            f"{path.input_net.name}:{transition_values[0]}",
            *steps,
            f"{path.output_net.name}:{transition_values[-1]}",
        )
    )
    path_display = " -> ".join(
        (path.input_net.name, *(step.gate.name for step in path.steps))
    )
    return path_id, path_display
