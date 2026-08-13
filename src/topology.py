"""Topology-only circuit export for interactive graph consumers."""

from __future__ import annotations

from .circuit import Circuit
from .experiment_artifacts import ArtifactWriter
from .netlist import Gate, Net, NetType, NetlistError
from .sta import CriticalPath, TimingAnalysisResult


TOPOLOGY_SCHEMA_VERSION = 2


def circuit_topology(circuit: Circuit) -> dict[str, object]:
    """Return circuit connectivity without electrical or sizing attributes."""

    levels = {
        gate.name: level
        for level, gates in enumerate(circuit.topological_order, start=1)
        for gate in gates
    }
    output_level = len(circuit.topological_order) + 1
    nodes: list[dict[str, object]] = []

    for net in circuit.netlist.values():
        if net.net_type is NetType.INPUT:
            nodes.append(_port_node(net, "input", 0))
    for gate in circuit.gates.values():
        nodes.append(_gate_node(gate, levels[gate.name]))
    for net in circuit.netlist.values():
        if net.net_type is NetType.OUTPUT:
            nodes.append(_port_node(net, "output", output_level))

    nets = [_net_record(net) for net in circuit.netlist.values()]
    return {
        "schema_version": 1,
        "circuit_name": circuit.config.circuit_name,
        "counts": {
            "nodes": len(nodes),
            "gates": len(circuit.gates),
            "nets": len(nets),
            "primary_inputs": sum(
                net.net_type is NetType.INPUT for net in circuit.netlist.values()
            ),
            "primary_outputs": sum(
                net.net_type is NetType.OUTPUT for net in circuit.netlist.values()
            ),
        },
        "nodes": nodes,
        "nets": nets,
    }


def circuit_analysis_graph(
    original: Circuit,
    original_timing: TimingAnalysisResult,
    optimized: Circuit,
    optimized_timing: TimingAnalysisResult,
) -> dict[str, object]:
    """Return shared topology with original and optimized analysis overlays."""

    topology = circuit_topology(original)
    topology["schema_version"] = TOPOLOGY_SCHEMA_VERSION
    topology["units"] = {
        "capacitance": original.cell_library.units.get("capacitance", "fF"),
        "time": original.cell_library.units.get("time", "ns"),
    }
    topology["states"] = {
        "original": _analysis_state(original, original_timing),
        "optimized": _analysis_state(optimized, optimized_timing),
    }
    return topology


def save_circuit_topology(
    writer: ArtifactWriter,
    original: Circuit,
    original_timing: TimingAnalysisResult,
    optimized: Circuit,
    optimized_timing: TimingAnalysisResult,
) -> None:
    """Write the comparison graph artifact consumed by the interactive viewer."""

    writer.write_json(
        "circuit_topology",
        circuit_analysis_graph(
            original,
            original_timing,
            optimized,
            optimized_timing,
        ),
    )


def _analysis_state(
    circuit: Circuit,
    timing: TimingAnalysisResult,
) -> dict[str, object]:
    return {
        "gates": {
            gate_name: {
                "cell": circuit.gates[gate_name].cell.name,
                "size": circuit.gates[gate_name].cell.size,
                "load_capacitance": circuit.fanout_capacitances[gate_name],
                "delay_rise": circuit.gate_delays[gate_name].rise,
                "delay_fall": circuit.gate_delays[gate_name].fall,
            }
            for gate_name in circuit.gates
        },
        "critical_paths": [
            _critical_path(rank, critical_path)
            for rank, critical_path in enumerate(timing.critical_paths, start=1)
        ],
    }


def _critical_path(rank: int, critical_path: CriticalPath) -> dict[str, object]:
    path = critical_path.path
    net_names = [path.input_net.name]
    for step in path.steps:
        if step.gate.output is None:
            raise NetlistError(f"gate {step.gate.name!r} has no output")
        net_names.append(step.gate.output.name)
    return {
        "rank": rank,
        "slack": critical_path.slack,
        "input": path.input_net.name,
        "output": path.output_net.name,
        "gates": [step.gate.name for step in path.steps],
        "nets": net_names,
    }


def _port_node(net: Net, kind: str, level: int) -> dict[str, object]:
    return {
        "id": f"{kind}:{net.name}",
        "kind": kind,
        "name": net.name,
        "level": level,
    }


def _gate_node(gate: Gate, level: int) -> dict[str, object]:
    return {
        "id": f"gate:{gate.name}",
        "kind": "gate",
        "name": gate.name,
        "gate_type": gate.cell.family,
        "level": level,
    }


def _net_record(net: Net) -> dict[str, object]:
    targets = [
        {"node": f"gate:{gate.name}", "pin": pin_number}
        for pin_number, gate in net.loads
    ]
    if net.net_type is NetType.OUTPUT:
        targets.append({"node": f"output:{net.name}", "pin": None})
    return {
        "id": f"net:{net.name}",
        "name": net.name,
        "source": _net_source(net),
        "targets": targets,
    }


def _net_source(net: Net) -> str:
    if net.net_type is NetType.INPUT:
        return f"input:{net.name}"
    if net.driver is None:
        raise NetlistError(f"net {net.name!r} has no driver")
    return f"gate:{net.driver.name}"
