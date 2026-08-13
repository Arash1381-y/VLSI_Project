"""Persistence of nominal timing and logical-effort analysis reports."""

from __future__ import annotations

from collections.abc import Iterable

from .circuit import Circuit
from .experiment_artifacts import ArtifactWriter, ExperimentError
from .logical_effort import LogicalEffortPathAnalysis, LogicalEffortStage
from .netlist import Gate, NetType
from .report_models import critical_path_record
from .sta import TimingAnalysisResult


TIMING_HEADER = (
    "node", "cell", "output_net", "is_primary_output",
    "cload_fF", "delay_rise_ns", "delay_fall_ns",
    "at_rise_ns", "at_fall_ns", "rt_rise_ns", "rt_fall_ns",
    "slack_rise_ns", "slack_fall_ns", "node_slack_ns",
)

CRITICAL_PATH_HEADER = (
    "rank", "slack_ns", "input_net", "output_net",
    "input_transition", "output_transition", "gate_count",
    "gates", "input_pins", "transitions",
)


def save_fanout_capacitances(
    writer: ArtifactWriter,
    circuit: Circuit,
) -> None:
    writer.write_json(
        "fanout_capacitances",
        {
            "unit": circuit.cell_library.units.get("capacitance", "fF"),
            "values": circuit.fanout_capacitances,
        },
    )


def save_gate_delays(writer: ArtifactWriter, circuit: Circuit) -> None:
    writer.write_json(
        "gate_delays",
        {
            "unit": circuit.cell_library.units.get("time", "ns"),
            "values": {
                gate_name: {"rise": delay.rise, "fall": delay.fall}
                for gate_name, delay in circuit.gate_delays.items()
            },
        },
    )


def save_timing_reports(
    writer: ArtifactWriter,
    circuit: Circuit,
    result: TimingAnalysisResult,
    elapsed_seconds: float,
) -> None:
    paths = [
        critical_path_record(rank, critical_path)
        for rank, critical_path in enumerate(result.critical_paths, start=1)
    ]
    writer.write_json(
        "timing_analysis",
        {
            "wns_ns": result.wns,
            "tns_ns": result.tns,
            "circuit_delay_ns": result.circuit_delay,
            "timing_met": result.wns >= 0.0,
            "elapsed_seconds": elapsed_seconds,
            "top_k_paths": circuit.config.timing_analysis.top_k_paths,
            "critical_paths": paths,
        },
    )
    writer.write_csv(
        "timing_analysis",
        TIMING_HEADER,
        _timing_rows(circuit, result),
    )
    writer.write_csv(
        "critical_paths",
        CRITICAL_PATH_HEADER,
        (_critical_path_row(path) for path in paths),
    )


def save_logical_effort_report(
    writer: ArtifactWriter,
    circuit: Circuit,
    timing: TimingAnalysisResult,
    analyses: tuple[LogicalEffortPathAnalysis, ...],
) -> None:
    paths = [
        _logical_effort_path_record(rank, critical_path.slack, analysis)
        for rank, (critical_path, analysis) in enumerate(
            zip(timing.critical_paths, analyses, strict=True), start=1
        )
    ]
    writer.write_json(
        "logical_effort_analysis",
        {
            "units": dict(circuit.cell_library.units),
            "tau_ns": circuit.cell_library.logical_effort_tau,
            "paths": paths,
        },
    )


def _timing_rows(
    circuit: Circuit,
    result: TimingAnalysisResult,
) -> Iterable[tuple[object, ...]]:
    for level in circuit.topological_order:
        for gate in level:
            yield _timing_row(circuit, result, gate)


def _timing_row(
    circuit: Circuit,
    result: TimingAnalysisResult,
    gate: Gate,
) -> tuple[object, ...]:
    if gate.output is None:
        raise ExperimentError(f"gate {gate.name!r} has no output")
    output_name = gate.output.name
    delay = circuit.gate_delays[gate.name]
    arrival = result.arrival_times[output_name]
    required = result.required_times[output_name]
    slack = result.transition_slacks[output_name]
    return (
        gate.name,
        gate.cell.name,
        output_name,
        gate.output.net_type is NetType.OUTPUT,
        circuit.fanout_capacitances[gate.name],
        delay.rise,
        delay.fall,
        arrival.rise,
        arrival.fall,
        required.rise,
        required.fall,
        slack.rise,
        slack.fall,
        min(slack.rise, slack.fall),
    )


def _critical_path_row(path: dict[str, object]) -> tuple[object, ...]:
    return tuple(path[column] for column in CRITICAL_PATH_HEADER)


def _logical_effort_path_record(
    rank: int,
    endpoint_slack: float,
    analysis: LogicalEffortPathAnalysis,
) -> dict[str, object]:
    return {
        "rank": rank,
        "input_net": analysis.path.input_net.name,
        "output_net": analysis.path.output_net.name,
        "endpoint_slack_ns": endpoint_slack,
        "number_of_stages": len(analysis.stages),
        "G_path_logical_effort": analysis.path_logical_effort,
        "B_branching_effort": analysis.branching_effort,
        "H_electrical_effort": analysis.electrical_effort,
        "F_total_effort": analysis.total_effort,
        "P_parasitic_delay_normalized": analysis.parasitic_delay,
        "optimal_stage_effort": analysis.optimal_stage_effort,
        "minimum_normalized_delay": analysis.minimum_normalized_delay,
        "minimum_theoretical_delay_ns": analysis.minimum_delay,
        "stages": [_logical_effort_stage_record(stage) for stage in analysis.stages],
    }


def _logical_effort_stage_record(stage: LogicalEffortStage) -> dict[str, object]:
    pin = stage.step.gate.cell.input_pins[stage.step.input_pin]
    return {
        "gate": stage.step.gate.name,
        "cell": stage.step.gate.cell.name,
        "input_pin_index": stage.step.input_pin,
        "input_pin_name": pin.name,
        "g": stage.logical_effort,
        "b": stage.branching_effort,
        "h_on_path": stage.electrical_effort,
        "f_current": stage.stage_effort,
        "p": stage.parasitic_delay,
        "input_capacitance_fF": stage.input_capacitance,
        "total_output_load_fF": stage.total_output_load,
        "on_path_load_fF": stage.on_path_load,
        "target_input_capacitance_fF": stage.target_input_capacitance,
        "target_size_ratio": stage.target_size_ratio,
        "candidate_cells": [cell.name for cell in stage.candidates],
    }
