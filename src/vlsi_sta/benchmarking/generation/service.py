"""Deterministic creation of repairable STA sizing benchmarks."""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import random
import shutil
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from vlsi_sta.domain.cell import Cell, CellLibrary
from vlsi_sta.domain.circuit import Circuit
from vlsi_sta.input.config import Config
from vlsi_sta.input.netlist import Gate, NetListParser, NetType
from vlsi_sta.domain.numeric import compare_floats
from vlsi_sta.analysis.sta import TimingAnalysisResult, analyze_timing
from vlsi_sta.benchmarking.config import (
    BenchmarkConfig,
    FloatRange,
    GeneratedSourceConfig,
    SeededSourceConfig,
    TopologyConfig,
)
from vlsi_sta.benchmarking.models import (
    GeneratedCase,
    GenerationFailure,
    GenerationResult,
    MutationRecord,
    SearchStatistics,
)


logger = logging.getLogger(__name__)


class BenchmarkGenerationError(RuntimeError):
    """Raised when a complete valid suite cannot be generated."""


@dataclass(frozen=True)
class _Topology:
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    gates: tuple[Gate, ...]
    cells: Mapping[str, Cell]


@dataclass(frozen=True)
class _SearchResult:
    circuit: Circuit
    timing: TimingAnalysisResult
    statistics: SearchStatistics


@dataclass(frozen=True)
class _SearchState:
    circuit: Circuit
    timing: TimingAnalysisResult
    assignment: tuple[str, ...]
    score: tuple[float, ...]


@dataclass(frozen=True)
class _ElectricalValues:
    input_arrivals: Mapping[str, tuple[float, float]]
    output_loads: Mapping[str, float]
    activity_factors: Mapping[str, float]


@dataclass(frozen=True)
class _Normalization:
    delay: float
    area: float
    power: float


CASE_HEADER = (
    "case_id", "source_type", "source_name", "random_seed", "gate_count",
    "logic_depth", "input_count", "output_count", "reference_wns_ns",
    "initial_wns_ns", "reference_area", "reference_power_uW",
    "area_headroom", "power_headroom", "mutation_count", "search_starts",
    "search_expansions", "search_unique_states", "search_elapsed_seconds",
    "search_termination",
)

FAILURE_HEADER = (
    "case_id", "source_type", "source_name", "attempts", "reason",
)

MUTATION_HEADER = (
    "gate", "reference_cell", "initial_cell", "size_steps",
    "on_reference_critical_path", "wns_delta_ns", "tns_delta_ns",
    "delay_delta_ns", "area_delta", "power_delta_uW",
)


def generate_suite(config: BenchmarkConfig) -> GenerationResult:
    """Generate a deterministic self-contained benchmark suite."""

    suite_directory = config.suite.output_root / config.suite.name
    if suite_directory.exists():
        raise BenchmarkGenerationError(
            f"benchmark suite already exists: {suite_directory}"
        )
    suite_directory.mkdir(parents=True)
    cases_directory = suite_directory / "cases"
    cases_directory.mkdir()
    _write_suite_config(config, suite_directory / "suite_config.json")
    shutil.copy2(config.cell_library_path, suite_directory / "cell_library.json")
    library = CellLibrary(suite_directory / "cell_library.json")

    requests = _case_requests(config)
    logger.info(
        "Generating benchmark suite %s with %d cases in %s",
        config.suite.name,
        len(requests),
        suite_directory,
    )
    generated: list[GeneratedCase] = []
    failures: list[GenerationFailure] = []
    for case_index, (source_type, source_name, source) in enumerate(requests, start=1):
        case_id = f"case_{case_index:04d}"
        logger.info(
            "Case %d/%d started (%s, %s)",
            case_index,
            len(requests),
            source_type,
            source_name,
        )
        last_error = "generation was not attempted"
        for attempt in range(1, config.suite.maximum_attempts_per_case + 1):
            if attempt == 1 or attempt % 10 == 0:
                logger.debug(
                    "Case %s generation attempt %d/%d",
                    case_id,
                    attempt,
                    config.suite.maximum_attempts_per_case,
                )
            seed = _derived_seed(config.suite.random_seed, case_index, attempt)
            try:
                topology = (
                    _generate_topology(source, library, config, random.Random(seed))
                    if isinstance(source, GeneratedSourceConfig)
                    else _load_seed_topology(source, library)
                )
                case = _generate_case(
                    config,
                    library,
                    topology,
                    cases_directory,
                    case_id,
                    source_type,
                    source_name,
                    seed,
                )
            except (ValueError, RuntimeError) as exc:
                last_error = str(exc)
                logger.debug(
                    "Case %s attempt %d failed: %s",
                    case_id,
                    attempt,
                    last_error,
                )
                if attempt % 10 == 0:
                    logger.warning(
                        "Case %s is still retrying after %d attempts; latest "
                        "failure: %s",
                        case_id,
                        attempt,
                        last_error,
                    )
                temporary = cases_directory / f".{case_id}.tmp"
                if temporary.exists():
                    shutil.rmtree(temporary)
                continue
            generated.append(case)
            logger.info(
                "Case %d/%d completed: %s, %d gates, depth %d, "
                "initial WNS %.6g ns",
                case_index,
                len(requests),
                case_id,
                case.gate_count,
                case.logic_depth,
                case.initial_wns_ns,
            )
            break
        else:
            logger.error(
                "Could not generate case %s after %d attempts: %s",
                case_id,
                config.suite.maximum_attempts_per_case,
                last_error,
            )
            failures.append(
                GenerationFailure(
                    case_id,
                    source_type,
                    source_name,
                    config.suite.maximum_attempts_per_case,
                    last_error,
                )
            )

    _write_csv(suite_directory / "generation_cases.csv", CASE_HEADER, (
        _case_row(case) for case in generated
    ))
    _write_csv(suite_directory / "generation_failures.csv", FAILURE_HEADER, (
        (item.case_id, item.source_type, item.source_name, item.attempts, item.reason)
        for item in failures
    ))
    _write_json(
        suite_directory / "suite_manifest.json",
        {
            "schema_version": 1,
            "suite_name": config.suite.name,
            "random_seed": config.suite.random_seed,
            "requested_cases": len(requests),
            "generated_cases": len(generated),
            "failed_cases": len(failures),
            "reference_certification": "best_known_multi_start_beam",
            "cell_library_sha256": _sha256(suite_directory / "cell_library.json"),
            "case_ids": [case.case_id for case in generated],
        },
    )
    result = GenerationResult(
        suite_directory,
        len(requests),
        tuple(generated),
        tuple(failures),
    )
    if failures:
        raise BenchmarkGenerationError(
            f"generated {len(generated)} of {len(requests)} requested cases; "
            f"see {suite_directory / 'generation_failures.csv'}"
        )
    logger.info(
        "Benchmark suite generation completed: %d/%d cases written to %s",
        len(generated),
        len(requests),
        suite_directory,
    )
    return result


def _case_requests(
    config: BenchmarkConfig,
) -> list[tuple[str, str, GeneratedSourceConfig | SeededSourceConfig]]:
    requests: list[tuple[str, str, GeneratedSourceConfig | SeededSourceConfig]] = []
    generated = config.sources.generated
    if generated is not None:
        requests.extend(
            ("generated", "structured_random", generated)
            for _ in range(generated.case_count)
        )
    for source in config.sources.seeded:
        requests.extend(
            ("seeded", source.directory.name, source)
            for _ in range(source.case_count)
        )
    return requests


def _generate_topology(
    source: GeneratedSourceConfig,
    library: CellLibrary,
    config: BenchmarkConfig,
    generator: random.Random,
) -> _Topology:
    settings = source.topology
    gate_count = generator.randint(
        settings.gate_count.minimum, settings.gate_count.maximum
    )
    level_count = generator.randint(
        settings.logic_levels.minimum, settings.logic_levels.maximum
    )
    if level_count > gate_count:
        raise ValueError("sampled logic level count exceeds sampled gate count")
    input_count = generator.randint(
        settings.primary_inputs.minimum, settings.primary_inputs.maximum
    )
    output_count = generator.randint(
        settings.primary_outputs.minimum, settings.primary_outputs.maximum
    )
    if output_count > gate_count - level_count + 1:
        raise ValueError("sampled output count cannot fit the sampled topology")
    maximum_inputs = max(
        cell.num_inputs
        for family in settings.family_weights
        for cell in library.variants(family)
        if cell.size in config.reference_search.allowed_sizes
    )
    widths = _level_widths(
        gate_count,
        level_count,
        output_count,
        input_count,
        maximum_inputs,
        settings.maximum_fanout,
    )
    inputs = tuple(f"IN{index}" for index in range(1, input_count + 1))
    fanouts = {name: 0 for name in inputs}
    levels: list[list[str]] = []
    gates: list[Gate] = []
    cells: dict[str, Cell] = {}
    gate_index = 1
    internal_index = 1
    for level_index, width in enumerate(widths):
        previous = list(inputs) if level_index == 0 else levels[-1]
        family_cells = _choose_level_cells(
            width,
            len(previous),
            settings,
            library,
            config.reference_search.allowed_sizes,
            generator,
        )
        assignments: dict[tuple[int, int], str] = {}
        unused_previous = set(previous)
        for gate_offset in range(width):
            candidates = [
                name for name in previous
                if fanouts.get(name, 0) < settings.maximum_fanout
            ]
            if not candidates:
                raise ValueError("maximum fanout cannot feed every next-level gate")
            minimum = min(fanouts.get(name, 0) for name in candidates)
            source_name = generator.choice([
                name for name in candidates if fanouts.get(name, 0) == minimum
            ])
            assignments[(gate_offset, 0)] = source_name
            fanouts[source_name] = fanouts.get(source_name, 0) + 1
            unused_previous.discard(source_name)
        remaining_slots = [
            (gate_offset, pin)
            for gate_offset, cell in enumerate(family_cells)
            for pin in range(1, cell.num_inputs)
        ]
        generator.shuffle(remaining_slots)
        for source_name in sorted(unused_previous):
            if not remaining_slots:
                raise ValueError("gate inputs cannot cover the preceding level")
            assignments[remaining_slots.pop()] = source_name
            fanouts[source_name] = fanouts.get(source_name, 0) + 1
        available_sources = list(inputs) + [name for level in levels for name in level]
        current_outputs: list[str] = []
        for offset, cell in enumerate(family_cells):
            gate_name = f"G{gate_index}"
            is_last = level_index == len(widths) - 1
            output_name = (
                f"OUT{offset + 1}" if is_last else f"N{internal_index}"
            )
            if not is_last:
                internal_index += 1
            input_names: list[str] = []
            for pin in range(cell.num_inputs):
                selected = assignments.get((offset, pin))
                if selected is None:
                    selected = _choose_source(
                        available_sources,
                        fanouts,
                        settings,
                        generator,
                    )
                    fanouts[selected] = fanouts.get(selected, 0) + 1
                input_names.append(selected)
            gates.append(
                Gate(gate_name, cell.family, tuple(input_names), output_name)
            )
            cells[gate_name] = cell
            fanouts[output_name] = 0
            current_outputs.append(output_name)
            gate_index += 1
        levels.append(current_outputs)
    outputs = tuple(levels[-1])
    return _Topology(inputs, outputs, tuple(gates), cells)


def _level_widths(
    gates: int,
    levels: int,
    outputs: int,
    inputs: int,
    maximum_inputs: int,
    maximum_fanout: int,
) -> tuple[int, ...]:
    widths = [1] * levels
    widths[-1] = outputs
    widths[0] = max(widths[0], (inputs + maximum_inputs - 1) // maximum_inputs)
    changed = True
    while changed:
        changed = False
        for index in range(levels - 2, -1, -1):
            required = (widths[index + 1] + maximum_fanout - 1) // maximum_fanout
            if widths[index] < required:
                widths[index] = required
                changed = True
        for index in range(levels - 1):
            required = (widths[index] + maximum_inputs - 1) // maximum_inputs
            if index + 1 == levels - 1 and required > outputs:
                raise ValueError("fixed output count cannot support all gates")
            if widths[index + 1] < required:
                widths[index + 1] = required
                changed = True
    if widths[0] > inputs * maximum_fanout:
        raise ValueError("sampled inputs cannot feed the first level")
    remaining = gates - sum(widths)
    if remaining < 0:
        raise ValueError("requested gates cannot fit the sampled level constraints")
    while remaining > 0:
        addable = [
            index
            for index in range(levels - 1)
            if widths[index] < maximum_inputs * widths[index + 1]
            and (
                index == 0
                or widths[index] < maximum_fanout * widths[index - 1]
            )
            and (index != 0 or widths[index] < maximum_fanout * inputs)
        ]
        if not addable:
            raise ValueError("gate count cannot be distributed across requested levels")
        index = min(addable, key=lambda item: (widths[item], item))
        widths[index] += 1
        remaining -= 1
    return tuple(widths)


def _choose_level_cells(
    width: int,
    predecessor_count: int,
    settings: TopologyConfig,
    library: CellLibrary,
    allowed_sizes: Sequence[str],
    generator: random.Random,
) -> tuple[Cell, ...]:
    families = tuple(settings.family_weights)
    weights = tuple(settings.family_weights.values())
    for _ in range(100):
        chosen_families = generator.choices(families, weights=weights, k=width)
        cells = tuple(
            min(
                (
                    _variant_for_size(library, family, size)
                    for size in allowed_sizes
                ),
                key=lambda cell: cell.size_factor,
            )
            for family in chosen_families
        )
        if sum(cell.num_inputs for cell in cells) >= max(predecessor_count, width):
            return cells
    raise ValueError("selected gate families cannot connect the preceding level")


def _choose_source(
    sources: Sequence[str],
    fanouts: Mapping[str, int],
    settings: TopologyConfig,
    generator: random.Random,
) -> str:
    available = [
        name for name in sources if fanouts.get(name, 0) < settings.maximum_fanout
    ]
    if not available:
        raise ValueError("maximum fanout prevents a complete topology")
    branched = [name for name in available if fanouts.get(name, 0) > 0]
    if branched and generator.random() < settings.branch_probability:
        return generator.choice(branched)
    if generator.random() < settings.reconvergence_probability:
        return generator.choice(available)
    minimum_fanout = min(fanouts.get(name, 0) for name in available)
    return generator.choice(
        [name for name in available if fanouts.get(name, 0) == minimum_fanout]
    )


def _load_seed_topology(
    source: SeededSourceConfig,
    library: CellLibrary,
) -> _Topology:
    Config(source.directory / "config.json")
    nets, gates, gate_cells = NetListParser(
        source.directory / "netlist.txt", library
    ).parse()
    inputs = tuple(
        name for name, net in nets.items() if net.net_type is NetType.INPUT
    )
    outputs = tuple(
        name for name, net in nets.items() if net.net_type is NetType.OUTPUT
    )
    return _Topology(inputs, outputs, tuple(gates.values()), gate_cells)


def _generate_case(
    config: BenchmarkConfig,
    library: CellLibrary,
    topology: _Topology,
    cases_directory: Path,
    case_id: str,
    source_type: str,
    source_name: str,
    seed: int,
) -> GeneratedCase:
    generator = random.Random(seed)
    case_directory = cases_directory / case_id
    temporary = cases_directory / f".{case_id}.tmp"
    if temporary.exists():
        shutil.rmtree(temporary)
    temporary.mkdir()
    netlist_path = temporary / "netlist.txt"
    config_path = temporary / "config.json"
    electrical = _electrical_values(config, topology, generator)
    _write_netlist(netlist_path, topology, topology.cells)
    loose_required = {name: (1.0e9, 1.0e9) for name in topology.outputs}
    _write_analyzer_config(
        config_path, config, case_id, topology, electrical, loose_required,
        1.0e30, 1.0e30,
    )
    base = _load_circuit(netlist_path, config_path, library)
    starts = _search_starts(base, topology.cells, config, generator)
    anchor = _beam_search(base, starts, config, mode="delay", phase="timing anchor")
    margins = {
        output: generator.uniform(
            config.electrical.required_time_margin_ns.minimum,
            config.electrical.required_time_margin_ns.maximum,
        )
        for output in topology.outputs
    }
    required = {
        output: (
            anchor.timing.arrival_times[output].rise + margins[output],
            anchor.timing.arrival_times[output].fall + margins[output],
        )
        for output in topology.outputs
    }
    provisional_area = anchor.circuit.area * (
        1.0 + config.reference_search.provisional_area_headroom
    )
    provisional_power = anchor.circuit.power * (
        1.0 + config.reference_search.provisional_power_headroom
    )
    _write_analyzer_config(
        config_path, config, case_id, topology, electrical, required,
        provisional_area, provisional_power,
    )
    provisional_base = _load_circuit(netlist_path, config_path, library)
    provisional_starts = _search_starts(
        provisional_base, anchor.circuit.gate_cells, config, generator
    )
    provisional = _beam_search(
        provisional_base,
        provisional_starts,
        config,
        mode="feasible",
        phase="provisional reference",
    )
    if compare_floats(provisional.timing.wns, 0.0) < 0:
        raise ValueError("reference search found no provisional timing-feasible state")
    area_headroom = generator.uniform(
        config.reference_search.final_area_headroom.minimum,
        config.reference_search.final_area_headroom.maximum,
    )
    power_headroom = generator.uniform(
        config.reference_search.final_power_headroom.minimum,
        config.reference_search.final_power_headroom.maximum,
    )
    maximum_area = provisional.circuit.area * (1.0 + area_headroom)
    maximum_power = provisional.circuit.power * (1.0 + power_headroom)
    _write_analyzer_config(
        config_path, config, case_id, topology, electrical, required,
        maximum_area, maximum_power,
    )
    final_base = _load_circuit(netlist_path, config_path, library)
    final_starts = _search_starts(
        final_base, provisional.circuit.gate_cells, config, generator
    )
    reference = _beam_search(
        final_base,
        final_starts,
        config,
        mode="feasible",
        phase="final reference",
    )
    if not _compliant(reference.circuit, reference.timing):
        raise ValueError("reference search found no constraint-compliant state")
    initial, _, mutations = _perturb_reference(
        reference.circuit, reference.timing, config, generator
    )
    search_statistics = _combined_search_statistics(anchor, provisional, reference)
    _write_netlist(netlist_path, topology, initial.gate_cells)
    validated_initial = _load_circuit(netlist_path, config_path, library)
    validated_timing = analyze_timing(validated_initial)
    if (
        compare_floats(validated_timing.wns, 0.0) >= 0
        or not _physical_constraints_hold(validated_initial)
    ):
        raise ValueError("serialized initial state failed benchmark invariants")

    _write_json(
        temporary / "reference_assignment.json",
        {name: cell.name for name, cell in reference.circuit.gate_cells.items()},
    )
    _write_csv(temporary / "planted_mutations.csv", MUTATION_HEADER, (
        (
            item.gate, item.reference_cell, item.initial_cell, item.size_steps,
            item.on_reference_critical_path, item.wns_delta_ns,
            item.tns_delta_ns, item.delay_delta_ns, item.area_delta,
            item.power_delta_uW,
        )
        for item in mutations
    ))
    logic_depth = len(validated_initial.topological_order)
    _write_json(
        temporary / "benchmark_manifest.json",
        {
            "schema_version": 1,
            "case_id": case_id,
            "source_type": source_type,
            "source_name": source_name,
            "random_seed": seed,
            "reference_certification": "best_known_multi_start_beam",
            "reference_search": asdict(search_statistics),
            "cell_library_sha256": _sha256(library.path),
            "topology": {
                "gates": len(topology.gates),
                "logic_depth": logic_depth,
                "primary_inputs": len(topology.inputs),
                "primary_outputs": len(topology.outputs),
                "maximum_fanout": _maximum_fanout(topology),
                "reconvergent_gate_fraction": _reconvergent_gate_fraction(topology),
            },
            "constraints": {
                "maximum_area": maximum_area,
                "maximum_power_uW": maximum_power,
            },
            "initial": _metrics(validated_initial, validated_timing),
            "reference": _metrics(reference.circuit, reference.timing),
            "planted_gates": [item.gate for item in mutations],
        },
    )
    temporary.replace(case_directory)
    return GeneratedCase(
        case_id, source_type, source_name, case_directory, seed,
        len(topology.gates), logic_depth, len(topology.inputs), len(topology.outputs),
        reference.timing.wns, validated_timing.wns, reference.circuit.area,
        reference.circuit.power, area_headroom, power_headroom, mutations,
        search_statistics,
    )


def _electrical_values(
    config: BenchmarkConfig,
    topology: _Topology,
    generator: random.Random,
) -> _ElectricalValues:
    arrivals = {
        name: (
            _sample(config.electrical.input_arrival_rise_ns, generator),
            _sample(config.electrical.input_arrival_fall_ns, generator),
        )
        for name in topology.inputs
    }
    loads = {
        name: _sample(config.electrical.output_load_fF, generator)
        for name in topology.outputs
    }
    activity: dict[str, float] = {}
    for gate in topology.gates:
        if generator.random() < config.electrical.activity_override_probability:
            activity[gate.output] = _sample(
                config.electrical.activity_override_range, generator
            )
    return _ElectricalValues(arrivals, loads, activity)


def _search_starts(
    base: Circuit,
    preferred: Mapping[str, Cell],
    config: BenchmarkConfig,
    generator: random.Random,
) -> tuple[Circuit, ...]:
    ordered_gates = tuple(base.gates.values())
    starts: list[Mapping[str, Cell]] = [preferred]
    for selector in (min, max):
        starts.append({
            gate.name: selector(
                _allowed_variants(base, gate, config),
                key=lambda cell: cell.size_factor,
            )
            for gate in ordered_gates
        })
    starts.append({
        gate.name: sorted(
            _allowed_variants(base, gate, config), key=lambda cell: cell.size_factor
        )[len(_allowed_variants(base, gate, config)) // 2]
        for gate in ordered_gates
    })
    while len(starts) < config.reference_search.restarts:
        starts.append({
            gate.name: generator.choice(_allowed_variants(base, gate, config))
            for gate in ordered_gates
        })
    circuits: list[Circuit] = []
    seen: set[tuple[str, ...]] = set()
    for assignment in starts[:max(config.reference_search.restarts, 1)]:
        circuit = _apply_assignment(base, assignment)
        key = _assignment_key(circuit)
        if key not in seen:
            circuits.append(circuit)
            seen.add(key)
    return tuple(circuits)


def _beam_search(
    base: Circuit,
    starts: Sequence[Circuit],
    config: BenchmarkConfig,
    mode: str,
    phase: str,
) -> _SearchResult:
    started = time.perf_counter()
    logger.debug(
        "Starting %s beam search with %d starts, width %d, expansion limit %d",
        phase,
        len(starts),
        config.reference_search.beam_width,
        config.reference_search.maximum_expansions,
    )
    base_timing = analyze_timing(base)
    normalization = _Normalization(
        max(base_timing.circuit_delay, 1.0e-30),
        max(base.area, 1.0e-30),
        max(base.power, 1.0e-30),
    )
    states = [
        _state(circuit, analyze_timing(circuit), normalization, config, mode)
        for circuit in starts
    ]
    states.sort(key=lambda item: item.score)
    best = states[0]
    frontier = states[:config.reference_search.beam_width]
    seen = {state.assignment for state in states}
    expansions = 0
    stagnant = 0
    termination = "frontier_exhausted"
    progress_interval = max(
        100,
        config.reference_search.maximum_expansions // 20,
    )
    next_progress = progress_interval
    while frontier and expansions < config.reference_search.maximum_expansions:
        candidates: list[_SearchState] = []
        for state in frontier:
            for neighbor in _adjacent_variants(state.circuit, config):
                key = _assignment_key(neighbor)
                if key in seen:
                    continue
                seen.add(key)
                timing = analyze_timing(neighbor)
                candidate = _state(neighbor, timing, normalization, config, mode)
                candidates.append(candidate)
                expansions += 1
                if candidate.score < best.score:
                    best = candidate
                    stagnant = 0
                else:
                    stagnant += 1
                if expansions >= next_progress:
                    logger.debug(
                        "%s search progress: %d/%d expansions, %d unique states, "
                        "best WNS %.6g ns, best delay %.6g ns",
                        phase,
                        expansions,
                        config.reference_search.maximum_expansions,
                        len(seen),
                        best.timing.wns,
                        best.timing.circuit_delay,
                    )
                    next_progress += progress_interval
                if expansions >= config.reference_search.maximum_expansions:
                    termination = "maximum_expansions"
                    break
                if stagnant >= config.reference_search.stagnation_limit:
                    termination = "stagnation_limit"
                    break
            if termination != "frontier_exhausted":
                break
        if termination != "frontier_exhausted":
            break
        candidates.sort(key=lambda item: item.score)
        frontier = candidates[:config.reference_search.beam_width]
    elapsed = time.perf_counter() - started
    logger.debug(
        "Completed %s search after %d expansions and %.3f s (%s)",
        phase,
        expansions,
        elapsed,
        termination,
    )
    return _SearchResult(
        best.circuit,
        best.timing,
        SearchStatistics(len(starts), expansions, len(seen), elapsed, termination),
    )


def _state(
    circuit: Circuit,
    timing: TimingAnalysisResult,
    normalization: _Normalization,
    config: BenchmarkConfig,
    mode: str,
) -> _SearchState:
    if mode == "delay":
        score = (timing.circuit_delay, circuit.area, circuit.power)
    else:
        constraints = circuit.config.design_constraints
        timing_violation = max(-timing.wns, 0.0) / normalization.delay
        area_excess = max(
            circuit.area - constraints.maximum_area, 0.0
        ) / max(constraints.maximum_area, 1.0e-30)
        power_excess = max(
            circuit.power - constraints.maximum_power_uW, 0.0
        ) / max(constraints.maximum_power_uW, 1.0e-30)
        weights = config.analyzer.weights
        cost = (
            weights.delay * timing.circuit_delay / normalization.delay
            + weights.area * circuit.area / normalization.area
            + weights.power * circuit.power / normalization.power
        )
        score = (timing_violation, area_excess, power_excess, cost)
    return _SearchState(circuit, timing, _assignment_key(circuit), score)


def _adjacent_variants(
    circuit: Circuit,
    config: BenchmarkConfig,
) -> Iterable[Circuit]:
    for gate in circuit.gates.values():
        variants = sorted(
            _allowed_variants(circuit, gate, config),
            key=lambda cell: cell.size_factor,
        )
        current = circuit.cell_for(gate)
        index = next(i for i, cell in enumerate(variants) if cell is current)
        for neighbor_index in (index - 1, index + 1):
            if 0 <= neighbor_index < len(variants):
                yield circuit.with_gate_cell(gate.name, variants[neighbor_index])


def _perturb_reference(
    reference: Circuit,
    reference_timing: TimingAnalysisResult,
    config: BenchmarkConfig,
    generator: random.Random,
) -> tuple[Circuit, TimingAnalysisResult, tuple[MutationRecord, ...]]:
    critical_gates = {
        step.gate.name
        for path in reference_timing.critical_paths
        for step in path.path.steps
    }
    downsizable = [
        gate for gate in reference.gates.values()
        if _size_index(reference, gate, config) > 0
    ]
    if not downsizable:
        raise ValueError("reference has no downsizable gates")
    for attempt in range(1, config.perturbation.maximum_attempts + 1):
        if attempt == 1 or attempt % 50 == 0:
            logger.debug(
                "Searching for violating perturbation: attempt %d/%d",
                attempt,
                config.perturbation.maximum_attempts,
            )
        count = min(
            len(downsizable),
            generator.randint(
                config.perturbation.changed_gate_count.minimum,
                config.perturbation.changed_gate_count.maximum,
            ),
        )
        selected: list[Gate] = []
        if config.perturbation.require_reference_path_gate:
            critical_choices = [
                gate for gate in downsizable if gate.name in critical_gates
            ]
            if not critical_choices:
                continue
            selected.append(generator.choice(critical_choices))
        remaining = [gate for gate in downsizable if gate not in selected]
        generator.shuffle(remaining)
        selected.extend(remaining[:count - len(selected)])
        initial = reference
        staged_records: list[MutationRecord] = []
        before_timing = reference_timing
        for gate in selected:
            variants = sorted(
                _allowed_variants(reference, gate, config),
                key=lambda cell: cell.size_factor,
            )
            current_index = next(
                index for index, cell in enumerate(variants)
                if cell is reference.cell_for(gate)
            )
            requested_steps = generator.randint(
                config.perturbation.steps_per_gate.minimum,
                config.perturbation.steps_per_gate.maximum,
            )
            steps = min(current_index, requested_steps)
            if steps == 0:
                continue
            changed = initial.with_gate_cell(
                gate.name, variants[current_index - steps]
            )
            changed_timing = analyze_timing(changed)
            staged_records.append(
                _mutation_record(
                    reference,
                    initial,
                    before_timing,
                    changed,
                    changed_timing,
                    gate.name,
                    steps,
                    critical_gates,
                )
            )
            initial = changed
            before_timing = changed_timing
        timing = analyze_timing(initial)
        target = config.perturbation.target_wns_ns
        if not target.minimum <= timing.wns <= target.maximum:
            continue
        if not _physical_constraints_hold(initial):
            continue
        if config.perturbation.require_critical_path_change:
            if _critical_path_ids(timing) == _critical_path_ids(reference_timing):
                continue
        records = tuple(staged_records)
        if records:
            logger.debug(
                "Accepted perturbation after %d attempts: %d gates changed, "
                "WNS %.6g ns",
                attempt,
                len(records),
                timing.wns,
            )
            return initial, timing, records
    raise ValueError("could not produce a violating perturbation in the target WNS range")


def _mutation_record(
    reference_assignment: Circuit,
    before: Circuit,
    before_timing: TimingAnalysisResult,
    after: Circuit,
    after_timing: TimingAnalysisResult,
    gate_name: str,
    steps: int,
    critical_gates: set[str],
) -> MutationRecord:
    return MutationRecord(
        gate_name,
        reference_assignment.cell_for(gate_name).name,
        after.cell_for(gate_name).name,
        steps,
        gate_name in critical_gates,
        after_timing.wns - before_timing.wns,
        after_timing.tns - before_timing.tns,
        after_timing.circuit_delay - before_timing.circuit_delay,
        after.area - before.area,
        after.power - before.power,
    )


def _write_analyzer_config(
    path: Path,
    benchmark: BenchmarkConfig,
    case_id: str,
    topology: _Topology,
    electrical: _ElectricalValues,
    required: Mapping[str, tuple[float, float]],
    maximum_area: float,
    maximum_power: float,
) -> None:
    analyzer = benchmark.analyzer
    conditions = benchmark.electrical
    _write_json(path, {
        "circuit_name": case_id,
        "cell_library": "../../cell_library.json",
        "input_arrival_times": {
            name: {"rise": value[0], "fall": value[1]}
            for name, value in electrical.input_arrivals.items()
        },
        "output_required_times": {
            name: {"rise": value[0], "fall": value[1]}
            for name, value in required.items()
        },
        "output_loads": dict(electrical.output_loads),
        "timing_analysis": {
            "top_k_paths": analyzer.top_k_paths,
            "separate_rise_fall": analyzer.separate_rise_fall,
        },
        "operating_conditions": {
            "supply_voltage": conditions.supply_voltage,
            "frequency_hz": conditions.frequency_hz,
            "temperature_c": conditions.temperature_c,
            "default_activity_factor": conditions.default_activity_factor,
            "node_activity_factors": dict(electrical.activity_factors),
        },
        "design_constraints": {
            "maximum_area": maximum_area,
            "maximum_power_uW": maximum_power,
        },
        "optimization": {
            "enabled": True,
            "allowed_sizes": list(benchmark.reference_search.allowed_sizes),
            "maximum_iterations": analyzer.maximum_iterations,
            "minimum_cost_improvement": analyzer.minimum_cost_improvement,
            "normalization_reference": "initial_design",
            "weights": asdict(analyzer.weights),
        },
        "monte_carlo": {
            "enabled": False,
            "samples": 1,
            "global_sigma": 0.0,
            "local_sigma": 0.0,
            "random_seed": benchmark.suite.random_seed,
            "negative_delay_policy": "resample",
        },
    })


def _write_netlist(
    path: Path,
    topology: _Topology,
    assignments: Mapping[str, Cell],
) -> None:
    lines = [*(f"INPUT {name}" for name in topology.inputs)]
    lines.extend(
        " ".join((gate.name, assignments[gate.name].name, *gate.inputs, gate.output))
        for gate in topology.gates
    )
    lines.extend(f"OUTPUT {name}" for name in topology.outputs)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _load_circuit(
    netlist_path: Path,
    config_path: Path,
    library: CellLibrary,
) -> Circuit:
    parsed_config = Config(config_path)
    nets, gates, cells = NetListParser(netlist_path, library).parse()
    return Circuit(nets, gates, cells, parsed_config, library)


def _apply_assignment(
    circuit: Circuit,
    assignments: Mapping[str, Cell],
) -> Circuit:
    result = circuit
    for gate_name, cell in assignments.items():
        if result.cell_for(gate_name) is not cell:
            result = result.with_gate_cell(gate_name, cell)
    return result


def _allowed_variants(
    circuit: Circuit,
    gate: Gate,
    config: BenchmarkConfig,
) -> tuple[Cell, ...]:
    allowed = set(config.reference_search.allowed_sizes)
    variants = tuple(
        cell for cell in circuit.cell_library.variants(gate.cell_family)
        if cell.size in allowed
    )
    if not variants:
        raise ValueError(f"gate {gate.name!r} has no allowed cell variants")
    return variants


def _variant_for_size(
    library: CellLibrary,
    family: str,
    size: str,
) -> Cell:
    try:
        return next(
            cell for cell in library.variants(family) if cell.size == size
        )
    except StopIteration as exc:
        raise ValueError(f"family {family!r} has no size {size!r}") from exc


def _size_index(circuit: Circuit, gate: Gate, config: BenchmarkConfig) -> int:
    variants = sorted(
        _allowed_variants(circuit, gate, config), key=lambda cell: cell.size_factor
    )
    return next(
        index for index, cell in enumerate(variants)
        if cell is circuit.cell_for(gate)
    )


def _assignment_key(circuit: Circuit) -> tuple[str, ...]:
    return tuple(circuit.cell_for(name).name for name in circuit.gates)


def _physical_constraints_hold(circuit: Circuit) -> bool:
    constraints = circuit.config.design_constraints
    return (
        compare_floats(circuit.area, constraints.maximum_area) <= 0
        and compare_floats(circuit.power, constraints.maximum_power_uW) <= 0
    )


def _compliant(circuit: Circuit, timing: TimingAnalysisResult) -> bool:
    return compare_floats(timing.wns, 0.0) >= 0 and _physical_constraints_hold(circuit)


def _critical_path_ids(timing: TimingAnalysisResult) -> tuple[tuple[str, ...], ...]:
    return tuple(
        tuple(step.gate.name for step in item.path.steps)
        for item in timing.critical_paths
    )


def _metrics(circuit: Circuit, timing: TimingAnalysisResult) -> dict[str, object]:
    return {
        "wns_ns": timing.wns,
        "tns_ns": timing.tns,
        "circuit_delay_ns": timing.circuit_delay,
        "area": circuit.area,
        "leakage_power_uW": circuit.leakage_power,
        "dynamic_power_uW": circuit.dynamic_power,
        "power_uW": circuit.power,
        "timing_compliant": compare_floats(timing.wns, 0.0) >= 0,
        "area_power_compliant": _physical_constraints_hold(circuit),
    }


def _combined_search_statistics(
    *results: _SearchResult,
) -> SearchStatistics:
    return SearchStatistics(
        starts=sum(item.statistics.starts for item in results),
        expansions=sum(item.statistics.expansions for item in results),
        unique_states=sum(item.statistics.unique_states for item in results),
        elapsed_seconds=sum(item.statistics.elapsed_seconds for item in results),
        termination=";".join(item.statistics.termination for item in results),
    )


def _maximum_fanout(topology: _Topology) -> int:
    counts: dict[str, int] = {}
    for gate in topology.gates:
        for input_net in gate.inputs:
            counts[input_net] = counts.get(input_net, 0) + 1
    return max(counts.values(), default=0)


def _reconvergent_gate_fraction(topology: _Topology) -> float:
    driver = {gate.output: gate.name for gate in topology.gates}
    ancestors: dict[str, set[str]] = {}
    reconvergent = 0
    for gate in topology.gates:
        input_ancestors: list[set[str]] = []
        for input_net in gate.inputs:
            source = driver.get(input_net)
            branch = set() if source is None else {source, *ancestors[source]}
            input_ancestors.append(branch)
        if any(
            left & right
            for index, left in enumerate(input_ancestors)
            for right in input_ancestors[index + 1:]
        ):
            reconvergent += 1
        ancestors[gate.name] = set().union(*input_ancestors)
    return reconvergent / len(topology.gates) if topology.gates else 0.0


def _case_row(case: GeneratedCase) -> tuple[object, ...]:
    return (
        case.case_id, case.source_type, case.source_name, case.random_seed,
        case.gate_count, case.logic_depth, case.input_count, case.output_count,
        case.reference_wns_ns, case.initial_wns_ns, case.reference_area,
        case.reference_power_uW, case.area_headroom, case.power_headroom,
        len(case.mutations), case.search.starts, case.search.expansions,
        case.search.unique_states, case.search.elapsed_seconds,
        case.search.termination,
    )


def _sample(bounds: FloatRange, generator: random.Random) -> float:
    return generator.uniform(bounds.minimum, bounds.maximum)


def _derived_seed(suite_seed: int, case_index: int, attempt: int) -> int:
    digest = hashlib.sha256(
        f"{suite_seed}:{case_index}:{attempt}".encode("ascii")
    ).digest()
    return int.from_bytes(digest[:8], "big")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, data: object) -> None:
    path.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_suite_config(config: BenchmarkConfig, destination: Path) -> None:
    raw = json.loads(config.path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise BenchmarkGenerationError("benchmark configuration is not an object")
    raw["cell_library"] = "cell_library.json"
    suite = raw.get("suite")
    if isinstance(suite, dict):
        suite["output_root"] = "."
    _write_json(destination, raw)


def _write_csv(
    path: Path,
    header: Sequence[str],
    rows: Iterable[Sequence[object]],
) -> None:
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(header)
        writer.writerows(rows)


__all__ = ["BenchmarkGenerationError", "GenerationResult", "generate_suite"]
