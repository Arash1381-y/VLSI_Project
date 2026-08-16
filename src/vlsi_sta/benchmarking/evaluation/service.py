"""Benchmark execution and independent gate-selection oracle."""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import math
import shutil
import signal
import statistics
import time
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import FrameType
from typing import Any, Iterator, TextIO, cast

from vlsi_sta.domain.cell import Cell, CellLibrary
from vlsi_sta.domain.circuit import Circuit, replacement_area_and_power
from vlsi_sta.input.config import Config
from vlsi_sta.input.netlist import NetListParser
from vlsi_sta.domain.numeric import compare_floats
from vlsi_sta.optimization.heuristics import OptimizationHeuristic
from vlsi_sta.optimization.optimizer import (
    CircuitOptimizer,
    OptimizationIteration,
    OptimizationResult,
)
from vlsi_sta.analysis.sta import (
    TimingAnalysisResult,
    TimingMetrics,
    analyze_timing,
    analyze_timing_metrics,
)
from vlsi_sta.benchmarking.config import BenchmarkConfig
from vlsi_sta.benchmarking.models import EvaluationResult


logger = logging.getLogger(__name__)


class BenchmarkEvaluationError(RuntimeError):
    """Raised when a suite is incomplete, corrupt, or incompatible."""


@dataclass(frozen=True)
class _Reference:
    delay: float
    area: float
    power: float


@dataclass(frozen=True)
class _Candidate:
    gate: str
    cell: str
    timing: TimingMetrics | None
    area: float
    power: float
    cost: float | None
    feasible: bool
    beneficial: bool = False
    best: bool = False


@dataclass(frozen=True)
class _Oracle:
    state_id: str
    assignment: tuple[str, ...]
    current_timing: TimingAnalysisResult
    candidates: tuple[_Candidate, ...]
    best: tuple[_Candidate, ...]


@dataclass(frozen=True)
class _Run:
    case_id: str
    source_type: str
    heuristic: OptimizationHeuristic
    repetition: int
    seed: int | None
    elapsed: float
    timed_out: bool
    error: str | None
    result: OptimizationResult | None
    repair_success: bool
    assignment_distance: int | None
    beats_reference: bool | None


@dataclass(frozen=True)
class _CaseMetadata:
    gate_count: int
    depth: int
    maximum_fanout: int
    reconvergence: float
    violation_severity: float
    constraint_headroom: float


@dataclass(frozen=True)
class _RunSummary:
    case_id: str
    source_type: str
    heuristic: OptimizationHeuristic
    elapsed: float
    timed_out: bool
    completed: bool
    repair_success: bool
    final_wns: float | None
    final_tns: float | None
    final_delay: float | None
    final_area: float | None
    final_power: float | None
    final_cost: float | None
    total_iterations: int | None
    repair_iteration: int | None
    accepted_iterations: int | None
    sta_calls: int | None
    assignment_distance: int | None
    beats_reference: bool | None


@dataclass(frozen=True)
class _CasePayload:
    case_id: str
    fragment_directory: Path
    runs: tuple[_RunSummary, ...]
    score_rows: tuple[Sequence[object], ...]
    metadata: _CaseMetadata
    planted_count: int


class _CsvSink:
    def __init__(self, path: Path, header: Sequence[str]) -> None:
        self.handle: TextIO = path.open("w", encoding="utf-8", newline="")
        self.writer: Any = csv.writer(self.handle)
        self.writer.writerow(header)

    def close(self) -> None:
        self.handle.close()


class _CsvStreams:
    """Stream large evaluation tables instead of retaining suite-wide rows."""

    def __init__(self, directory: Path) -> None:
        case_runs = _CsvSink(directory / "case_runs.csv", CASE_RUN_HEADER)
        iterations = _CsvSink(
            directory / "optimizer_iterations.csv", ITERATION_HEADER
        )
        oracle_states = _CsvSink(
            directory / "oracle_states.csv", ORACLE_STATE_HEADER
        )
        oracle_candidates = _CsvSink(
            directory / "oracle_candidates.csv", ORACLE_HEADER
        )
        scores = _CsvSink(
            directory / "gate_selection_scores.csv", SCORE_HEADER
        )
        self._sinks = (
            case_runs, iterations, oracle_states, oracle_candidates, scores,
        )
        self.case_runs = case_runs.writer
        self.iterations = iterations.writer
        self.oracle_states = oracle_states.writer
        self.oracle_candidates = oracle_candidates.writer
        self.scores = scores.writer

    def close(self) -> None:
        for sink in self._sinks:
            sink.close()

    def append_fragments(self, directory: Path) -> None:
        """Append a completed case's CSV fragments in deterministic order."""

        destinations = (
            ("case_runs.csv", self.case_runs),
            ("optimizer_iterations.csv", self.iterations),
            ("oracle_states.csv", self.oracle_states),
            ("oracle_candidates.csv", self.oracle_candidates),
            ("gate_selection_scores.csv", self.scores),
        )
        for filename, writer in destinations:
            with (directory / filename).open(encoding="utf-8", newline="") as source:
                rows = csv.reader(source)
                next(rows, None)
                writer.writerows(rows)


CASE_RUN_HEADER = (
    "case_id", "source_type", "heuristic", "repetition", "random_seed",
    "timed_out", "error", "repair_success", "final_wns_ns", "final_tns_ns",
    "final_delay_ns", "final_area", "final_power_uW", "final_cost",
    "total_iterations", "repair_iteration", "accepted_changes", "sta_calls",
    "runtime_seconds", "termination", "assignment_distance",
    "beats_reference_cost",
)

ITERATION_HEADER = (
    "case_id", "heuristic", "repetition", "iteration", "phase", "accepted",
    "gate", "previous_cell", "new_cell", "rejection_reason", "wns_ns",
    "tns_ns", "delay_ns", "area", "power_uW", "cost",
)

ORACLE_HEADER = (
    "case_id", "oracle_state_id", "gate", "cell", "feasible", "beneficial",
    "oracle_best", "wns_ns", "tns_ns", "delay_ns", "area", "power_uW",
    "cost", "on_reported_critical_path",
)

ORACLE_STATE_HEADER = (
    "case_id", "oracle_state_id", "assignment_sha256", "wns_ns", "tns_ns",
    "delay_ns", "candidate_count", "critical_path_gates",
)

SCORE_HEADER = (
    "case_id", "heuristic", "repetition", "iteration", "oracle_state_id", "selected_gate",
    "selected_cell", "accepted", "oracle_beneficial_move_exists", "gate_hit",
    "exact_move_hit", "wns_regret_ns", "tns_regret_ns", "cost_regret",
    "rejected_while_beneficial_exists", "oracle_best_outside_critical_paths",
    "selected_planted_gate", "planted_gate_count", "oracle_best_gates",
    "oracle_best_moves",
)

SUMMARY_HEADER = (
    "heuristic", "runs", "repair_successes", "repair_rate",
    "wilson_low", "wilson_high", "failure_rate", "execution_error_rate",
    "timeout_rate", "mean_final_wns_ns", "stddev_final_wns_ns",
    "mean_final_tns_ns", "mean_final_delay_ns", "mean_final_area",
    "mean_final_power_uW", "mean_final_cost", "mean_runtime_seconds",
    "runtime_stddev_seconds",
    "gate_hit_rate", "exact_move_hit_rate", "median_wns_regret_ns",
    "worst_wns_regret_ns", "median_tns_regret_ns", "worst_tns_regret_ns",
    "median_cost_regret", "worst_cost_regret", "planted_gate_precision",
    "planted_gate_recall", "mean_assignment_distance",
    "reference_cost_beat_rate", "mean_total_iterations",
    "mean_repair_iterations",
    "mean_accepted_changes", "mean_sta_calls",
)


def evaluate_suite(suite_directory: str | Path) -> EvaluationResult:
    """Evaluate every case and emit numerical and hierarchical reports."""

    suite = Path(suite_directory).resolve()
    config_path = suite / "suite_config.json"
    manifest_path = suite / "suite_manifest.json"
    if not config_path.is_file() or not manifest_path.is_file():
        raise BenchmarkEvaluationError("suite_config.json or suite_manifest.json is missing")
    config = BenchmarkConfig.load_suite(config_path)
    manifest = _json_object(manifest_path)
    library_path = suite / "cell_library.json"
    if _sha256(library_path) != manifest.get("cell_library_sha256"):
        raise BenchmarkEvaluationError("cell library digest does not match suite manifest")
    case_ids = _string_list(manifest.get("case_ids"), "suite_manifest.case_ids")
    evaluation_directory = _new_evaluation_directory(suite)
    fragment_root = evaluation_directory / ".case_fragments"
    fragment_root.mkdir()
    worker_count = min(config.evaluation.parallel_workers, len(case_ids))
    logger.info(
        "Evaluating benchmark suite %s with %d cases using %d workers; "
        "reports will be written to %s",
        suite.name,
        len(case_ids),
        worker_count,
        evaluation_directory,
    )

    reports = _CsvStreams(evaluation_directory)
    score_rows: list[Sequence[object]] = []
    runs: list[_RunSummary] = []
    case_metadata: dict[str, _CaseMetadata] = {}
    planted_counts: dict[str, int] = {}
    try:
        for case_index, case_id in enumerate(case_ids, start=1):
            logger.info("Case %d/%d started: %s", case_index, len(case_ids), case_id)
        payloads = _evaluate_cases(
            suite, evaluation_directory, case_ids, worker_count
        )
        for case_index, payload in enumerate(payloads, start=1):
            reports.append_fragments(payload.fragment_directory)
            runs.extend(payload.runs)
            score_rows.extend(payload.score_rows)
            case_metadata[payload.case_id] = payload.metadata
            planted_counts[payload.case_id] = payload.planted_count
            repaired = sum(run.repair_success for run in payload.runs)
            logger.info(
                "Case %d/%d completed: %s, %d/%d optimizer runs repaired "
                "constraints",
                case_index,
                len(case_ids),
                payload.case_id,
                repaired,
                len(payload.runs),
            )
            shutil.rmtree(payload.fragment_directory)
    finally:
        reports.close()
    fragment_root.rmdir()
    summary_rows, aggregates = _summaries(runs, score_rows, planted_counts)
    _write_csv(
        evaluation_directory / "heuristic_summary.csv", SUMMARY_HEADER, summary_rows
    )
    _write_json(evaluation_directory / "evaluation_summary.json", {
        "schema_version": 1,
        "suite": suite.name,
        "suite_manifest_sha256": _sha256(manifest_path),
        "suite_config_sha256": _sha256(config_path),
        "cell_library_sha256": _sha256(library_path),
        "case_count": len(case_ids),
        "run_count": len(runs),
        "primary_metric": "constraint_repair_success",
        "heuristics": aggregates,
        "groupings": _groupings(runs, case_metadata),
        "artifacts": {
            "case_runs": "case_runs.csv",
            "optimizer_iterations": "optimizer_iterations.csv",
            "oracle_states": "oracle_states.csv",
            "oracle_candidates": "oracle_candidates.csv",
            "gate_selection_scores": "gate_selection_scores.csv",
            "heuristic_summary": "heuristic_summary.csv",
        },
    })
    successful = sum(run.repair_success for run in runs)
    logger.info(
        "Benchmark evaluation completed: %d/%d optimizer runs repaired all "
        "constraints; reports are in %s",
        successful,
        len(runs),
        evaluation_directory,
    )
    return EvaluationResult(suite, evaluation_directory, len(runs), successful)


def _evaluate_cases(
    suite: Path,
    evaluation_directory: Path,
    case_ids: Sequence[str],
    worker_count: int,
) -> Iterator[_CasePayload]:
    """Evaluate inline for one case, or distribute independent cases."""

    if worker_count == 1:
        for case_id in case_ids:
            yield _evaluate_case_worker(
                str(suite), str(evaluation_directory), case_id
            )
        return
    with ProcessPoolExecutor(max_workers=worker_count) as executor:
        yield from executor.map(
            _evaluate_case_worker,
            [str(suite)] * len(case_ids),
            [str(evaluation_directory)] * len(case_ids),
            case_ids,
        )


def _evaluate_case_worker(
    suite_directory: str,
    evaluation_directory: str,
    case_id: str,
) -> _CasePayload:
    """Evaluate one case and write process-local report fragments."""

    suite = Path(suite_directory)
    config = BenchmarkConfig.load_suite(suite / "suite_config.json")
    library = CellLibrary(suite / "cell_library.json")
    case_directory = suite / "cases" / case_id
    case_manifest = _json_object(case_directory / "benchmark_manifest.json")
    metadata = _case_metadata(case_manifest)
    source_type = str(case_manifest.get("source_type", "unknown"))
    initial = _load_circuit(case_directory, library)
    initial_timing = analyze_timing(initial)
    normalization = _Reference(
        max(initial_timing.circuit_delay, 1.0e-15),
        max(initial.area, 1.0e-15),
        max(initial.power, 1.0e-15),
    )
    reference = _reference_circuit(case_directory, initial)
    reference_timing = analyze_timing(reference)
    if not _compliant(reference, reference_timing):
        raise BenchmarkEvaluationError(
            f"{case_id}: stored reference assignment is no longer compliant"
        )
    planted = _planted_gates(case_directory / "planted_mutations.csv")
    fragment_directory = Path(evaluation_directory) / ".case_fragments" / case_id
    fragment_directory.mkdir()
    reports = _CsvStreams(fragment_directory)
    score_rows: list[Sequence[object]] = []
    runs: list[_RunSummary] = []
    oracle_cache: dict[tuple[str, ...], _Oracle] = {}
    try:
        for heuristic in config.evaluation.heuristics:
            repetitions = (
                config.evaluation.random_greedy_repetitions
                if heuristic is OptimizationHeuristic.RANDOM_GREEDY else 1
            )
            for repetition in range(1, repetitions + 1):
                seed = (
                    _derived_seed(config.suite.random_seed, case_id, repetition)
                    if heuristic is OptimizationHeuristic.RANDOM_GREEDY else None
                )
                logger.debug(
                    "Running %s on %s (repetition %d/%d)",
                    heuristic.value,
                    case_id,
                    repetition,
                    repetitions,
                )
                run = _run_optimizer(
                    initial, reference, reference_timing, normalization, case_id,
                    source_type, heuristic, repetition, seed,
                    config.evaluation.maximum_seconds_per_run,
                )
                runs.append(_run_summary(run))
                reports.case_runs.writerow(_case_run_row(run))
                if run.result is None:
                    logger.warning(
                        "%s on %s did not complete: %s",
                        heuristic.value,
                        case_id,
                        run.error,
                    )
                    continue
                reports.iterations.writerows(
                    _iteration_rows(case_id, heuristic, repetition, run.result.history)
                )
                state_rows, candidate_rows, decision_rows = _score_history(
                    case_id, heuristic, repetition, initial, normalization,
                    run.result.history, planted, config, oracle_cache,
                )
                reports.oracle_states.writerows(state_rows)
                reports.oracle_candidates.writerows(candidate_rows)
                reports.scores.writerows(decision_rows)
                score_rows.extend(decision_rows)
    finally:
        reports.close()
    return _CasePayload(
        case_id,
        fragment_directory,
        tuple(runs),
        tuple(score_rows),
        metadata,
        len(planted),
    )


def _run_optimizer(
    initial: Circuit,
    reference: Circuit,
    reference_timing: TimingAnalysisResult,
    normalization: _Reference,
    case_id: str,
    source_type: str,
    heuristic: OptimizationHeuristic,
    repetition: int,
    seed: int | None,
    timeout: float,
) -> _Run:
    started = time.perf_counter()
    result: OptimizationResult | None = None
    error: str | None = None
    timed_out = False
    try:
        with _time_limit(timeout):
            result = CircuitOptimizer(initial, heuristic, seed).optimize()
    except TimeoutError:
        timed_out = True
        error = f"optimizer exceeded {timeout} seconds"
    except Exception as exc:  # keep one bad run from aborting the suite
        error = f"{type(exc).__name__}: {exc}"
    elapsed = time.perf_counter() - started
    if result is None:
        return _Run(
            case_id, source_type, heuristic, repetition, seed, elapsed,
            timed_out, error, None, False, None, None,
        )
    success = _compliant(result.circuit, result.timing)
    distance = _assignment_distance(result.circuit, reference)
    reference_cost = _cost(reference, reference_timing, normalization)
    beats_reference = success and compare_floats(result.cost, reference_cost) < 0
    return _Run(
        case_id, source_type, heuristic, repetition, seed, elapsed, False,
        error, result, success, distance, beats_reference,
    )


def _score_history(
    case_id: str,
    heuristic: OptimizationHeuristic,
    repetition: int,
    initial: Circuit,
    normalization: _Reference,
    history: Sequence[OptimizationIteration],
    planted: set[str],
    config: BenchmarkConfig,
    oracle_cache: dict[tuple[str, ...], _Oracle],
) -> tuple[
    list[Sequence[object]],
    list[Sequence[object]],
    list[Sequence[object]],
]:
    state_rows: list[Sequence[object]] = []
    candidate_rows: list[Sequence[object]] = []
    decision_rows: list[Sequence[object]] = []
    current = initial
    scored_entries = [
        entry
        for entry in history
        if entry.iteration != 0
        and entry.changed_gate is not None
        and entry.new_cell is not None
    ]
    for score_index, entry in enumerate(scored_entries, start=1):
        if entry.changed_gate is None or entry.new_cell is None:
            raise AssertionError("scored optimizer entry has no sizing move")
        logger.debug(
            "Oracle replay for %s/%s repetition %d: decision %d/%d "
            "(iteration %d)",
            case_id,
            heuristic.value,
            repetition,
            score_index,
            len(scored_entries),
            entry.iteration,
        )
        assignment = _assignment_key(current)
        oracle = oracle_cache.get(assignment)
        if oracle is None:
            current_timing = analyze_timing(current)
            oracle = _oracle(
                current,
                current_timing,
                normalization,
                config,
                state_id=f"{case_id}:state_{len(oracle_cache) + 1:04d}",
            )
            oracle_cache[assignment] = oracle
            critical_gates = _critical_path_gates(oracle.current_timing)
            state_rows.append((
                case_id,
                oracle.state_id,
                _assignment_digest(assignment),
                oracle.current_timing.wns,
                oracle.current_timing.tns,
                oracle.current_timing.circuit_delay,
                len(oracle.candidates),
                ";".join(sorted(critical_gates)),
            ))
            candidate_rows.extend(
                _oracle_candidate_rows(case_id, oracle, critical_gates)
            )
        current_timing = oracle.current_timing
        critical_gates = {
            gate.name
            for path in current_timing.critical_paths
            for gate in path.path.gates
        }
        best_gates = {item.gate for item in oracle.best}
        best_moves = {(item.gate, item.cell) for item in oracle.best}
        selected = next(
            (
                item for item in oracle.candidates
                if item.gate == entry.changed_gate and item.cell == entry.new_cell
            ),
            None,
        )
        best = oracle.best[0] if oracle.best else None
        wns_regret = (
            _timing(best).wns - _timing(selected).wns
            if best is not None and selected is not None and selected.feasible
            else None
        )
        tns_regret = (
            _timing(best).tns - _timing(selected).tns
            if best is not None and selected is not None and selected.feasible
            and compare_floats(_timing(best).wns, _timing(selected).wns) == 0
            else None
        )
        cost_regret = (
            _candidate_cost(selected) - _candidate_cost(best)
            if best is not None and selected is not None and selected.feasible
            and compare_floats(_timing(best).wns, _timing(selected).wns) == 0
            and compare_floats(_timing(best).tns, _timing(selected).tns) == 0
            else None
        )
        beneficial_exists = bool(oracle.best)
        decision_rows.append((
            case_id, heuristic.value, repetition, entry.iteration,
            oracle.state_id,
            entry.changed_gate, entry.new_cell, entry.accepted,
            beneficial_exists, entry.changed_gate in best_gates,
            (entry.changed_gate, entry.new_cell) in best_moves,
            wns_regret, tns_regret, cost_regret,
            entry.accepted is False and beneficial_exists,
            any(item.gate not in critical_gates for item in oracle.best),
            entry.changed_gate in planted,
            len(planted),
            ";".join(sorted(best_gates)),
            ";".join(f"{gate}:{cell}" for gate, cell in sorted(best_moves)),
        ))
        if entry.accepted is True:
            if selected is None:
                raise BenchmarkEvaluationError(
                    f"{case_id}: optimizer history contains an unavailable move"
                )
            _verify_history_state(case_id, entry, selected)
            current = current.with_gate_cell(
                entry.changed_gate,
                current.cell_library[entry.new_cell],
            )
    return state_rows, candidate_rows, decision_rows


def _oracle(
    current: Circuit,
    timing: TimingAnalysisResult,
    normalization: _Reference,
    config: BenchmarkConfig,
    state_id: str,
) -> _Oracle:
    allowed = set(config.reference_search.allowed_sizes)
    candidates: list[_Candidate] = []
    constraints = current.config.design_constraints
    gates = tuple(current.gates.values())
    progress_interval = max(10, len(gates) // 10)
    for gate_index, gate in enumerate(gates, start=1):
        current_cell = current.cell_for(gate)
        for cell in current.cell_library.variants(current_cell.family):
            if cell.size not in allowed or cell.name == current_cell.name:
                continue
            area, power = replacement_area_and_power(current, gate.name, cell)
            feasible = (
                compare_floats(area, constraints.maximum_area) <= 0
                and compare_floats(power, constraints.maximum_power_uW) <= 0
            )
            if not feasible:
                candidates.append(_Candidate(
                    gate.name, cell.name, None, area, power, None, False,
                ))
                continue
            changed = current.with_gate_cell(gate.name, cell)
            changed_timing = analyze_timing_metrics(changed)
            candidates.append(_Candidate(
                gate.name, cell.name, changed_timing, area, power,
                _cost(changed, changed_timing, normalization), True,
            ))
        if gate_index % progress_interval == 0 and gate_index < len(gates):
            logger.debug(
                "Oracle candidate progress: %d/%d gates evaluated (%d variants)",
                gate_index,
                len(gates),
                len(candidates),
            )
    feasible_candidates = [item for item in candidates if item.feasible]
    eligible: list[_Candidate]
    if compare_floats(timing.wns, 0.0) < 0:
        eligible = [
            item for item in feasible_candidates
            if compare_floats(_timing(item).wns, timing.wns) > 0
        ]
        if not eligible:
            eligible = [
                item for item in feasible_candidates
                if compare_floats(_timing(item).wns, timing.wns) == 0
                and compare_floats(_timing(item).tns, timing.tns) > 0
            ]
        best = _best_timing_candidates(eligible)
    else:
        eligible = [
            item for item in feasible_candidates
            if compare_floats(_timing(item).wns, 0.0) >= 0
            and compare_floats(
                _candidate_cost(item), _cost(current, timing, normalization)
            ) < 0
        ]
        best = _minimum_cost_candidates(eligible)
    best_moves = {(item.gate, item.cell) for item in best}
    eligible_moves = {(item.gate, item.cell) for item in eligible}
    marked = tuple(
        _Candidate(
            item.gate, item.cell, item.timing, item.area, item.power, item.cost,
            item.feasible, (item.gate, item.cell) in eligible_moves,
            (item.gate, item.cell) in best_moves,
        )
        for item in candidates
    )
    marked_best = tuple(item for item in marked if item.best)
    return _Oracle(
        state_id,
        _assignment_key(current),
        timing,
        marked,
        marked_best,
    )


def _best_timing_candidates(candidates: Sequence[_Candidate]) -> tuple[_Candidate, ...]:
    if not candidates:
        return ()
    best_wns = max(_timing(item).wns for item in candidates)
    wns_tied = [
        item for item in candidates
        if compare_floats(_timing(item).wns, best_wns) == 0
    ]
    best_tns = max(_timing(item).tns for item in wns_tied)
    timing_tied = [
        item for item in wns_tied
        if compare_floats(_timing(item).tns, best_tns) == 0
    ]
    return _minimum_cost_candidates(timing_tied)


def _minimum_cost_candidates(candidates: Sequence[_Candidate]) -> tuple[_Candidate, ...]:
    if not candidates:
        return ()
    best_cost = min(_candidate_cost(item) for item in candidates)
    return tuple(
        item for item in candidates
        if compare_floats(_candidate_cost(item), best_cost) == 0
    )


def _timing(candidate: _Candidate) -> TimingMetrics:
    if candidate.timing is None:
        raise BenchmarkEvaluationError("infeasible oracle candidate has no timing")
    return candidate.timing


def _candidate_cost(candidate: _Candidate) -> float:
    if candidate.cost is None:
        raise BenchmarkEvaluationError("infeasible oracle candidate has no cost")
    return candidate.cost


def _critical_path_gates(timing: TimingAnalysisResult) -> set[str]:
    return {
        gate.name
        for path in timing.critical_paths
        for gate in path.path.gates
    }


def _oracle_candidate_rows(
    case_id: str,
    oracle: _Oracle,
    critical_gates: set[str],
) -> list[Sequence[object]]:
    return [
        (
            case_id,
            oracle.state_id,
            candidate.gate,
            candidate.cell,
            candidate.feasible,
            candidate.beneficial,
            candidate.best,
            None if candidate.timing is None else candidate.timing.wns,
            None if candidate.timing is None else candidate.timing.tns,
            None if candidate.timing is None else candidate.timing.circuit_delay,
            candidate.area,
            candidate.power,
            candidate.cost,
            candidate.gate in critical_gates,
        )
        for candidate in oracle.candidates
    ]


def _assignment_key(circuit: Circuit) -> tuple[str, ...]:
    return tuple(circuit.cell_for(name).name for name in circuit.gates)


def _assignment_digest(assignment: Sequence[str]) -> str:
    return hashlib.sha256("\0".join(assignment).encode("utf-8")).hexdigest()


def _cost(
    circuit: Circuit,
    timing: TimingAnalysisResult | TimingMetrics,
    reference: _Reference,
) -> float:
    weights = circuit.config.optimization.weights
    return (
        weights.delay * timing.circuit_delay / reference.delay
        + weights.power * circuit.power / reference.power
        + weights.area * circuit.area / reference.area
        + weights.timing_violation * max(-timing.wns, 0.0) / reference.delay
    )


def _verify_history_state(
    case_id: str,
    entry: OptimizationIteration,
    candidate: _Candidate,
) -> None:
    timing = _timing(candidate)
    values = (
        (entry.wns, timing.wns, "WNS"),
        (entry.tns, timing.tns, "TNS"),
        (entry.circuit_delay, timing.circuit_delay, "delay"),
        (entry.area, candidate.area, "area"),
        (entry.power, candidate.power, "power"),
        (entry.cost, _candidate_cost(candidate), "cost"),
    )
    for recorded, replayed, label in values:
        if compare_floats(recorded, replayed) != 0:
            raise BenchmarkEvaluationError(
                f"{case_id}: replayed {label} differs at iteration {entry.iteration}"
            )


def _reference_circuit(case_directory: Path, initial: Circuit) -> Circuit:
    assignment = _json_object(case_directory / "reference_assignment.json")
    if set(assignment) != set(initial.gates):
        raise BenchmarkEvaluationError("reference assignment gate set is incomplete")
    circuit = initial
    for gate_name in initial.gates:
        cell_name = assignment[gate_name]
        if not isinstance(cell_name, str):
            raise BenchmarkEvaluationError("reference cell names must be strings")
        try:
            cell = initial.cell_library[cell_name]
        except KeyError as exc:
            raise BenchmarkEvaluationError(
                f"reference uses unknown cell {cell_name!r}"
            ) from exc
        if cell.family != initial.cell_for(gate_name).family:
            raise BenchmarkEvaluationError(
                f"reference changes the family of gate {gate_name!r}"
            )
        circuit = circuit.with_gate_cell(gate_name, cell)
    return circuit


def _load_circuit(case_directory: Path, library: CellLibrary) -> Circuit:
    config = Config(case_directory / "config.json")
    nets, gates, cells = NetListParser(
        case_directory / "netlist.txt", library
    ).parse()
    return Circuit(nets, gates, cells, config, library)


def _compliant(circuit: Circuit, timing: TimingAnalysisResult) -> bool:
    constraints = circuit.config.design_constraints
    return (
        compare_floats(timing.wns, 0.0) >= 0
        and compare_floats(circuit.area, constraints.maximum_area) <= 0
        and compare_floats(circuit.power, constraints.maximum_power_uW) <= 0
    )


def _assignment_distance(candidate: Circuit, reference: Circuit) -> int:
    distance = 0
    for gate in candidate.gates.values():
        variants = sorted(
            candidate.cell_library.variants(candidate.cell_for(gate).family),
            key=lambda cell: (cell.size_factor, cell.name),
        )
        positions = {cell.name: index for index, cell in enumerate(variants)}
        distance += abs(
            positions[candidate.cell_for(gate).name]
            - positions[reference.cell_for(gate).name]
        )
    return distance


def _case_run_row(run: _Run) -> Sequence[object]:
    result = run.result
    return (
        run.case_id, run.source_type, run.heuristic.value, run.repetition,
        run.seed, run.timed_out, run.error, run.repair_success,
        None if result is None else result.timing.wns,
        None if result is None else result.timing.tns,
        None if result is None else result.timing.circuit_delay,
        None if result is None else result.circuit.area,
        None if result is None else result.circuit.power,
        None if result is None else result.cost,
        None if result is None else result.total_iterations,
        None if result is None else _repair_iteration(result),
        None if result is None else result.accepted_iterations,
        None if result is None else result.sta_calls,
        run.elapsed,
        None if result is None else result.termination.value,
        run.assignment_distance, run.beats_reference,
    )


def _run_summary(run: _Run) -> _RunSummary:
    result = run.result
    return _RunSummary(
        case_id=run.case_id,
        source_type=run.source_type,
        heuristic=run.heuristic,
        elapsed=run.elapsed,
        timed_out=run.timed_out,
        completed=result is not None,
        repair_success=run.repair_success,
        final_wns=None if result is None else result.timing.wns,
        final_tns=None if result is None else result.timing.tns,
        final_delay=None if result is None else result.timing.circuit_delay,
        final_area=None if result is None else result.circuit.area,
        final_power=None if result is None else result.circuit.power,
        final_cost=None if result is None else result.cost,
        total_iterations=None if result is None else result.total_iterations,
        repair_iteration=None if result is None else _repair_iteration(result),
        accepted_iterations=None if result is None else result.accepted_iterations,
        sta_calls=None if result is None else result.sta_calls,
        assignment_distance=run.assignment_distance,
        beats_reference=run.beats_reference,
    )


def _repair_iteration(result: OptimizationResult) -> int | None:
    return next(
        (
            entry.iteration
            for entry in result.history
            if compare_floats(entry.wns, 0.0) >= 0
        ),
        None,
    )


def _iteration_rows(
    case_id: str,
    heuristic: OptimizationHeuristic,
    repetition: int,
    history: Sequence[OptimizationIteration],
) -> Iterable[Sequence[object]]:
    return (
        (
            case_id, heuristic.value, repetition, item.iteration,
            item.phase.value, item.accepted, item.changed_gate,
            item.previous_cell, item.new_cell, item.rejection_reason,
            item.wns, item.tns, item.circuit_delay, item.area, item.power,
            item.cost,
        )
        for item in history
    )


def _summaries(
    runs: Sequence[_RunSummary],
    score_rows: Sequence[Sequence[object]],
    planted_counts: Mapping[str, int],
) -> tuple[list[Sequence[object]], dict[str, object]]:
    by_heuristic: dict[str, list[_RunSummary]] = defaultdict(list)
    for run in runs:
        by_heuristic[run.heuristic.value].append(run)
    scores: dict[str, list[Sequence[object]]] = defaultdict(list)
    for row in score_rows:
        scores[str(row[1])].append(row)
    rows: list[Sequence[object]] = []
    aggregates: dict[str, object] = {}
    for heuristic, items in sorted(by_heuristic.items()):
        count = len(items)
        successes = sum(item.repair_success for item in items)
        low, high = _wilson(successes, count)
        completed = [item for item in items if item.completed]
        runtimes = [item.elapsed for item in items]
        decision_rows = scores[heuristic]
        gate_hits = [bool(item[9]) for item in decision_rows]
        move_hits = [bool(item[10]) for item in decision_rows]
        regrets = [
            float(value)
            for item in decision_rows
            if isinstance((value := item[11]), (int, float))
            and not isinstance(value, bool)
        ]
        tns_regrets = _numeric_column(decision_rows, 12)
        cost_regrets = _numeric_column(decision_rows, 13)
        accepted_rows = [item for item in decision_rows if item[7] is True]
        distinct_planted_hits = {
            (str(item[0]), int(cast(int, item[2])), str(item[5]))
            for item in accepted_rows if item[16] is True
        }
        planted_hit_moves = sum(item[16] is True for item in accepted_rows)
        planted_denominator = sum(planted_counts[item.case_id] for item in items)
        repair_rate = successes / count
        final_wns = [
            item.final_wns for item in completed if item.final_wns is not None
        ]
        gate_rate = _mean([float(item) for item in gate_hits])
        move_rate = _mean([float(item) for item in move_hits])
        median_regret = statistics.median(regrets) if regrets else None
        worst_regret = max(regrets) if regrets else None
        successful_completed = [item for item in completed if item.repair_success]
        row = (
            heuristic, count, successes, repair_rate, low, high,
            (count - successes) / count,
            sum(not item.completed and not item.timed_out for item in items) / count,
            sum(item.timed_out for item in items) / count,
            _mean(final_wns), _stddev(final_wns),
            _mean([item.final_tns for item in completed if item.final_tns is not None]),
            _mean([
                item.final_delay for item in completed if item.final_delay is not None
            ]),
            _mean([item.final_area for item in completed if item.final_area is not None]),
            _mean([
                item.final_power for item in completed if item.final_power is not None
            ]),
            _mean([item.final_cost for item in completed if item.final_cost is not None]),
            _mean(runtimes), _stddev(runtimes), gate_rate, move_rate,
            median_regret, worst_regret,
            statistics.median(tns_regrets) if tns_regrets else None,
            max(tns_regrets) if tns_regrets else None,
            statistics.median(cost_regrets) if cost_regrets else None,
            max(cost_regrets) if cost_regrets else None,
            planted_hit_moves / len(accepted_rows) if accepted_rows else None,
            len(distinct_planted_hits) / planted_denominator
            if planted_denominator else None,
            _mean([
                float(item.assignment_distance)
                for item in completed if item.assignment_distance is not None
            ]),
            _mean([
                float(item.beats_reference)
                for item in successful_completed if item.beats_reference is not None
            ]),
            _mean([
                float(item.total_iterations)
                for item in completed if item.total_iterations is not None
            ]),
            _mean([
                float(item.repair_iteration)
                for item in successful_completed
                if item.repair_iteration is not None
            ]),
            _mean([
                float(item.accepted_iterations)
                for item in completed if item.accepted_iterations is not None
            ]),
            _mean([
                float(item.sta_calls)
                for item in completed if item.sta_calls is not None
            ]),
        )
        rows.append(row)
        aggregates[heuristic] = dict(zip(SUMMARY_HEADER[1:], row[1:]))
    return rows, aggregates


def _groupings(
    runs: Sequence[_RunSummary],
    metadata: Mapping[str, _CaseMetadata],
) -> dict[str, object]:
    dimensions = {
        "source_type": lambda run, _item: run.source_type,
        "gate_count": lambda _run, item: _bucket(
            item.gate_count, ((50, "small"), (100, "medium")), "large"
        ),
        "depth": lambda _run, item: _bucket(
            item.depth, ((8, "shallow"), (16, "medium")), "deep"
        ),
        "maximum_fanout": lambda _run, item: _bucket(
            item.maximum_fanout, ((3, "low"), (6, "medium")), "high"
        ),
        "reconvergence": lambda _run, item: _bucket(
            item.reconvergence, ((0.01, "none"), (0.25, "low"), (0.5, "medium")), "high"
        ),
        "violation_severity": lambda _run, item: _bucket(
            item.violation_severity,
            ((0.01, "mild"), (0.05, "moderate")),
            "severe",
        ),
        "constraint_headroom": lambda _run, item: _bucket(
            item.constraint_headroom,
            ((0.05, "tight"), (0.15, "moderate")),
            "loose",
        ),
    }
    result: dict[str, object] = {}
    for dimension, classifier in dimensions.items():
        grouped: dict[str, list[_RunSummary]] = defaultdict(list)
        for run in runs:
            group = classifier(run, metadata[run.case_id])
            grouped[f"{group}:{run.heuristic.value}"].append(run)
        result[dimension] = {
            key: {
                "runs": len(items),
                "repair_rate": sum(item.repair_success for item in items) / len(items),
                "mean_runtime_seconds": _mean([item.elapsed for item in items]),
            }
            for key, items in sorted(grouped.items())
        }
    return result


def _case_metadata(manifest: Mapping[str, object]) -> _CaseMetadata:
    topology = _mapping(manifest.get("topology"), "manifest.topology")
    initial = _mapping(manifest.get("initial"), "manifest.initial")
    constraints = _mapping(manifest.get("constraints"), "manifest.constraints")
    initial_area = _float(initial.get("area"), "manifest.initial.area")
    initial_power = _float(initial.get("power_uW"), "manifest.initial.power_uW")
    area_limit = _float(
        constraints.get("maximum_area"), "manifest.constraints.maximum_area"
    )
    power_limit = _float(
        constraints.get("maximum_power_uW"),
        "manifest.constraints.maximum_power_uW",
    )
    return _CaseMetadata(
        _int(topology.get("gates"), "manifest.topology.gates"),
        _int(topology.get("logic_depth"), "manifest.topology.logic_depth"),
        _int(
            topology.get("maximum_fanout"),
            "manifest.topology.maximum_fanout",
        ),
        _float(
            topology.get("reconvergent_gate_fraction"),
            "manifest.topology.reconvergent_gate_fraction",
        ),
        abs(_float(initial.get("wns_ns"), "manifest.initial.wns_ns")),
        min(
            (area_limit - initial_area) / max(initial_area, 1.0e-30),
            (power_limit - initial_power) / max(initial_power, 1.0e-30),
        ),
    )


def _bucket(
    value: float,
    thresholds: Sequence[tuple[float, str]],
    fallback: str,
) -> str:
    return next((label for limit, label in thresholds if value < limit), fallback)


def _wilson(successes: int, count: int) -> tuple[float, float]:
    if count == 0:
        return 0.0, 0.0
    z = 1.959963984540054
    proportion = successes / count
    denominator = 1.0 + z * z / count
    center = (proportion + z * z / (2.0 * count)) / denominator
    margin = z * math.sqrt(
        proportion * (1.0 - proportion) / count + z * z / (4.0 * count * count)
    ) / denominator
    return center - margin, center + margin


@contextmanager
def _time_limit(seconds: float) -> Iterator[None]:
    if not hasattr(signal, "setitimer"):
        yield
        return

    def expired(_signum: int, _frame: FrameType | None) -> None:
        raise TimeoutError

    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, previous)


def _new_evaluation_directory(suite: Path) -> Path:
    root = suite / "evaluation"
    root.mkdir(exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    result = root / run_id
    result.mkdir()
    return result


def _planted_gates(path: Path) -> set[str]:
    with path.open(encoding="utf-8", newline="") as source:
        return {row["gate"] for row in csv.DictReader(source)}


def _derived_seed(suite_seed: int, case_id: str, repetition: int) -> int:
    digest = hashlib.sha256(
        f"{suite_seed}:{case_id}:{repetition}".encode("ascii")
    ).digest()
    return int.from_bytes(digest[:8], "big")


def _json_object(path: Path) -> dict[str, object]:
    try:
        value: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BenchmarkEvaluationError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise BenchmarkEvaluationError(f"{path} must contain a JSON object")
    return cast(dict[str, object], value)


def _string_list(value: object, context: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise BenchmarkEvaluationError(f"{context} must be an array of strings")
    return tuple(cast(list[str], value))


def _mapping(value: object, context: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise BenchmarkEvaluationError(f"{context} must be an object")
    return cast(dict[str, object], value)


def _float(value: object, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BenchmarkEvaluationError(f"{context} must be numeric")
    return float(value)


def _int(value: object, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BenchmarkEvaluationError(f"{context} must be an integer")
    return value


def _mean(values: Sequence[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _numeric_column(rows: Sequence[Sequence[object]], index: int) -> list[float]:
    return [
        float(value)
        for row in rows
        if isinstance((value := row[index]), (int, float))
        and not isinstance(value, bool)
    ]


def _stddev(values: Sequence[float]) -> float:
    return statistics.stdev(values) if len(values) > 1 else 0.0


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_csv(
    path: Path,
    header: Sequence[str],
    rows: Iterable[Sequence[object]],
) -> None:
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(header)
        writer.writerows(rows)


__all__ = ["BenchmarkEvaluationError", "EvaluationResult", "evaluate_suite"]
