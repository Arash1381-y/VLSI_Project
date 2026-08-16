"""Strict versioned configuration for benchmark generation and evaluation."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, cast

from ..cell import CellLibrary
from ..config import Config
from ..netlist import NetListParser
from ..optimization_heuristics import OptimizationHeuristic


class BenchmarkConfigError(ValueError):
    """Raised when a benchmark configuration is invalid."""


@dataclass(frozen=True)
class IntRange:
    minimum: int
    maximum: int


@dataclass(frozen=True)
class FloatRange:
    minimum: float
    maximum: float


@dataclass(frozen=True)
class SuiteConfig:
    name: str
    output_root: Path
    random_seed: int
    maximum_attempts_per_case: int


@dataclass(frozen=True)
class TopologyConfig:
    gate_count: IntRange
    logic_levels: IntRange
    primary_inputs: IntRange
    primary_outputs: IntRange
    maximum_fanout: int
    reconvergence_probability: float
    branch_probability: float
    family_weights: Mapping[str, float]


@dataclass(frozen=True)
class GeneratedSourceConfig:
    case_count: int
    topology: TopologyConfig


@dataclass(frozen=True)
class SeededSourceConfig:
    directory: Path
    case_count: int


@dataclass(frozen=True)
class SourcesConfig:
    generated: GeneratedSourceConfig | None
    seeded: tuple[SeededSourceConfig, ...]

    @property
    def case_count(self) -> int:
        generated_count = 0 if self.generated is None else self.generated.case_count
        return generated_count + sum(source.case_count for source in self.seeded)


@dataclass(frozen=True)
class ElectricalConfig:
    input_arrival_rise_ns: FloatRange
    input_arrival_fall_ns: FloatRange
    output_load_fF: FloatRange
    required_time_margin_ns: FloatRange
    supply_voltage: float
    frequency_hz: float
    temperature_c: float
    default_activity_factor: float
    activity_override_probability: float
    activity_override_range: FloatRange


@dataclass(frozen=True)
class ReferenceSearchConfig:
    allowed_sizes: tuple[str, ...]
    beam_width: int
    restarts: int
    maximum_expansions: int
    stagnation_limit: int
    provisional_area_headroom: float
    provisional_power_headroom: float
    final_area_headroom: FloatRange
    final_power_headroom: FloatRange


@dataclass(frozen=True)
class PerturbationConfig:
    changed_gate_count: IntRange
    steps_per_gate: IntRange
    target_wns_ns: FloatRange
    require_reference_path_gate: bool
    require_critical_path_change: bool
    maximum_attempts: int


@dataclass(frozen=True)
class AnalyzerWeights:
    delay: float
    power: float
    area: float
    timing_violation: float


@dataclass(frozen=True)
class AnalyzerConfig:
    top_k_paths: int
    separate_rise_fall: bool
    maximum_iterations: int
    minimum_cost_improvement: float
    weights: AnalyzerWeights


@dataclass(frozen=True)
class EvaluationConfig:
    heuristics: tuple[OptimizationHeuristic, ...]
    random_greedy_repetitions: int
    maximum_seconds_per_run: float
    parallel_workers: int


@dataclass(frozen=True)
class BenchmarkConfig:
    """Validated immutable benchmark-suite configuration."""

    path: Path
    suite: SuiteConfig
    cell_library_path: Path
    sources: SourcesConfig
    electrical: ElectricalConfig
    reference_search: ReferenceSearchConfig
    perturbation: PerturbationConfig
    analyzer: AnalyzerConfig
    evaluation: EvaluationConfig

    @classmethod
    def load(cls, path: str | Path) -> BenchmarkConfig:
        return cls._load(path, validate_seed_sources=True)

    @classmethod
    def load_suite(cls, path: str | Path) -> BenchmarkConfig:
        """Load a copied suite without requiring its original seed circuits."""

        return cls._load(path, validate_seed_sources=False)

    @classmethod
    def _load(
        cls,
        path: str | Path,
        *,
        validate_seed_sources: bool,
    ) -> BenchmarkConfig:
        config_path = Path(path).resolve()
        try:
            raw: object = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BenchmarkConfigError(
                f"cannot read benchmark configuration {config_path}: {exc}"
            ) from exc
        root = _object(raw, "benchmark configuration")
        _keys(
            root,
            {
                "schema_version", "suite", "cell_library", "sources",
                "electrical", "reference_search", "perturbation", "analyzer",
                "evaluation",
            },
            "benchmark configuration",
        )
        if _integer(root["schema_version"], "schema_version") != 1:
            raise BenchmarkConfigError("schema_version must be 1")

        base = config_path.parent
        library_path = _resolved_file(base, root["cell_library"], "cell_library")
        suite = _suite(_object(root["suite"], "suite"), base)
        reference = _reference_search(
            _object(root["reference_search"], "reference_search")
        )
        library = CellLibrary(library_path)
        sources = _sources(
            _object(root["sources"], "sources"),
            base,
            library,
            reference.allowed_sizes,
            validate_seed_sources=validate_seed_sources,
        )
        electrical = _electrical(_object(root["electrical"], "electrical"))
        perturbation = _perturbation(
            _object(root["perturbation"], "perturbation")
        )
        analyzer = _analyzer(_object(root["analyzer"], "analyzer"))
        evaluation = _evaluation(_object(root["evaluation"], "evaluation"))
        _validate_library(library, sources, reference)
        return cls(
            config_path, suite, library_path, sources, electrical, reference,
            perturbation, analyzer, evaluation,
        )


def _suite(data: dict[str, object], base: Path) -> SuiteConfig:
    _keys(
        data,
        {"name", "output_root", "random_seed", "maximum_attempts_per_case"},
        "suite",
    )
    output_root = Path(_string(data["output_root"], "suite.output_root"))
    if not output_root.is_absolute():
        output_root = (base / output_root).resolve()
    name = _string(data["name"], "suite.name")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name) is None:
        raise BenchmarkConfigError(
            "suite.name may contain only letters, digits, '.', '_', and '-'"
        )
    return SuiteConfig(
        name,
        output_root,
        _integer(data["random_seed"], "suite.random_seed"),
        _positive_integer(
            data["maximum_attempts_per_case"],
            "suite.maximum_attempts_per_case",
        ),
    )


def _sources(
    data: dict[str, object],
    base: Path,
    library: CellLibrary,
    allowed_sizes: tuple[str, ...],
    *,
    validate_seed_sources: bool,
) -> SourcesConfig:
    _allowed_keys(data, {"generated", "seeded"}, "sources")
    generated: GeneratedSourceConfig | None = None
    if "generated" in data:
        raw_generated = _object(data["generated"], "sources.generated")
        _keys(raw_generated, {"case_count", "topology"}, "sources.generated")
        generated = GeneratedSourceConfig(
            _positive_integer(
                raw_generated["case_count"], "sources.generated.case_count"
            ),
            _topology(
                _object(raw_generated["topology"], "sources.generated.topology")
            ),
        )

    seeded: list[SeededSourceConfig] = []
    if "seeded" in data:
        raw_seeded = _list(data["seeded"], "sources.seeded")
        for index, raw_source in enumerate(raw_seeded):
            context = f"sources.seeded[{index}]"
            source = _object(raw_source, context)
            _keys(source, {"directory", "case_count"}, context)
            directory = _source_directory(
                base,
                source["directory"],
                f"{context}.directory",
                must_exist=validate_seed_sources,
            )
            netlist_path = directory / "netlist.txt"
            config_path = directory / "config.json"
            if validate_seed_sources and (
                not netlist_path.is_file() or not config_path.is_file()
            ):
                raise BenchmarkConfigError(
                    f"{context}.directory must contain netlist.txt and config.json"
                )
            if validate_seed_sources:
                seed_config = Config(config_path)
                if _digest(seed_config.cell_library_path) != _digest(library.path):
                    raise BenchmarkConfigError(
                        f"{context} does not use the configured cell library"
                    )
                try:
                    _, gates, _ = NetListParser(netlist_path, library).parse()
                except ValueError as exc:
                    raise BenchmarkConfigError(
                        f"{context} is not a valid seeded circuit: {exc}"
                    ) from exc
                required_sizes = set(allowed_sizes)
                for gate in gates.values():
                    family_sizes = {
                        cell.size for cell in library.variants(gate.cell_family)
                    }
                    if not required_sizes <= family_sizes:
                        raise BenchmarkConfigError(
                            f"{context} gate family {gate.cell_family!r} does not "
                            "support every allowed size"
                        )
            seeded.append(
                SeededSourceConfig(
                    directory,
                    _positive_integer(source["case_count"], f"{context}.case_count"),
                )
            )
    result = SourcesConfig(generated, tuple(seeded))
    if result.case_count == 0:
        raise BenchmarkConfigError("sources must request at least one case")
    return result


def _topology(data: dict[str, object]) -> TopologyConfig:
    fields = {
        "gate_count", "logic_levels", "primary_inputs", "primary_outputs",
        "maximum_fanout", "reconvergence_probability", "branch_probability",
        "family_weights",
    }
    _keys(data, fields, "sources.generated.topology")
    weights_data = _object(
        data["family_weights"], "sources.generated.topology.family_weights"
    )
    if not weights_data:
        raise BenchmarkConfigError("family_weights cannot be empty")
    weights = {
        family: _positive_number(value, f"family_weights.{family}")
        for family, value in weights_data.items()
    }
    result = TopologyConfig(
        _int_range(data["gate_count"], "topology.gate_count", minimum=1),
        _int_range(data["logic_levels"], "topology.logic_levels", minimum=1),
        _int_range(data["primary_inputs"], "topology.primary_inputs", minimum=1),
        _int_range(data["primary_outputs"], "topology.primary_outputs", minimum=1),
        _positive_integer(data["maximum_fanout"], "topology.maximum_fanout"),
        _probability(data["reconvergence_probability"], "topology.reconvergence_probability"),
        _probability(data["branch_probability"], "topology.branch_probability"),
        MappingProxyType(weights),
    )
    if result.logic_levels.maximum > result.gate_count.maximum:
        raise BenchmarkConfigError("logic_levels cannot exceed gate_count")
    return result


def _electrical(data: dict[str, object]) -> ElectricalConfig:
    fields = {
        "input_arrival_rise_ns", "input_arrival_fall_ns", "output_load_fF",
        "required_time_margin_ns", "supply_voltage", "frequency_hz",
        "temperature_c", "default_activity_factor",
        "activity_override_probability", "activity_override_range",
    }
    _keys(data, fields, "electrical")
    return ElectricalConfig(
        _float_range(data["input_arrival_rise_ns"], "electrical.input_arrival_rise_ns"),
        _float_range(data["input_arrival_fall_ns"], "electrical.input_arrival_fall_ns"),
        _float_range(data["output_load_fF"], "electrical.output_load_fF", minimum=0.0),
        _float_range(
            data["required_time_margin_ns"],
            "electrical.required_time_margin_ns",
            minimum=0.0,
        ),
        _positive_number(data["supply_voltage"], "electrical.supply_voltage"),
        _positive_number(data["frequency_hz"], "electrical.frequency_hz"),
        _number(data["temperature_c"], "electrical.temperature_c"),
        _probability(data["default_activity_factor"], "electrical.default_activity_factor"),
        _probability(
            data["activity_override_probability"],
            "electrical.activity_override_probability",
        ),
        _float_range(
            data["activity_override_range"],
            "electrical.activity_override_range",
            minimum=0.0,
            maximum=1.0,
        ),
    )


def _reference_search(data: dict[str, object]) -> ReferenceSearchConfig:
    fields = {
        "algorithm", "allowed_sizes", "beam_width", "restarts",
        "maximum_expansions", "stagnation_limit", "provisional_area_headroom",
        "provisional_power_headroom", "final_area_headroom",
        "final_power_headroom",
    }
    _keys(data, fields, "reference_search")
    if data["algorithm"] != "multi_start_beam":
        raise BenchmarkConfigError(
            "reference_search.algorithm must be 'multi_start_beam'"
        )
    sizes = tuple(_string(item, "reference_search.allowed_sizes[]") for item in _list(
        data["allowed_sizes"], "reference_search.allowed_sizes"
    ))
    if not sizes or len(set(sizes)) != len(sizes):
        raise BenchmarkConfigError(
            "reference_search.allowed_sizes must be non-empty and unique"
        )
    return ReferenceSearchConfig(
        sizes,
        _positive_integer(data["beam_width"], "reference_search.beam_width"),
        _minimum_integer(data["restarts"], "reference_search.restarts", 4),
        _positive_integer(
            data["maximum_expansions"], "reference_search.maximum_expansions"
        ),
        _positive_integer(
            data["stagnation_limit"], "reference_search.stagnation_limit"
        ),
        _nonnegative_number(
            data["provisional_area_headroom"],
            "reference_search.provisional_area_headroom",
        ),
        _nonnegative_number(
            data["provisional_power_headroom"],
            "reference_search.provisional_power_headroom",
        ),
        _float_range(
            data["final_area_headroom"],
            "reference_search.final_area_headroom",
            minimum=0.0,
        ),
        _float_range(
            data["final_power_headroom"],
            "reference_search.final_power_headroom",
            minimum=0.0,
        ),
    )


def _perturbation(data: dict[str, object]) -> PerturbationConfig:
    fields = {
        "direction", "changed_gate_count", "steps_per_gate", "target_wns_ns",
        "require_reference_path_gate", "require_critical_path_change",
        "maximum_attempts",
    }
    _keys(data, fields, "perturbation")
    if data["direction"] != "downsize_only":
        raise BenchmarkConfigError(
            "perturbation.direction must be 'downsize_only'"
        )
    target = _float_range(data["target_wns_ns"], "perturbation.target_wns_ns")
    if target.maximum >= 0.0:
        raise BenchmarkConfigError("perturbation.target_wns_ns must be negative")
    return PerturbationConfig(
        _int_range(data["changed_gate_count"], "perturbation.changed_gate_count", minimum=1),
        _int_range(data["steps_per_gate"], "perturbation.steps_per_gate", minimum=1),
        target,
        _boolean(
            data["require_reference_path_gate"],
            "perturbation.require_reference_path_gate",
        ),
        _boolean(
            data["require_critical_path_change"],
            "perturbation.require_critical_path_change",
        ),
        _positive_integer(data["maximum_attempts"], "perturbation.maximum_attempts"),
    )


def _analyzer(data: dict[str, object]) -> AnalyzerConfig:
    fields = {
        "top_k_paths", "separate_rise_fall", "maximum_iterations",
        "minimum_cost_improvement", "weights",
    }
    _keys(data, fields, "analyzer")
    raw_weights = _object(data["weights"], "analyzer.weights")
    _keys(
        raw_weights,
        {"delay", "power", "area", "timing_violation"},
        "analyzer.weights",
    )
    weights = AnalyzerWeights(
        _nonnegative_number(raw_weights["delay"], "analyzer.weights.delay"),
        _nonnegative_number(raw_weights["power"], "analyzer.weights.power"),
        _nonnegative_number(raw_weights["area"], "analyzer.weights.area"),
        _nonnegative_number(
            raw_weights["timing_violation"], "analyzer.weights.timing_violation"
        ),
    )
    if sum((weights.delay, weights.power, weights.area, weights.timing_violation)) == 0:
        raise BenchmarkConfigError("at least one analyzer weight must be positive")
    return AnalyzerConfig(
        _positive_integer(data["top_k_paths"], "analyzer.top_k_paths"),
        _boolean(data["separate_rise_fall"], "analyzer.separate_rise_fall"),
        _positive_integer(data["maximum_iterations"], "analyzer.maximum_iterations"),
        _nonnegative_number(
            data["minimum_cost_improvement"],
            "analyzer.minimum_cost_improvement",
        ),
        weights,
    )


def _evaluation(data: dict[str, object]) -> EvaluationConfig:
    required_fields = {
        "heuristics", "random_greedy_repetitions", "candidate_scope",
        "maximum_seconds_per_run",
    }
    _allowed_keys(data, required_fields | {"parallel_workers"}, "evaluation")
    missing = required_fields - set(data)
    if missing:
        raise BenchmarkConfigError(
            f"evaluation is missing fields: {', '.join(sorted(missing))}"
        )
    if data["candidate_scope"] != "all_allowed_variants":
        raise BenchmarkConfigError(
            "evaluation.candidate_scope must be 'all_allowed_variants'"
        )
    heuristics: list[OptimizationHeuristic] = []
    for item in _list(data["heuristics"], "evaluation.heuristics"):
        value = _string(item, "evaluation.heuristics[]")
        try:
            heuristics.append(OptimizationHeuristic(value))
        except ValueError as exc:
            raise BenchmarkConfigError(
                f"unknown evaluation heuristic {value!r}"
            ) from exc
    if not heuristics or len(set(heuristics)) != len(heuristics):
        raise BenchmarkConfigError(
            "evaluation.heuristics must be non-empty and unique"
        )
    return EvaluationConfig(
        tuple(heuristics),
        _positive_integer(
            data["random_greedy_repetitions"],
            "evaluation.random_greedy_repetitions",
        ),
        _positive_number(
            data["maximum_seconds_per_run"],
            "evaluation.maximum_seconds_per_run",
        ),
        _positive_integer(
            data.get("parallel_workers", 4),
            "evaluation.parallel_workers",
        ),
    )


def _validate_library(
    library: CellLibrary,
    sources: SourcesConfig,
    search: ReferenceSearchConfig,
) -> None:
    available_sizes = {cell.size for cell in library.values()}
    missing_sizes = set(search.allowed_sizes) - available_sizes
    if missing_sizes:
        raise BenchmarkConfigError(
            f"cell library has no allowed sizes: {', '.join(sorted(missing_sizes))}"
        )
    if sources.generated is None:
        return
    for family in sources.generated.topology.family_weights:
        variants = library.variants(family)
        if not variants:
            raise BenchmarkConfigError(f"cell library has no family {family!r}")
        sizes = {cell.size for cell in variants}
        if not set(search.allowed_sizes) <= sizes:
            raise BenchmarkConfigError(
                f"family {family!r} does not support every allowed size"
            )


def _keys(data: dict[str, object], expected: set[str], context: str) -> None:
    _allowed_keys(data, expected, context)
    missing = expected - set(data)
    if missing:
        raise BenchmarkConfigError(
            f"{context} is missing fields: {', '.join(sorted(missing))}"
        )


def _allowed_keys(data: dict[str, object], allowed: set[str], context: str) -> None:
    unknown = set(data) - allowed
    if unknown:
        raise BenchmarkConfigError(
            f"{context} has unknown fields: {', '.join(sorted(unknown))}"
        )


def _object(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise BenchmarkConfigError(f"{context} must be an object")
    return cast(dict[str, object], value)


def _list(value: object, context: str) -> list[object]:
    if not isinstance(value, list):
        raise BenchmarkConfigError(f"{context} must be an array")
    return cast(list[object], value)


def _string(value: object, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise BenchmarkConfigError(f"{context} must be a non-empty string")
    return value


def _boolean(value: object, context: str) -> bool:
    if not isinstance(value, bool):
        raise BenchmarkConfigError(f"{context} must be a boolean")
    return value


def _integer(value: object, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BenchmarkConfigError(f"{context} must be an integer")
    return value


def _positive_integer(value: object, context: str) -> int:
    result = _integer(value, context)
    if result <= 0:
        raise BenchmarkConfigError(f"{context} must be positive")
    return result


def _minimum_integer(value: object, context: str, minimum: int) -> int:
    result = _integer(value, context)
    if result < minimum:
        raise BenchmarkConfigError(f"{context} must be at least {minimum}")
    return result


def _number(value: object, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BenchmarkConfigError(f"{context} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise BenchmarkConfigError(f"{context} must be finite")
    return result


def _positive_number(value: object, context: str) -> float:
    result = _number(value, context)
    if result <= 0.0:
        raise BenchmarkConfigError(f"{context} must be positive")
    return result


def _nonnegative_number(value: object, context: str) -> float:
    result = _number(value, context)
    if result < 0.0:
        raise BenchmarkConfigError(f"{context} cannot be negative")
    return result


def _probability(value: object, context: str) -> float:
    result = _number(value, context)
    if not 0.0 <= result <= 1.0:
        raise BenchmarkConfigError(f"{context} must be between 0 and 1")
    return result


def _int_range(value: object, context: str, minimum: int | None = None) -> IntRange:
    items = _list(value, context)
    if len(items) != 2:
        raise BenchmarkConfigError(f"{context} must contain exactly two values")
    result = IntRange(_integer(items[0], context), _integer(items[1], context))
    if result.minimum > result.maximum:
        raise BenchmarkConfigError(f"{context} minimum cannot exceed maximum")
    if minimum is not None and result.minimum < minimum:
        raise BenchmarkConfigError(f"{context} values must be at least {minimum}")
    return result


def _float_range(
    value: object,
    context: str,
    minimum: float | None = None,
    maximum: float | None = None,
) -> FloatRange:
    items = _list(value, context)
    if len(items) != 2:
        raise BenchmarkConfigError(f"{context} must contain exactly two values")
    result = FloatRange(_number(items[0], context), _number(items[1], context))
    if result.minimum > result.maximum:
        raise BenchmarkConfigError(f"{context} minimum cannot exceed maximum")
    if minimum is not None and result.minimum < minimum:
        raise BenchmarkConfigError(f"{context} values must be at least {minimum}")
    if maximum is not None and result.maximum > maximum:
        raise BenchmarkConfigError(f"{context} values must be at most {maximum}")
    return result


def _resolved_file(base: Path, value: object, context: str) -> Path:
    path = Path(_string(value, context))
    resolved = path if path.is_absolute() else base / path
    resolved = resolved.resolve()
    if not resolved.is_file():
        raise BenchmarkConfigError(f"{context} is not a file: {resolved}")
    return resolved


def _source_directory(
    base: Path,
    value: object,
    context: str,
    *,
    must_exist: bool,
) -> Path:
    path = Path(_string(value, context))
    resolved = (path if path.is_absolute() else base / path).resolve()
    if must_exist and not resolved.is_dir():
        raise BenchmarkConfigError(f"{context} is not a directory: {resolved}")
    return resolved


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
