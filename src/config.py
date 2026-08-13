"""Analysis configuration loading and validation."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal, cast


class ConfigError(ValueError):
    """Raised when an analysis configuration is invalid."""


@dataclass(frozen=True)
class TransitionTime:
    rise: float
    fall: float


@dataclass(frozen=True)
class TimingAnalysisConfig:
    top_k_paths: int
    separate_rise_fall: bool


@dataclass(frozen=True)
class OperatingConditions:
    supply_voltage: float
    frequency_hz: float
    temperature_c: float
    default_activity_factor: float
    node_activity_factors: Mapping[str, float]


@dataclass(frozen=True)
class DesignConstraints:
    maximum_area: float
    maximum_power_uW: float


@dataclass(frozen=True)
class OptimizationWeights:
    delay: float
    power: float
    area: float
    timing_violation: float


@dataclass(frozen=True)
class OptimizationConfig:
    enabled: bool
    allowed_sizes: tuple[str, ...]
    maximum_iterations: int
    minimum_cost_improvement: float
    normalization_reference: Literal["initial_design"]
    weights: OptimizationWeights


@dataclass(frozen=True)
class MonteCarloConfig:
    enabled: bool
    samples: int
    global_sigma: float
    local_sigma: float
    random_seed: int
    negative_delay_policy: Literal["resample"]


def _required_string(data: Mapping[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"configuration must contain a non-empty {key!r}")
    return value


def _output_loads(data: Mapping[str, object]) -> dict[str, float]:
    raw_loads = data.get("output_loads")
    if not isinstance(raw_loads, dict):
        raise ConfigError("configuration must contain an 'output_loads' object")

    loads: dict[str, float] = {}
    for output_name, raw_load in raw_loads.items():
        if not isinstance(output_name, str) or not output_name:
            raise ConfigError("output load names must be non-empty strings")
        if isinstance(raw_load, bool) or not isinstance(raw_load, (int, float)):
            raise ConfigError(f"output load for {output_name!r} must be a number")
        load = float(raw_load)
        if load < 0.0:
            raise ConfigError(f"output load for {output_name!r} cannot be negative")
        loads[output_name] = load
    return loads


def _number(value: object, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{context} must be a number")
    return float(value)


def _transition_times(
    data: Mapping[str, object], key: str
) -> dict[str, TransitionTime]:
    raw_times = data.get(key)
    if not isinstance(raw_times, dict):
        raise ConfigError(f"configuration must contain a {key!r} object")

    times: dict[str, TransitionTime] = {}
    for net_name, raw_time in raw_times.items():
        if not isinstance(net_name, str) or not net_name:
            raise ConfigError(f"{key} names must be non-empty strings")
        if not isinstance(raw_time, dict):
            raise ConfigError(f"{key}.{net_name} must be an object")
        times[net_name] = TransitionTime(
            rise=_number(raw_time.get("rise"), f"{key}.{net_name}.rise"),
            fall=_number(raw_time.get("fall"), f"{key}.{net_name}.fall"),
        )
    return times


def _timing_analysis(data: Mapping[str, object]) -> TimingAnalysisConfig:
    raw_analysis = data.get("timing_analysis")
    if not isinstance(raw_analysis, dict):
        raise ConfigError("configuration must contain a 'timing_analysis' object")

    top_k_paths = raw_analysis.get("top_k_paths")
    if isinstance(top_k_paths, bool) or not isinstance(top_k_paths, int):
        raise ConfigError("timing_analysis.top_k_paths must be an integer")
    if top_k_paths <= 0:
        raise ConfigError("timing_analysis.top_k_paths must be positive")

    separate_rise_fall = raw_analysis.get("separate_rise_fall")
    if not isinstance(separate_rise_fall, bool):
        raise ConfigError("timing_analysis.separate_rise_fall must be a boolean")

    return TimingAnalysisConfig(top_k_paths, separate_rise_fall)


def _activity_factor(value: object, context: str) -> float:
    factor = _number(value, context)
    if not 0.0 <= factor <= 1.0:
        raise ConfigError(f"{context} must be between 0 and 1")
    return factor


def _operating_conditions(data: Mapping[str, object]) -> OperatingConditions:
    raw_conditions_value = data.get("operating_conditions")
    if not isinstance(raw_conditions_value, dict):
        raise ConfigError(
            "configuration must contain an 'operating_conditions' object"
        )
    if not all(isinstance(key, str) for key in raw_conditions_value):
        raise ConfigError("operating condition names must be strings")
    raw_conditions = cast(dict[str, object], raw_conditions_value)

    supply_voltage = _number(
        raw_conditions.get("supply_voltage"),
        "operating_conditions.supply_voltage",
    )
    if supply_voltage <= 0.0:
        raise ConfigError("operating_conditions.supply_voltage must be positive")

    frequency_hz = _number(
        raw_conditions.get("frequency_hz"),
        "operating_conditions.frequency_hz",
    )
    if frequency_hz <= 0.0:
        raise ConfigError("operating_conditions.frequency_hz must be positive")

    raw_node_factors_value = raw_conditions.get("node_activity_factors")
    if not isinstance(raw_node_factors_value, dict):
        raise ConfigError(
            "operating_conditions.node_activity_factors must be an object"
        )
    if not all(isinstance(key, str) for key in raw_node_factors_value):
        raise ConfigError("activity-factor node names must be strings")
    raw_node_factors = cast(dict[str, object], raw_node_factors_value)

    node_factors: dict[str, float] = {}
    for node_name, raw_factor in raw_node_factors.items():
        if not node_name:
            raise ConfigError("activity-factor node names must be non-empty strings")
        node_factors[node_name] = _activity_factor(
            raw_factor,
            f"operating_conditions.node_activity_factors.{node_name}",
        )

    return OperatingConditions(
        supply_voltage=supply_voltage,
        frequency_hz=frequency_hz,
        temperature_c=_number(
            raw_conditions.get("temperature_c"),
            "operating_conditions.temperature_c",
        ),
        default_activity_factor=_activity_factor(
            raw_conditions.get("default_activity_factor"),
            "operating_conditions.default_activity_factor",
        ),
        node_activity_factors=MappingProxyType(node_factors),
    )


def _object(data: Mapping[str, object], key: str) -> dict[str, object]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise ConfigError(f"configuration must contain a {key!r} object")
    if not all(isinstance(item_key, str) for item_key in value):
        raise ConfigError(f"{key} keys must be strings")
    return cast(dict[str, object], value)


def _positive_number(value: object, context: str) -> float:
    number = _number(value, context)
    if number <= 0.0:
        raise ConfigError(f"{context} must be positive")
    return number


def _design_constraints(data: Mapping[str, object]) -> DesignConstraints:
    constraints = _object(data, "design_constraints")
    return DesignConstraints(
        maximum_area=_positive_number(
            constraints.get("maximum_area"),
            "design_constraints.maximum_area",
        ),
        maximum_power_uW=_positive_number(
            constraints.get("maximum_power_uW"),
            "design_constraints.maximum_power_uW",
        ),
    )


def _optimization(data: Mapping[str, object]) -> OptimizationConfig:
    optimization = _object(data, "optimization")
    enabled = optimization.get("enabled")
    if not isinstance(enabled, bool):
        raise ConfigError("optimization.enabled must be a boolean")

    raw_sizes = optimization.get("allowed_sizes")
    if not isinstance(raw_sizes, list) or not raw_sizes:
        raise ConfigError("optimization.allowed_sizes must be a non-empty array")
    if not all(isinstance(size, str) and size for size in raw_sizes):
        raise ConfigError("optimization.allowed_sizes must contain non-empty strings")
    allowed_sizes = tuple(cast(list[str], raw_sizes))
    if len(set(allowed_sizes)) != len(allowed_sizes):
        raise ConfigError("optimization.allowed_sizes cannot contain duplicates")

    maximum_iterations = optimization.get("maximum_iterations")
    if isinstance(maximum_iterations, bool) or not isinstance(maximum_iterations, int):
        raise ConfigError("optimization.maximum_iterations must be an integer")
    if maximum_iterations <= 0:
        raise ConfigError("optimization.maximum_iterations must be positive")

    minimum_improvement = _number(
        optimization.get("minimum_cost_improvement"),
        "optimization.minimum_cost_improvement",
    )
    if minimum_improvement < 0.0:
        raise ConfigError(
            "optimization.minimum_cost_improvement cannot be negative"
        )

    normalization_reference = optimization.get("normalization_reference")
    if normalization_reference != "initial_design":
        raise ConfigError(
            "optimization.normalization_reference must be 'initial_design'"
        )

    raw_weights = _object(optimization, "weights")
    weights = OptimizationWeights(
        delay=_number(raw_weights.get("delay"), "optimization.weights.delay"),
        power=_number(raw_weights.get("power"), "optimization.weights.power"),
        area=_number(raw_weights.get("area"), "optimization.weights.area"),
        timing_violation=_number(
            raw_weights.get("timing_violation"),
            "optimization.weights.timing_violation",
        ),
    )
    if min(
        weights.delay,
        weights.power,
        weights.area,
        weights.timing_violation,
    ) < 0.0:
        raise ConfigError("optimization weights cannot be negative")
    if sum(
        (
            weights.delay,
            weights.power,
            weights.area,
            weights.timing_violation,
        )
    ) == 0.0:
        raise ConfigError("at least one optimization weight must be positive")

    return OptimizationConfig(
        enabled=enabled,
        allowed_sizes=allowed_sizes,
        maximum_iterations=maximum_iterations,
        minimum_cost_improvement=minimum_improvement,
        normalization_reference="initial_design",
        weights=weights,
    )


def _monte_carlo(data: Mapping[str, object]) -> MonteCarloConfig:
    monte_carlo = _object(data, "monte_carlo")
    enabled = monte_carlo.get("enabled")
    if not isinstance(enabled, bool):
        raise ConfigError("monte_carlo.enabled must be a boolean")

    samples = monte_carlo.get("samples")
    if isinstance(samples, bool) or not isinstance(samples, int):
        raise ConfigError("monte_carlo.samples must be an integer")
    if samples <= 0:
        raise ConfigError("monte_carlo.samples must be positive")

    global_sigma = _number(
        monte_carlo.get("global_sigma"),
        "monte_carlo.global_sigma",
    )
    local_sigma = _number(
        monte_carlo.get("local_sigma"),
        "monte_carlo.local_sigma",
    )
    if global_sigma < 0.0 or local_sigma < 0.0:
        raise ConfigError("Monte Carlo sigma values cannot be negative")

    random_seed = monte_carlo.get("random_seed")
    if isinstance(random_seed, bool) or not isinstance(random_seed, int):
        raise ConfigError("monte_carlo.random_seed must be an integer")

    negative_delay_policy = monte_carlo.get("negative_delay_policy")
    if negative_delay_policy != "resample":
        raise ConfigError(
            "monte_carlo.negative_delay_policy must be 'resample'"
        )
    return MonteCarloConfig(
        enabled=enabled,
        samples=samples,
        global_sigma=global_sigma,
        local_sigma=local_sigma,
        random_seed=random_seed,
        negative_delay_policy="resample",
    )


class Config:
    """A validated configuration loaded from a circuit's JSON file."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if not self.path.is_file():
            raise ConfigError(f"configuration is not a file: {self.path}")
        if self.path.suffix.lower() != ".json":
            raise ConfigError(f"configuration must be a JSON file: {self.path}")

        try:
            with self.path.open("r", encoding="utf-8") as config_file:
                raw_data: object = json.load(config_file)
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError(f"cannot read configuration {self.path}: {exc}") from exc

        if not isinstance(raw_data, dict):
            raise ConfigError("configuration root must be a JSON object")
        if not all(isinstance(key, str) for key in raw_data):
            raise ConfigError("configuration keys must be strings")
        data = cast(dict[str, object], raw_data)

        self.circuit_name = _required_string(data, "circuit_name")
        library_value = _required_string(data, "cell_library")
        library_path = Path(library_value)
        if not library_path.is_absolute():
            library_path = self.path.parent / library_path
        self.cell_library_path = library_path.resolve()
        if not self.cell_library_path.is_file():
            raise ConfigError(f"cell library is not a file: {self.cell_library_path}")

        self.output_loads: Mapping[str, float] = MappingProxyType(
            _output_loads(data)
        )
        self.input_arrival_times: Mapping[str, TransitionTime] = MappingProxyType(
            _transition_times(data, "input_arrival_times")
        )
        self.output_required_times: Mapping[str, TransitionTime] = MappingProxyType(
            _transition_times(data, "output_required_times")
        )
        self.timing_analysis: TimingAnalysisConfig = _timing_analysis(data)
        self.operating_conditions: OperatingConditions = _operating_conditions(data)
        self.design_constraints: DesignConstraints = _design_constraints(data)
        self.optimization: OptimizationConfig = _optimization(data)
        self.monte_carlo: MonteCarloConfig = _monte_carlo(data)

    def output_load(self, net_name: str) -> float:
        """Return the configured external capacitance of a primary output."""

        try:
            return self.output_loads[net_name]
        except KeyError as exc:
            raise ConfigError(
                f"no external load is configured for output {net_name!r}"
            ) from exc

    def input_arrival(self, net_name: str) -> TransitionTime:
        """Return the configured rise and fall arrival of a primary input."""

        try:
            return self.input_arrival_times[net_name]
        except KeyError as exc:
            raise ConfigError(
                f"no arrival time is configured for input {net_name!r}"
            ) from exc

    def output_required(self, net_name: str) -> TransitionTime:
        """Return the configured rise and fall required time of an output."""

        try:
            return self.output_required_times[net_name]
        except KeyError as exc:
            raise ConfigError(
                f"no required time is configured for output {net_name!r}"
            ) from exc

    def activity_factor(self, node_name: str) -> float:
        """Return a node override or the configured default activity factor."""

        conditions = self.operating_conditions
        return conditions.node_activity_factors.get(
            node_name,
            conditions.default_activity_factor,
        )
