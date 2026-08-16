"""Construction of the aggregate per-circuit summary report."""

from __future__ import annotations

import json
from pathlib import Path

from vlsi_sta.domain.circuit import Circuit
from vlsi_sta.reporting.artifacts import ArtifactWriter, ExperimentError
from vlsi_sta.analysis.logical_effort import LogicalEffortPathAnalysis
from vlsi_sta.reporting.models import (
    CANONICAL_OPTIMIZATION,
    EFFORT_GAP_OPTIMIZATION,
    GREEDY_OPTIMIZATION,
    TimedOptimization,
    circuit_compliance,
    circuit_specification,
    critical_path_record,
    optimization_differences,
    optimization_record,
)
from vlsi_sta.analysis.sta import TimingAnalysisResult


def save_final_summary(
    writer: ArtifactWriter,
    circuit: Circuit,
    netlist_path: Path,
    nominal_timing: TimingAnalysisResult,
    logical_effort: tuple[LogicalEffortPathAnalysis, ...],
    optimization_runs: dict[str, TimedOptimization],
    monte_carlo_summary: dict[str, object],
) -> None:
    logical_run = optimization_runs[CANONICAL_OPTIMIZATION]
    effort_gap_run = optimization_runs[EFFORT_GAP_OPTIMIZATION]
    greedy_run = optimization_runs[GREEDY_OPTIMIZATION]
    canonical = logical_run.result
    compliance = circuit_compliance(canonical.circuit, canonical.timing)
    summary = {
        "circuit": _circuit_identity(circuit, netlist_path),
        "validation": _load_validation(writer.directory),
        "constraints": _constraint_record(circuit),
        "initial": circuit_specification(
            circuit,
            nominal_timing,
            logical_run.result.history[0].cost,
        ),
        "canonical_method": CANONICAL_OPTIMIZATION,
        "post_optimization": circuit_specification(
            canonical.circuit, canonical.timing, canonical.cost
        ),
        "optimizers": {
            "logical_effort_guided": optimization_record(logical_run),
            "criticality_effort_gap": optimization_record(effort_gap_run),
            "greedy_baseline": optimization_record(greedy_run),
        },
        "optimizer_comparison": {
            EFFORT_GAP_OPTIMIZATION: optimization_differences(
                logical_run,
                effort_gap_run,
            ),
            GREEDY_OPTIMIZATION: optimization_differences(
                logical_run,
                greedy_run,
            ),
        },
        "critical_paths": _critical_path_summary(circuit, nominal_timing),
        "logical_effort_worst_path": _worst_logical_effort(logical_effort),
        "monte_carlo": monte_carlo_summary,
        "compliance": {
            **compliance,
            "all_constraints_compliant": all(compliance.values()),
        },
        "artifacts": _artifact_manifest(writer),
    }
    writer.write_json("summary", summary)


def _circuit_identity(circuit: Circuit, netlist_path: Path) -> dict[str, object]:
    return {
        "name": circuit.config.circuit_name,
        "config_file": str(circuit.config.path.resolve()),
        "netlist_file": str(netlist_path.resolve()),
        "cell_library_file": str(circuit.cell_library.path.resolve()),
        "gate_count": len(circuit.gates),
        "net_count": len(circuit.netlist),
        "primary_input_count": len(circuit.config.input_arrival_times),
        "primary_output_count": len(circuit.config.output_required_times),
        "units": dict(circuit.cell_library.units),
    }


def _constraint_record(circuit: Circuit) -> dict[str, object]:
    constraints = circuit.config.design_constraints
    return {
        "maximum_area": constraints.maximum_area,
        "maximum_power_uW": constraints.maximum_power_uW,
        "timing_requirement": "WNS >= 0 ns",
    }


def _critical_path_summary(
    circuit: Circuit,
    timing: TimingAnalysisResult,
) -> dict[str, object]:
    worst = (
        critical_path_record(1, timing.critical_paths[0])
        if timing.critical_paths
        else None
    )
    return {
        "reported_count": len(timing.critical_paths),
        "configured_top_k": circuit.config.timing_analysis.top_k_paths,
        "worst": worst,
    }


def _worst_logical_effort(
    analyses: tuple[LogicalEffortPathAnalysis, ...],
) -> dict[str, object] | None:
    if not analyses:
        return None
    analysis = analyses[0]
    return {
        "G": analysis.path_logical_effort,
        "B": analysis.branching_effort,
        "H": analysis.electrical_effort,
        "F": analysis.total_effort,
        "P": analysis.parasitic_delay,
        "optimal_stage_effort": analysis.optimal_stage_effort,
        "minimum_theoretical_delay_ns": analysis.minimum_delay,
    }


def _load_validation(directory: Path) -> object:
    path = directory / "validation_report.json"
    if not path.is_file():
        return {"valid": True}
    try:
        with path.open("r", encoding="utf-8") as report_file:
            return json.load(report_file)
    except (OSError, json.JSONDecodeError) as exc:
        raise ExperimentError(f"cannot read validation report {path}: {exc}") from exc


def _artifact_manifest(writer: ArtifactWriter) -> dict[str, str]:
    artifacts = dict(sorted(writer.artifacts.items()))
    artifacts["summary.json"] = "summary.json"
    return artifacts
