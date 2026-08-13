"""Static timing analysis and transition-aware critical-path tracing."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType

from .cell import RiseFall, TimingSense
from .circuit import Circuit
from .netlist import CircuitPath, Gate, Net, NetType, NetlistError, PathStep


logger = logging.getLogger(__name__)


class Transition(str, Enum):
    RISE = "rise"
    FALL = "fall"


PredecessorArc = tuple[int, Transition]
PredecessorTable = dict[tuple[str, Transition], tuple[PredecessorArc, ...]]


@dataclass(frozen=True, eq=False)
class CriticalPath:
    """A transition-aware timing path and its endpoint slack."""

    path: CircuitPath
    transitions: tuple[Transition, ...]
    slack: float


@dataclass(frozen=True)
class TimingAnalysisResult:
    """Public results produced by one timing-analysis run."""

    critical_paths: tuple[CriticalPath, ...]
    wns: float
    tns: float
    circuit_delay: float
    arrival_times: Mapping[str, RiseFall]
    required_times: Mapping[str, RiseFall]
    transition_slacks: Mapping[str, RiseFall]

    def node_slack(self, net_name: str) -> float:
        """Return the worst rise/fall slack for one net."""

        slack = self.transition_slacks[net_name]
        return min(slack.rise, slack.fall)


def analyze_timing(
    circuit: Circuit,
    gate_delays: Mapping[str, RiseFall] | None = None,
) -> TimingAnalysisResult:
    """Run STA and return critical paths and endpoint metrics."""

    sampled = gate_delays is not None
    if not sampled:
        logger.info("Running static timing analysis")
    delays = circuit.gate_delays if gate_delays is None else gate_delays
    arrival_times, required_times, predecessors, transition_slacks = _run_sta(
        circuit, delays
    )
    circuit_delay = _compute_circuit_delay(circuit, arrival_times)
    logger.debug("Computing WNS and TNS from primary-output slacks")
    wns, tns = _compute_output_slack_metrics(circuit, transition_slacks)
    logger.debug(
        "Tracing the %d most critical paths from output slacks",
        circuit.config.timing_analysis.top_k_paths,
    )
    critical_paths = _find_critical_paths(
        circuit,
        predecessors,
        transition_slacks,
    )
    logger.debug("Selected %d critical paths", len(critical_paths))
    if sampled:
        return _timing_result(
            critical_paths, wns, tns, circuit_delay,
            arrival_times, required_times, transition_slacks,
        )
    if wns >= 0.0:
        logger.info("Timing PASSED: WNS=%.6g ns, TNS=%.6g ns", wns, tns)
    else:
        logger.info("Timing FAILED: WNS=%.6g ns, TNS=%.6g ns", wns, tns)
    return _timing_result(
        critical_paths, wns, tns, circuit_delay,
        arrival_times, required_times, transition_slacks,
    )


def _timing_result(
    critical_paths: list[CriticalPath],
    wns: float,
    tns: float,
    circuit_delay: float,
    arrival_times: dict[str, RiseFall],
    required_times: dict[str, RiseFall],
    transition_slacks: dict[str, RiseFall],
) -> TimingAnalysisResult:
    return TimingAnalysisResult(
        tuple(critical_paths),
        wns,
        tns,
        circuit_delay,
        MappingProxyType(arrival_times),
        MappingProxyType(required_times),
        MappingProxyType(transition_slacks),
    )


def _run_sta(
    circuit: Circuit,
    gate_delays: Mapping[str, RiseFall],
) -> tuple[
    dict[str, RiseFall],
    dict[str, RiseFall],
    PredecessorTable,
    dict[str, RiseFall],
]:
    """Run STA and return arrivals, controlling arcs, and transition slacks."""

    logger.debug("Propagating arrival times")
    arrival_times, predecessors = _compute_arrival_times(circuit, gate_delays)
    logger.debug("Propagating required times")
    required_times = _compute_required_times(circuit, gate_delays)
    logger.debug("Computing net slacks")
    return (
        arrival_times,
        required_times,
        predecessors,
        _compute_transition_slacks(arrival_times, required_times),
    )


def _compute_circuit_delay(
    circuit: Circuit,
    arrival_times: Mapping[str, RiseFall],
) -> float:
    """Return the latest rise or fall arrival at a primary output."""

    return max(
        (
            max(arrival_times[net_name].rise, arrival_times[net_name].fall)
            for net_name, net in circuit.netlist.items()
            if net.net_type is NetType.OUTPUT
        ),
        default=float("-inf"),
    )


def _compute_output_slack_metrics(
    circuit: Circuit,
    transition_slacks: Mapping[str, RiseFall],
) -> tuple[float, float]:
    """Return worst and total negative slack at primary outputs."""

    output_slacks = _output_slack_values(circuit, transition_slacks)
    wns = min(output_slacks, default=float("inf"))
    tns = sum(slack for slack in output_slacks if slack < 0.0)
    return wns, tns


def _find_critical_paths(
    circuit: Circuit,
    predecessors: Mapping[tuple[str, Transition], tuple[PredecessorArc, ...]],
    transition_slacks: Mapping[str, RiseFall],
) -> list[CriticalPath]:
    """Trace controlling arrival arcs and rank paths by minimum STA slack."""

    paths: list[CriticalPath] = []
    for output_net in circuit.netlist.values():
        if output_net.net_type is not NetType.OUTPUT:
            continue

        for output_transition in Transition:
            endpoint_slack = _transition_slack(
                transition_slacks,
                output_net.name,
                output_transition,
            )
            _trace_paths_backward(
                current_net=output_net,
                current_transition=output_transition,
                output_net=output_net,
                reversed_steps=(),
                reversed_transitions=(output_transition,),
                endpoint_slack=endpoint_slack,
                found_paths=paths,
                predecessors=predecessors,
            )

    paths.sort(key=lambda path: path.slack)
    return paths[:circuit.config.timing_analysis.top_k_paths]


def _trace_paths_backward(
    current_net: Net,
    current_transition: Transition,
    output_net: Net,
    reversed_steps: tuple[PathStep, ...],
    reversed_transitions: tuple[Transition, ...],
    endpoint_slack: float,
    found_paths: list[CriticalPath],
    predecessors: Mapping[tuple[str, Transition], tuple[PredecessorArc, ...]],
) -> None:
    """Recursively explore every predecessor controlling the current arrival."""

    if current_net.net_type is NetType.INPUT:
        found_paths.append(
            CriticalPath(
                path=CircuitPath(
                    input_net=current_net,
                    steps=tuple(reversed(reversed_steps)),
                    output_net=output_net,
                ),
                transitions=tuple(reversed(reversed_transitions)),
                slack=endpoint_slack,
            )
        )
        return

    gate = current_net.driver
    if gate is None:
        raise NetlistError(f"net {current_net.name!r} has no driver")

    for pin_number, input_transition in predecessors[
        (current_net.name, current_transition)
    ]:
        _trace_paths_backward(
            current_net=gate.inputs[pin_number],
            current_transition=input_transition,
            output_net=output_net,
            reversed_steps=reversed_steps + (PathStep(gate, pin_number),),
            reversed_transitions=reversed_transitions + (input_transition,),
            endpoint_slack=endpoint_slack,
            found_paths=found_paths,
            predecessors=predecessors,
        )


def _transition_slack(
    transition_slacks: Mapping[str, RiseFall],
    net_name: str,
    transition: Transition,
) -> float:
    """Return one transition's slack from an STA table."""

    slack = transition_slacks[net_name]
    if transition is Transition.RISE:
        return slack.rise
    return slack.fall


def _transition_value(value: RiseFall, transition: Transition) -> float:
    """Return the rise or fall component selected by a transition."""

    if transition is Transition.RISE:
        return value.rise
    return value.fall


def _valid_input_transitions(
    timing_sense: TimingSense,
    output_transition: Transition,
) -> tuple[Transition, ...]:
    """Return all input transitions that can cause an output transition."""

    if timing_sense is TimingSense.POSITIVE_UNATE:
        return (output_transition,)
    if timing_sense is TimingSense.NEGATIVE_UNATE:
        opposite = (
            Transition.FALL
            if output_transition is Transition.RISE
            else Transition.RISE
        )
        return (opposite,)
    return (Transition.RISE, Transition.FALL)


def _output_slack_values(
    circuit: Circuit,
    transition_slacks: Mapping[str, RiseFall],
) -> list[float]:
    """Return the configured slack values of primary outputs."""

    output_slacks: list[float] = []
    for net_name, net in circuit.netlist.items():
        if net.net_type is not NetType.OUTPUT:
            continue

        slack = transition_slacks[net_name]
        if circuit.config.timing_analysis.separate_rise_fall:
            output_slacks.extend((slack.rise, slack.fall))
        else:
            output_slacks.append(min(slack.rise, slack.fall))
    return output_slacks


def _compute_arrival_times(
    circuit: Circuit,
    gate_delays: Mapping[str, RiseFall],
) -> tuple[dict[str, RiseFall], PredecessorTable]:
    """Propagate arrivals and record every exactly tied controlling arc."""

    arrival_times: dict[str, RiseFall] = {}
    predecessors: PredecessorTable = {}
    for net_name, net in circuit.netlist.items():
        if net.net_type is NetType.INPUT:
            configured_arrival = circuit.config.input_arrival(net_name)
            arrival_times[net_name] = RiseFall(
                configured_arrival.rise,
                configured_arrival.fall,
            )

    for level in circuit.topological_order:
        for gate in level:
            if gate.output is None:
                raise NetlistError(f"gate {gate.name!r} has no output net")
            rise_arrival, rise_predecessors = _select_predecessors(
                circuit,
                gate,
                Transition.RISE,
                arrival_times,
                gate_delays,
            )
            fall_arrival, fall_predecessors = _select_predecessors(
                circuit,
                gate,
                Transition.FALL,
                arrival_times,
                gate_delays,
            )
            arrival_times[gate.output.name] = RiseFall(
                rise_arrival,
                fall_arrival,
            )
            predecessors[(gate.output.name, Transition.RISE)] = rise_predecessors
            predecessors[(gate.output.name, Transition.FALL)] = fall_predecessors

    return arrival_times, predecessors


def _select_predecessors(
    circuit: Circuit,
    gate: Gate,
    output_transition: Transition,
    arrival_times: Mapping[str, RiseFall],
    gate_delays: Mapping[str, RiseFall],
) -> tuple[float, tuple[PredecessorArc, ...]]:
    """Return the maximum arrival and every arc tied exactly at that maximum."""

    gate_delay = _transition_value(
        gate_delays[gate.name],
        output_transition,
    )
    candidates: list[tuple[float, PredecessorArc]] = []
    for pin_number, input_net in enumerate(gate.inputs):
        timing_sense = gate.cell.input_pins[pin_number].timing_sense
        for input_transition in _valid_input_transitions(
            timing_sense,
            output_transition,
        ):
            candidate_arrival = (
                _transition_value(
                    arrival_times[input_net.name],
                    input_transition,
                )
                + gate_delay
            )
            candidates.append(
                (candidate_arrival, (pin_number, input_transition))
            )

    if not candidates:
        raise NetlistError(f"gate {gate.name!r} has no timing input arcs")

    maximum_arrival = max(arrival for arrival, _ in candidates)
    controlling_arcs = tuple(
        arc for arrival, arc in candidates if arrival == maximum_arrival
    )
    return maximum_arrival, controlling_arcs


def _compute_required_times(
    circuit: Circuit,
    gate_delays: Mapping[str, RiseFall],
) -> dict[str, RiseFall]:
    """Propagate rise and fall required times in reverse order."""

    required_times: dict[str, RiseFall] = {}
    for net_name, net in circuit.netlist.items():
        if net.net_type is NetType.OUTPUT:
            configured_required = circuit.config.output_required(net_name)
            required_times[net_name] = RiseFall(
                configured_required.rise,
                configured_required.fall,
            )

    infinite_required = RiseFall(float("inf"), float("inf"))
    for level in reversed(circuit.topological_order):
        for gate in level:
            if gate.output is None:
                raise NetlistError(f"gate {gate.name!r} has no output net")

            output_required = required_times.get(
                gate.output.name,
                infinite_required,
            )
            gate_delay = gate_delays[gate.name]

            for pin_number, input_net in enumerate(gate.inputs):
                timing_sense = gate.cell.input_pins[pin_number].timing_sense
                candidate = _propagate_required(
                    output_required,
                    gate_delay,
                    timing_sense,
                )
                existing_required = required_times.get(
                    input_net.name,
                    infinite_required,
                )
                required_times[input_net.name] = RiseFall(
                    min(existing_required.rise, candidate.rise),
                    min(existing_required.fall, candidate.fall),
                )

    return required_times


def _propagate_required(
    output_required: RiseFall,
    gate_delay: RiseFall,
    timing_sense: TimingSense,
) -> RiseFall:
    """Propagate a required time backward through one timing arc."""

    if timing_sense is TimingSense.POSITIVE_UNATE:
        return RiseFall(
            output_required.rise - gate_delay.rise,
            output_required.fall - gate_delay.fall,
        )
    if timing_sense is TimingSense.NEGATIVE_UNATE:
        return RiseFall(
            output_required.fall - gate_delay.fall,
            output_required.rise - gate_delay.rise,
        )

    earliest_candidate = min(
        output_required.rise - gate_delay.rise,
        output_required.fall - gate_delay.fall,
    )
    return RiseFall(earliest_candidate, earliest_candidate)


def _compute_transition_slacks(
    arrival_times: Mapping[str, RiseFall],
    required_times: Mapping[str, RiseFall],
) -> dict[str, RiseFall]:
    """Compute rise and fall slack for every circuit net."""

    return {
        net_name: RiseFall(
            required_times[net_name].rise - arrival.rise,
            required_times[net_name].fall - arrival.fall,
        )
        for net_name, arrival in arrival_times.items()
    }
