"""Persistence of nominal timing and logical-effort analysis reports."""

from __future__ import annotations

from collections.abc import Iterable

from vlsi_sta.domain.circuit import Circuit
from vlsi_sta.reporting.artifacts import ArtifactWriter
from vlsi_sta.analysis.logical_effort import LogicalEffortPathAnalysis, LogicalEffortStage
from vlsi_sta.input.netlist import Gate, NetType
from vlsi_sta.reporting.models import critical_path_record
from vlsi_sta.analysis.sta import TimingAnalysisResult


TIMING_HEADER = (
    "node", "cell", "output_net", "is_primary_output",
    "cload_fF", "delay_rise_ns", "delay_fall_ns",
    "at_rise_ns", "at_fall_ns", "rt_rise_ns", "rt_fall_ns",
    "slack_rise_ns", "slack_fall_ns", "node_slack_ns",
)

FANOUT_HEADER = (
    "gate", "cell", "output_net", "is_primary_output",
    "fanout_capacitance_fF",
)

GATE_DELAY_HEADER = (
    "gate", "cell", "output_net", "delay_rise_ns", "delay_fall_ns",
)

TIMING_SUMMARY_HEADER = (
    "circuit", "circuit_delay_ns", "wns_ns", "tns_ns", "timing_met",
    "elapsed_seconds", "configured_top_k_paths", "reported_path_count",
)

CRITICAL_PATH_HEADER = (
    "rank", "slack_ns", "input_net", "output_net",
    "input_transition", "output_transition", "gate_count",
    "gates", "input_pins", "transitions",
)

LOGICAL_EFFORT_PATH_HEADER = (
    "path_rank", "input_net", "output_net", "endpoint_slack_ns",
    "stage_count", "G_path_logical_effort", "B_branching_effort",
    "H_electrical_effort", "F_total_effort",
    "P_parasitic_delay_normalized", "optimal_stage_effort",
    "minimum_normalized_delay", "tau_ns", "minimum_theoretical_delay_ns",
)

LOGICAL_EFFORT_STAGE_HEADER = (
    "path_rank", "stage_index", "gate", "cell", "input_pin_index",
    "input_pin_name", "g", "b", "h_on_path", "f_current", "p",
    "input_capacitance_fF", "total_output_load_fF", "on_path_load_fF",
    "target_input_capacitance_fF", "target_size_ratio", "candidate_count",
)

LOGICAL_EFFORT_CANDIDATE_HEADER = (
    "path_rank", "stage_index", "gate", "candidate_rank",
    "candidate_cell", "candidate_size", "candidate_size_factor",
    "candidate_input_capacitance_fF",
)


def save_fanout_capacitances(
    writer: ArtifactWriter,
    circuit: Circuit,
) -> None:
    writer.write_csv(
        "fanout_capacitances",
        FANOUT_HEADER,
        (_fanout_row(circuit, gate) for gate in _topological_gates(circuit)),
    )


def save_gate_delays(writer: ArtifactWriter, circuit: Circuit) -> None:
    writer.write_csv(
        "gate_delays",
        GATE_DELAY_HEADER,
        (_gate_delay_row(circuit, gate) for gate in _topological_gates(circuit)),
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
    writer.write_csv(
        "timing_summary",
        TIMING_SUMMARY_HEADER,
        ((
            circuit.config.circuit_name,
            result.circuit_delay,
            result.wns,
            result.tns,
            result.wns >= 0.0,
            elapsed_seconds,
            circuit.config.timing_analysis.top_k_paths,
            len(paths),
        ),),
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
    paths = tuple(
        (rank, critical_path.slack, analysis)
        for rank, (critical_path, analysis) in enumerate(
            zip(timing.critical_paths, analyses, strict=True), start=1
        )
    )
    writer.write_csv(
        "logical_effort_paths",
        LOGICAL_EFFORT_PATH_HEADER,
        (
            _logical_effort_path_row(circuit, rank, slack, analysis)
            for rank, slack, analysis in paths
        ),
    )
    writer.write_csv(
        "logical_effort_stages",
        LOGICAL_EFFORT_STAGE_HEADER,
        _logical_effort_stage_rows(circuit, paths),
    )
    writer.write_csv(
        "logical_effort_candidates",
        LOGICAL_EFFORT_CANDIDATE_HEADER,
        _logical_effort_candidate_rows(paths),
    )


def _topological_gates(circuit: Circuit) -> Iterable[Gate]:
    return (gate for level in circuit.topological_order for gate in level)


def _fanout_row(circuit: Circuit, gate: Gate) -> tuple[object, ...]:
    return (
        gate.name,
        circuit.cell_for(gate).name,
        gate.output,
        circuit.output_net(gate).net_type is NetType.OUTPUT,
        circuit.fanout_capacitances[gate.name],
    )


def _gate_delay_row(circuit: Circuit, gate: Gate) -> tuple[object, ...]:
    delay = circuit.gate_delays[gate.name]
    return gate.name, circuit.cell_for(gate).name, gate.output, delay.rise, delay.fall


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
    output_name = gate.output
    output_net = circuit.output_net(gate)
    delay = circuit.gate_delays[gate.name]
    arrival = result.arrival_times[output_name]
    required = result.required_times[output_name]
    slack = result.transition_slacks[output_name]
    return (
        gate.name,
        circuit.cell_for(gate).name,
        output_name,
        output_net.net_type is NetType.OUTPUT,
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


def _logical_effort_path_row(
    circuit: Circuit,
    rank: int,
    endpoint_slack: float,
    analysis: LogicalEffortPathAnalysis,
) -> tuple[object, ...]:
    return (
        rank,
        analysis.path.input_net.name,
        analysis.path.output_net.name,
        endpoint_slack,
        len(analysis.stages),
        analysis.path_logical_effort,
        analysis.branching_effort,
        analysis.electrical_effort,
        analysis.total_effort,
        analysis.parasitic_delay,
        analysis.optimal_stage_effort,
        analysis.minimum_normalized_delay,
        circuit.cell_library.logical_effort_tau,
        analysis.minimum_delay,
    )


def _logical_effort_stage_rows(
    circuit: Circuit,
    paths: tuple[tuple[int, float, LogicalEffortPathAnalysis], ...],
) -> Iterable[tuple[object, ...]]:
    for path_rank, _, analysis in paths:
        for stage_index, stage in enumerate(analysis.stages, start=1):
            yield _logical_effort_stage_row(
                circuit, path_rank, stage_index, stage
            )


def _logical_effort_stage_row(
    circuit: Circuit,
    path_rank: int,
    stage_index: int,
    stage: LogicalEffortStage,
) -> tuple[object, ...]:
    cell = circuit.cell_for(stage.step.gate)
    pin = cell.input_pins[stage.step.input_pin]
    return (
        path_rank,
        stage_index,
        stage.step.gate.name,
        cell.name,
        stage.step.input_pin,
        pin.name,
        stage.logical_effort,
        stage.branching_effort,
        stage.electrical_effort,
        stage.stage_effort,
        stage.parasitic_delay,
        stage.input_capacitance,
        stage.total_output_load,
        stage.on_path_load,
        stage.target_input_capacitance,
        stage.target_size_ratio,
        len(stage.candidates),
    )


def _logical_effort_candidate_rows(
    paths: tuple[tuple[int, float, LogicalEffortPathAnalysis], ...],
) -> Iterable[tuple[object, ...]]:
    for path_rank, _, analysis in paths:
        for stage_index, stage in enumerate(analysis.stages, start=1):
            for candidate_rank, candidate in enumerate(
                stage.candidates, start=1
            ):
                yield (
                    path_rank,
                    stage_index,
                    stage.step.gate.name,
                    candidate_rank,
                    candidate.name,
                    candidate.size,
                    candidate.size_factor,
                    candidate.input_pins[
                        stage.step.input_pin
                    ].capacitance,
                )
