"""Timing-first iterative gate-sizing optimization."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum
from random import Random

from .cell import Cell
from .circuit import Circuit, replace_gate_cell, replacement_area_and_power
from .logical_effort import OptimizationChoice, get_optimization_candidates
from .netlist import Gate
from .numeric import compare_floats
from .optimization_heuristics import (
    OptimizationHeuristic,
    iter_criticality_effort_gap_choices,
    iter_ranked_optimization_choices,
)
from .sta import TimingAnalysisResult, analyze_timing


logger = logging.getLogger(__name__)

class OptimizationTermination(str, Enum):
    DISABLED = "disabled"
    TIMING_MET_NO_COST_IMPROVEMENT = "timing_met_no_cost_improvement"
    NO_TIMING_IMPROVEMENT = "no_timing_improvement"
    NO_FEASIBLE_CHANGE = "no_feasible_change"
    MAXIMUM_ITERATIONS = "maximum_iterations"


class OptimizationPhase(str, Enum):
    INITIAL = "initial"
    TIMING_REPAIR = "timing_repair"
    COST_REDUCTION = "cost_reduction"
    REJECTED = "rejected"


@dataclass(frozen=True)
class OptimizationIteration:
    iteration: int
    phase: OptimizationPhase
    accepted: bool | None
    changed_gate: str | None
    previous_cell: str | None
    new_cell: str | None
    rejection_reason: str | None
    wns: float
    tns: float
    circuit_delay: float
    area: float
    power: float
    cost: float


@dataclass(frozen=True)
class OptimizationResult:
    circuit: Circuit
    timing: TimingAnalysisResult
    cost: float
    total_iterations: int
    accepted_iterations: int
    termination: OptimizationTermination
    heuristic: OptimizationHeuristic
    random_seed: int | None
    sta_calls: int
    history: tuple[OptimizationIteration, ...]


@dataclass(frozen=True)
class _Evaluation:
    circuit: Circuit
    timing: TimingAnalysisResult
    cost: float


@dataclass(frozen=True)
class _NormalizationReference:
    delay: float
    power: float
    area: float


@dataclass(frozen=True)
class _CandidateEvaluation:
    evaluation: _Evaluation
    gate_name: str
    gate_position: int
    size_factor: float
    cell_name: str


@dataclass(frozen=True)
class _AcceptedChange:
    candidate: _CandidateEvaluation
    phase: OptimizationPhase


@dataclass(frozen=True)
class _StopDecision:
    termination: OptimizationTermination
    message: str


@dataclass(frozen=True)
class _RejectedChange:
    gate_name: str
    cell_name: str
    message: str


_OptimizationDecision = _AcceptedChange | _RejectedChange | _StopDecision
_SizingChoice = tuple[Gate, Cell]


class CircuitOptimizer:
    """Optimize immutable circuits using exhaustive or heuristic local search."""

    def __init__(
        self,
        circuit: Circuit,
        heuristic: OptimizationHeuristic = OptimizationHeuristic.BRUTE_FORCE,
        random_seed: int | None = None,
    ) -> None:
        self.initial_circuit = circuit
        self.config = circuit.config
        self.heuristic = heuristic
        self.random_seed = random_seed
        self._sta_calls = 0

    def optimize(self) -> OptimizationResult:
        """Repair timing first, then reduce cost without violating timing."""

        current, reference, history = self._initial_state()
        random_generator = Random(self.random_seed)
        choice_iterator: Iterator[_SizingChoice] | None = None
        optimization = self.config.optimization
        if not optimization.enabled:
            logger.info("Circuit optimization is disabled")
            return self._result(current, OptimizationTermination.DISABLED, history)

        logger.info(
            "Starting circuit optimization with initial cost %.6g",
            current.cost,
        )
        for attempt in range(1, optimization.maximum_iterations + 1):
            logger.info(
                "Optimization attempt %d/%d",
                attempt,
                optimization.maximum_iterations,
            )
            if choice_iterator is None:
                choice_iterator = self._choice_iterator(
                    current,
                    random_generator,
                )
            decision = self._choose_next_change(
                current,
                reference,
                choice_iterator,
            )
            if isinstance(decision, _RejectedChange):
                self._record_rejection(history, current, attempt, decision)
                continue
            if isinstance(decision, _StopDecision):
                stop_iteration = (
                    attempt
                    if self.heuristic is OptimizationHeuristic.BRUTE_FORCE
                    else attempt - 1
                )
                return self._stop(
                    current,
                    history,
                    stop_iteration,
                    decision,
                )

            self._log_accepted_change(current, decision)
            current = self._accept_change(current, decision, history, attempt)
            choice_iterator = None

        logger.info(
            "Optimization stopped after reaching the maximum of %d iterations",
            optimization.maximum_iterations,
        )
        return self._result(
            current,
            OptimizationTermination.MAXIMUM_ITERATIONS,
            history,
        )

    def _initial_state(
        self,
    ) -> tuple[_Evaluation, _NormalizationReference, list[OptimizationIteration]]:
        self._sta_calls = 0
        initial_timing = self._analyze(self.initial_circuit)
        reference = self._normalization_reference(
            self.initial_circuit,
            initial_timing,
        )
        current = self._evaluate(
            self.initial_circuit,
            reference,
            initial_timing,
        )
        history = [
            self._history_entry(
                current,
                iteration=0,
                phase=OptimizationPhase.INITIAL,
            )
        ]
        return current, reference, history

    def _choose_next_change(
        self,
        current: _Evaluation,
        reference: _NormalizationReference,
        choice_iterator: Iterator[_SizingChoice],
    ) -> _OptimizationDecision:
        if self.heuristic is OptimizationHeuristic.BRUTE_FORCE:
            choices = tuple(choice_iterator)
            candidates = self._candidate_evaluations(
                current,
                reference,
                choices,
            )
            return self._select_next_change(current, candidates)

        try:
            gate, candidate_cell = next(choice_iterator)
        except StopIteration:
            return _StopDecision(
                OptimizationTermination.NO_FEASIBLE_CHANGE,
                "all critical-path sizing choices were exhausted",
            )
        return self._evaluate_single_choice(
            current,
            reference,
            gate,
            candidate_cell,
        )

    def _choice_iterator(
        self,
        current: _Evaluation,
        random_generator: Random,
    ) -> Iterator[_SizingChoice]:
        if self.heuristic is OptimizationHeuristic.RANDOM_GREEDY:
            choices = self._critical_path_one_step_choices(current)
            random_generator.shuffle(choices)
            return iter(choices)

        if self.heuristic is OptimizationHeuristic.CRITICALITY_EFFORT_GAP:
            return iter_criticality_effort_gap_choices(
                current.circuit,
                current.timing.critical_paths,
                self._critical_path_one_step_choices(current),
            )

        eligible = self._eligible_choices(
            current,
            set(self.config.optimization.allowed_sizes),
        )
        if self.heuristic is OptimizationHeuristic.BRUTE_FORCE:
            return (
                (gate, cell)
                for gate, candidate_cells in eligible
                for cell in candidate_cells
            )
        return iter_ranked_optimization_choices(
            current.circuit,
            current.timing.critical_paths,
            eligible,
        )

    def _record_rejection(
        self,
        history: list[OptimizationIteration],
        current: _Evaluation,
        iteration: int,
        decision: _RejectedChange,
    ) -> None:
        logger.debug("Optimization attempt rejected: %s", decision.message)
        history.append(
            self._history_entry(
                current,
                iteration=iteration,
                phase=OptimizationPhase.REJECTED,
                accepted=False,
                changed_gate=decision.gate_name,
                previous_cell=current.circuit.cell_for(decision.gate_name).name,
                new_cell=decision.cell_name,
                rejection_reason=decision.message,
            )
        )

    def _stop(
        self,
        current: _Evaluation,
        history: list[OptimizationIteration],
        iteration: int,
        decision: _StopDecision,
    ) -> OptimizationResult:
        logger.info("Optimization stopped: %s", decision.message)
        history.append(
            self._history_entry(
                current,
                iteration=iteration,
                phase=OptimizationPhase.REJECTED,
                accepted=False,
                rejection_reason=decision.message,
            )
        )
        return self._result(current, decision.termination, history)

    def _select_next_change(
        self,
        current: _Evaluation,
        candidates: list[_CandidateEvaluation],
    ) -> _AcceptedChange | _StopDecision:
        """Apply optimization policy to the evaluated candidate set."""

        if not candidates:
            return _StopDecision(
                OptimizationTermination.NO_FEASIBLE_CHANGE,
                "no selected sizing change is available within area and power "
                "constraints",
            )

        if not _timing_is_met(current.timing.wns):
            best = self._best_timing_change(current, candidates)
            if best is None:
                return _StopDecision(
                    OptimizationTermination.NO_TIMING_IMPROVEMENT,
                    "no candidate improves WNS or tied-WNS TNS",
                )
            return _AcceptedChange(best, OptimizationPhase.TIMING_REPAIR)

        best = self._best_cost_change(candidates)
        if best is None:
            return _StopDecision(
                OptimizationTermination.TIMING_MET_NO_COST_IMPROVEMENT,
                "timing is met and every feasible change would create a timing "
                "violation",
            )

        improvement = current.cost - best.evaluation.cost
        minimum_improvement = self.config.optimization.minimum_cost_improvement
        if (
            compare_floats(improvement, 0.0) <= 0
            or compare_floats(improvement, minimum_improvement) < 0
        ):
            return _StopDecision(
                OptimizationTermination.TIMING_MET_NO_COST_IMPROVEMENT,
                "timing is met and the best cost improvement "
                f"{max(improvement, 0.0):.6g} is below "
                f"{minimum_improvement:.6g}",
            )
        return _AcceptedChange(best, OptimizationPhase.COST_REDUCTION)

    @staticmethod
    def _log_accepted_change(
        current: _Evaluation,
        change: _AcceptedChange,
    ) -> None:
        candidate = change.candidate.evaluation
        if change.phase is OptimizationPhase.TIMING_REPAIR:
            logger.info(
                "Accepted timing repair: WNS %.6g -> %.6g ns, "
                "TNS %.6g -> %.6g ns",
                current.timing.wns,
                candidate.timing.wns,
                current.timing.tns,
                candidate.timing.tns,
            )
            return
        logger.info(
            "Accepted timing-safe cost reduction: cost %.6g -> %.6g",
            current.cost,
            candidate.cost,
        )

    def _accept_change(
        self,
        current: _Evaluation,
        change: _AcceptedChange,
        history: list[OptimizationIteration],
        iteration: int,
    ) -> _Evaluation:
        candidate = change.candidate
        previous_cell = current.circuit.cell_for(candidate.gate_name).name
        history.append(
            self._history_entry(
                candidate.evaluation,
                iteration=iteration,
                phase=change.phase,
                accepted=True,
                changed_gate=candidate.gate_name,
                previous_cell=previous_cell,
                new_cell=candidate.cell_name,
            )
        )
        return candidate.evaluation

    @staticmethod
    def _normalization_reference(
        circuit: Circuit,
        timing: TimingAnalysisResult,
    ) -> _NormalizationReference:
        return _NormalizationReference(
            delay=max(timing.circuit_delay, 1.0e-15),
            power=max(circuit.power, 1.0e-15),
            area=max(circuit.area, 1.0e-15),
        )

    def _candidate_evaluations(
        self,
        current: _Evaluation,
        reference: _NormalizationReference,
        choices: tuple[_SizingChoice, ...],
    ) -> list[_CandidateEvaluation]:
        """Exactly evaluate every constraint-safe brute-force choice."""

        evaluations: list[_CandidateEvaluation] = []
        for gate, candidate_cell in choices:
            candidate = self._candidate_evaluation(
                current,
                reference,
                gate,
                candidate_cell,
            )
            if candidate is not None:
                evaluations.append(candidate)

        return evaluations

    def _eligible_choices(
        self,
        current: _Evaluation,
        allowed_sizes: set[str],
    ) -> list[OptimizationChoice]:
        """Return critical-path gates with at least one allowed cell change."""

        eligible: list[OptimizationChoice] = []
        choices = get_optimization_candidates(
            current.circuit,
            current.timing.critical_paths,
        )
        for gate, candidate_cells in choices:
            current_cell = current.circuit.cell_for(gate)
            alternatives = tuple(
                cell
                for cell in candidate_cells
                if cell.size in allowed_sizes
                and cell.name != current_cell.name
            )
            if alternatives:
                eligible.append((gate, alternatives))
        return eligible

    def _evaluate_single_choice(
        self,
        current: _Evaluation,
        reference: _NormalizationReference,
        gate: Gate,
        candidate_cell: Cell,
    ) -> _AcceptedChange | _RejectedChange:
        """Evaluate exactly one sizing choice for this optimizer iteration."""

        candidate = self._candidate_evaluation(
            current,
            reference,
            gate,
            candidate_cell,
        )
        if candidate is None:
            return _RejectedChange(
                gate.name,
                candidate_cell.name,
                "candidate violates area or power constraints",
            )

        decision = self._select_next_change(current, [candidate])
        if isinstance(decision, _AcceptedChange):
            return decision
        return _RejectedChange(
            gate.name,
            candidate_cell.name,
            decision.message,
        )

    def _candidate_evaluation(
        self,
        current: _Evaluation,
        reference: _NormalizationReference,
        gate: Gate,
        candidate_cell: Cell,
    ) -> _CandidateEvaluation | None:
        area, power = replacement_area_and_power(
            current.circuit,
            gate.name,
            candidate_cell,
        )
        if self._violates_area_or_power_constraints(area, power):
            return None

        candidate_circuit = replace_gate_cell(
            current.circuit,
            gate.name,
            candidate_cell,
        )
        if not self._satisfies_constraints(candidate_circuit):
            return None
        gate_position = {
            gate_name: position
            for position, gate_name in enumerate(current.circuit.gates)
        }
        return _CandidateEvaluation(
            evaluation=self._evaluate(candidate_circuit, reference),
            gate_name=gate.name,
            gate_position=gate_position[gate.name],
            size_factor=candidate_cell.size_factor,
            cell_name=candidate_cell.name,
        )

    def _violates_area_or_power_constraints(
        self,
        area: float,
        power: float,
    ) -> bool:
        """Reject definite area or power violations before construction."""

        constraints = self.config.design_constraints
        return (
            compare_floats(area, constraints.maximum_area) > 0
            or compare_floats(power, constraints.maximum_power_uW) > 0
        )

    def _critical_path_one_step_choices(
        self,
        current: _Evaluation,
    ) -> list[tuple[Gate, Cell]]:
        """Return unique critical-path gates and their immediate larger cell."""

        allowed_sizes = set(self.config.optimization.allowed_sizes)
        choices: list[tuple[Gate, Cell]] = []
        seen: set[str] = set()
        for critical_path in current.timing.critical_paths:
            for gate in critical_path.path.gates:
                if gate.name in seen:
                    continue
                seen.add(gate.name)

                larger_cell = _next_larger_sizing_cell(
                    current.circuit,
                    gate,
                    allowed_sizes,
                )
                if larger_cell is not None:
                    choices.append((gate, larger_cell))
        return choices

    @staticmethod
    def _best_timing_change(
        current: _Evaluation,
        candidates: list[_CandidateEvaluation],
    ) -> _CandidateEvaluation | None:
        """Select the cheapest change from the highest-priority timing tier."""

        improving_wns = [
            candidate
            for candidate in candidates
            if _strictly_improves(
                candidate.evaluation.timing.wns,
                current.timing.wns,
            )
        ]
        eligible = improving_wns
        if not eligible:
            eligible = [
                candidate
                for candidate in candidates
                if _timing_values_tie(
                    candidate.evaluation.timing.wns,
                    current.timing.wns,
                )
                and _strictly_improves(
                    candidate.evaluation.timing.tns,
                    current.timing.tns,
                )
            ]
        if not eligible:
            return None

        best = min(
            eligible,
            key=lambda candidate: (
                candidate.evaluation.cost,
                candidate.gate_position,
                candidate.size_factor,
                candidate.cell_name,
            ),
        )
        return best

    @staticmethod
    def _best_cost_change(
        candidates: list[_CandidateEvaluation],
    ) -> _CandidateEvaluation | None:
        """Select the cheapest candidate that preserves timing within tolerance."""

        timing_safe = [
            candidate
            for candidate in candidates
            if _timing_is_met(candidate.evaluation.timing.wns)
        ]
        if not timing_safe:
            return None
        best = min(
            timing_safe,
            key=lambda candidate: (
                candidate.evaluation.cost,
                candidate.gate_position,
                candidate.size_factor,
                candidate.cell_name,
            ),
        )
        return best

    def _evaluate(
        self,
        circuit: Circuit,
        reference: _NormalizationReference,
        timing: TimingAnalysisResult | None = None,
    ) -> _Evaluation:
        """Compute the configured normalized cost of one implementation."""

        timing_result = timing if timing is not None else self._analyze(circuit)
        weights = self.config.optimization.weights
        timing_penalty = max(-timing_result.wns, 0.0)

        cost = (
            weights.delay
            * timing_result.circuit_delay
            / reference.delay
            + weights.power * circuit.power / reference.power
            + weights.area * circuit.area / reference.area
            + weights.timing_violation
            * timing_penalty
            / reference.delay
        )
        return _Evaluation(circuit, timing_result, cost)

    def _satisfies_constraints(self, circuit: Circuit) -> bool:
        constraints = self.config.design_constraints
        return (
            circuit.area <= constraints.maximum_area
            and circuit.power <= constraints.maximum_power_uW
        )

    def _result(
        self,
        evaluation: _Evaluation,
        termination: OptimizationTermination,
        history: list[OptimizationIteration],
    ) -> OptimizationResult:
        return OptimizationResult(
            circuit=evaluation.circuit,
            timing=evaluation.timing,
            cost=evaluation.cost,
            total_iterations=history[-1].iteration,
            accepted_iterations=sum(entry.accepted is True for entry in history),
            termination=termination,
            heuristic=self.heuristic,
            random_seed=self.random_seed,
            sta_calls=self._sta_calls,
            history=tuple(history),
        )

    def _analyze(self, circuit: Circuit) -> TimingAnalysisResult:
        """Run and count one optimizer-owned static timing analysis."""

        self._sta_calls += 1
        return analyze_timing(circuit)

    @staticmethod
    def _history_entry(
        evaluation: _Evaluation,
        iteration: int,
        phase: OptimizationPhase,
        accepted: bool | None = None,
        changed_gate: str | None = None,
        previous_cell: str | None = None,
        new_cell: str | None = None,
        rejection_reason: str | None = None,
    ) -> OptimizationIteration:
        return OptimizationIteration(
            iteration=iteration,
            phase=phase,
            accepted=accepted,
            changed_gate=changed_gate,
            previous_cell=previous_cell,
            new_cell=new_cell,
            rejection_reason=rejection_reason,
            wns=evaluation.timing.wns,
            tns=evaluation.timing.tns,
            circuit_delay=evaluation.timing.circuit_delay,
            area=evaluation.circuit.area,
            power=evaluation.circuit.power,
            cost=evaluation.cost,
        )


def _timing_is_met(wns: float) -> bool:
    return compare_floats(wns, 0.0) >= 0


def _strictly_improves(candidate: float, current: float) -> bool:
    return compare_floats(candidate, current) > 0


def _timing_values_tie(candidate: float, current: float) -> bool:
    return compare_floats(candidate, current) == 0


def _next_larger_sizing_cell(
    circuit: Circuit,
    gate: Gate,
    allowed_sizes: set[str],
) -> Cell | None:
    """Return the immediate larger allowed variant, without skipping a size."""

    current_cell = circuit.cell_for(gate)
    variants = sorted(
        circuit.cell_library.variants(current_cell.family),
        key=lambda cell: (cell.size_factor, cell.name),
    )
    current_index = next(
        index
        for index, cell in enumerate(variants)
        if cell.name == current_cell.name
    )
    next_index = current_index + 1
    if next_index >= len(variants):
        return None
    candidate = variants[next_index]
    return candidate if candidate.size in allowed_sizes else None
