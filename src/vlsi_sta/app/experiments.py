"""Experiment orchestration for one immutable circuit."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from time import perf_counter
from typing import Literal

from vlsi_sta.domain.circuit import Circuit
from vlsi_sta.reporting.artifacts import ArtifactWriter, ExperimentError
from vlsi_sta.analysis.logical_effort import LogicalEffortPathAnalysis, analyze_path_logical_effort
from vlsi_sta.analysis.monte_carlo import MonteCarloError, generate_variations, run_monte_carlo
from vlsi_sta.reporting.monte_carlo import (
    save_completed_monte_carlo,
    save_skipped_monte_carlo,
)
from vlsi_sta.optimization.heuristics import OptimizationHeuristic
from vlsi_sta.reporting.optimization import save_optimization_reports
from vlsi_sta.optimization.optimizer import CircuitOptimizer, OptimizationResult
from vlsi_sta.reporting.models import (
    CANONICAL_OPTIMIZATION,
    EFFORT_GAP_OPTIMIZATION,
    GREEDY_OPTIMIZATION,
    TimedOptimization,
)
from vlsi_sta.analysis.sta import TimingAnalysisResult, analyze_timing
from vlsi_sta.reporting.summary import save_final_summary
from vlsi_sta.reporting.timing import (
    save_fanout_capacitances,
    save_gate_delays,
    save_logical_effort_report,
    save_timing_reports,
)
from vlsi_sta.reporting.topology import save_circuit_topology


logger = logging.getLogger(__name__)

ExperimentName = Literal[
    "fanout_capacitances",
    "gate_delays",
    "timing_analysis",
    "logical_effort_analysis",
    "optimization",
    "topology_export",
    "visualization",
    "monte_carlo",
    "summary",
]

DEFAULT_EXPERIMENTS: tuple[ExperimentName, ...] = (
    "fanout_capacitances",
    "gate_delays",
    "timing_analysis",
    "logical_effort_analysis",
    "optimization",
    "topology_export",
    "visualization",
    "monte_carlo",
    "summary",
)

DEFAULT_RANDOM_SEED = 0
GENERATED_ARTIFACT_FILENAMES: tuple[str, ...] = (
    "run.log",
    "validation_report.json",
    "fanout_capacitances.csv",
    "gate_delays.csv",
    "timing_summary.csv",
    "timing_analysis.csv",
    "critical_paths.csv",
    "logical_effort_paths.csv",
    "logical_effort_stages.csv",
    "logical_effort_candidates.csv",
    "optimization_slack_weighted_capacitance.csv",
    "optimization_criticality_effort_gap.csv",
    "optimization_random_greedy.csv",
    "optimization_summary.csv",
    "optimization_comparison.csv",
    "circuit_topology.json",
    "circuit_graph_pre_optimization.png",
    "circuit_graph_post_optimization.png",
    "monte_carlo_samples.csv",
    "monte_carlo_variations.csv",
    "monte_carlo_critical_paths.csv",
    "monte_carlo_gate_criticality.csv",
    "monte_carlo_statistics.csv",
    "monte_carlo_summary.json",
    "summary.json",
)

OBSOLETE_ARTIFACT_FILENAMES: tuple[str, ...] = (
    "optimization.csv",
    "fanout_capacitances.json",
    "gate_delays.json",
    "timing_analysis.json",
    "logical_effort_analysis.json",
    "optimization_comparison.json",
)


class Experiments:
    """Run selected analyses and persist their reports in dependency order."""

    def __init__(
        self,
        directory: str | Path,
        experiments: Sequence[ExperimentName],
        netlist_path: str | Path,
        random_seed: int = DEFAULT_RANDOM_SEED,
    ) -> None:
        self.directory = Path(directory)
        self.experiments = tuple(experiments)
        self.netlist_path = Path(netlist_path)
        self.random_seed = random_seed
        self._validate_experiments()

        self._writer = ArtifactWriter(self.directory)
        self._nominal_timing: TimingAnalysisResult | None = None
        self._logical_effort_results: tuple[LogicalEffortPathAnalysis, ...] = ()
        self._optimization_results: dict[str, OptimizationResult] = {}
        self._optimization_runs: dict[str, TimedOptimization] = {}
        self._monte_carlo_summary: dict[str, object] = {"status": "not_run"}

    def run(self, circuit: Circuit) -> None:
        """Run the selected experiments in their configured order."""

        handlers: dict[ExperimentName, Callable[[Circuit], None]] = {
            "fanout_capacitances": self._save_fanout_capacitances,
            "gate_delays": self._save_gate_delays,
            "timing_analysis": self._run_timing_analysis,
            "logical_effort_analysis": self._run_logical_effort_analysis,
            "optimization": self._run_optimization,
            "topology_export": self._save_topology,
            "visualization": self._run_visualization,
            "monte_carlo": self._run_monte_carlo,
            "summary": self._save_final_summary,
        }
        for experiment in self.experiments:
            logger.info("Running experiment: %s", experiment)
            handlers[experiment](circuit)
        logger.info("Circuit experiments completed; results are in %s", self.directory)

    def write_json(self, artifact_name: str, data: object) -> Path:
        """Serialize one JSON artifact through the shared artifact writer."""

        return self._writer.write_json(artifact_name, data)

    def write_csv(
        self,
        artifact_name: str,
        header: Sequence[str],
        rows: Iterable[Sequence[object]],
    ) -> Path:
        """Serialize one CSV artifact through the shared artifact writer."""

        return self._writer.write_csv(artifact_name, header, rows)

    def _save_fanout_capacitances(self, circuit: Circuit) -> None:
        save_fanout_capacitances(self._writer, circuit)

    def _save_gate_delays(self, circuit: Circuit) -> None:
        save_gate_delays(self._writer, circuit)

    def _run_timing_analysis(self, circuit: Circuit) -> None:
        started_at = perf_counter()
        timing = analyze_timing(circuit)
        elapsed_seconds = perf_counter() - started_at
        self._nominal_timing = timing
        save_timing_reports(self._writer, circuit, timing, elapsed_seconds)

    def _run_logical_effort_analysis(self, circuit: Circuit) -> None:
        timing = self._require_nominal_timing()
        analyses = tuple(
            analyze_path_logical_effort(circuit, critical_path.path)
            for critical_path in timing.critical_paths
        )
        self._logical_effort_results = analyses
        save_logical_effort_report(self._writer, circuit, timing, analyses)

    def _run_optimization(self, circuit: Circuit) -> None:
        runs = (
            self._time_optimization(
                CANONICAL_OPTIMIZATION,
                CircuitOptimizer(
                    circuit,
                    OptimizationHeuristic.SLACK_WEIGHTED_CAPACITANCE,
                ),
            ),
            self._time_optimization(
                EFFORT_GAP_OPTIMIZATION,
                CircuitOptimizer(
                    circuit,
                    OptimizationHeuristic.CRITICALITY_EFFORT_GAP,
                ),
            ),
            self._time_optimization(
                GREEDY_OPTIMIZATION,
                CircuitOptimizer(
                    circuit,
                    OptimizationHeuristic.RANDOM_GREEDY,
                    random_seed=self.random_seed,
                ),
            ),
        )
        self._optimization_runs = {run.name: run for run in runs}
        self._optimization_results = {
            run.name: run.result for run in runs
        }
        save_optimization_reports(
            self._writer,
            circuit,
            self._require_nominal_timing(),
            runs,
        )

    def _run_visualization(self, circuit: Circuit) -> None:
        # Keep plotting dependencies out of parsing and non-visual workflows.
        from vlsi_sta.reporting.visualization import (
            draw_circuit_graph,
            timing_slack_extent,
        )

        nominal = self._require_nominal_timing()
        canonical = self._require_canonical_optimization()
        slack_extent = timing_slack_extent(nominal, canonical.timing)
        graphs = (
            ("circuit_graph_pre_optimization.png", circuit, nominal),
            (
                "circuit_graph_post_optimization.png",
                canonical.circuit,
                canonical.timing,
            ),
        )
        for filename, graph_circuit, timing in graphs:
            output_path = self.directory / filename
            draw_circuit_graph(graph_circuit, timing, output_path, slack_extent)
            self._writer.record_existing(output_path)

    def _save_topology(self, circuit: Circuit) -> None:
        optimized = self._require_canonical_optimization()
        save_circuit_topology(
            self._writer,
            circuit,
            self._require_nominal_timing(),
            optimized.circuit,
            optimized.timing,
        )

    def _run_monte_carlo(self, circuit: Circuit) -> None:
        if not circuit.config.monte_carlo.enabled:
            logger.info("Monte Carlo experiment is disabled by configuration")
            self._monte_carlo_summary = save_skipped_monte_carlo(
                self._writer, circuit
            )
            return

        optimization = self._optimization_results.get(CANONICAL_OPTIMIZATION)
        if optimization is None:
            optimization = CircuitOptimizer(
                circuit,
                OptimizationHeuristic.SLACK_WEIGHTED_CAPACITANCE,
            ).optimize()
        try:
            variations = generate_variations(circuit, circuit.config.monte_carlo)
            pre = run_monte_carlo(circuit, variations, "pre_optimization")
            post = run_monte_carlo(
                optimization.circuit,
                variations,
                "post_optimization",
            )
        except MonteCarloError as exc:
            raise ExperimentError(f"Monte Carlo experiment failed: {exc}") from exc
        self._monte_carlo_summary = save_completed_monte_carlo(
            self._writer,
            circuit,
            optimization,
            variations,
            pre,
            post,
        )

    def _save_final_summary(self, circuit: Circuit) -> None:
        self._require_canonical_optimization()
        save_final_summary(
            self._writer,
            circuit,
            self.netlist_path,
            self._require_nominal_timing(),
            self._logical_effort_results,
            self._optimization_runs,
            self._monte_carlo_summary,
        )

    def _validate_experiments(self) -> None:
        available = set(DEFAULT_EXPERIMENTS)
        unknown = [name for name in self.experiments if name not in available]
        if unknown:
            raise ExperimentError(f"unknown experiment: {unknown[0]!r}")
        if len(set(self.experiments)) != len(self.experiments):
            raise ExperimentError("experiment list cannot contain duplicates")

    def _require_nominal_timing(self) -> TimingAnalysisResult:
        if self._nominal_timing is None:
            raise ExperimentError("timing analysis must run before this experiment")
        return self._nominal_timing

    def _require_canonical_optimization(self) -> OptimizationResult:
        result = self._optimization_results.get(CANONICAL_OPTIMIZATION)
        if result is None:
            raise ExperimentError("optimization must run before this experiment")
        return result

    @staticmethod
    def _time_optimization(
        name: str,
        optimizer: CircuitOptimizer,
    ) -> TimedOptimization:
        logger.info("Running %s optimization", name)
        started_at = perf_counter()
        result = optimizer.optimize()
        elapsed_seconds = perf_counter() - started_at
        logger.info("%s optimization completed in %.6f s", name, elapsed_seconds)
        return TimedOptimization(name, result, elapsed_seconds)


__all__ = [
    "DEFAULT_EXPERIMENTS",
    "DEFAULT_RANDOM_SEED",
    "GENERATED_ARTIFACT_FILENAMES",
    "OBSOLETE_ARTIFACT_FILENAMES",
    "ExperimentError",
    "ExperimentName",
    "Experiments",
]
