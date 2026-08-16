"""Deterministic circuit-DAG construction and timing visualization."""

from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from pathlib import Path
from typing import Any, cast

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import networkx as nx
from matplotlib.axes import Axes
from matplotlib.figure import Figure
from matplotlib.lines import Line2D

from .circuit import Circuit
from .netlist import Gate, Net, NetType, NetlistError
from .sta import TimingAnalysisResult


@dataclass(frozen=True)
class _NodeGroups:
    gates: list[str]
    inputs: list[str]
    outputs: list[str]


@dataclass(frozen=True)
class _GraphStyle:
    figure_size: tuple[float, float]
    base_gate_size: int
    port_size: int
    node_font_size: int
    guide_font_size: int


@dataclass(frozen=True)
class _PathEdges:
    regular: list[tuple[str, str]]
    other_critical: list[tuple[str, str]]
    worst: list[tuple[str, str]]


def build_circuit_graph(circuit: Circuit) -> tuple[Any, dict[str, tuple[float, float]]]:
    """Build a deterministic PI/gate/PO DAG and node positions."""

    graph = nx.DiGraph()
    positions: dict[str, tuple[float, float]] = {}
    inputs = _net_names(circuit, NetType.INPUT)
    outputs = _net_names(circuit, NetType.OUTPUT)

    for index, name in enumerate(inputs):
        _add_port(graph, positions, "pi", name, 0.0, index, "input")
    for level_index, level in enumerate(circuit.topological_order, start=1):
        for row, gate in enumerate(level):
            _add_gate(circuit, graph, positions, gate, level_index, row)
    output_level = float(len(circuit.topological_order) + 1)
    for index, name in enumerate(outputs):
        _add_port(graph, positions, "po", name, output_level, index, "output")
        driver = circuit.netlist[name].driver
        if driver is not None:
            graph.add_edge(f"gate:{driver}", f"po:{name}", net=name)
    return graph, positions


def timing_slack_extent(*results: TimingAnalysisResult) -> float:
    """Return one symmetric nonzero color limit shared by several analyses."""

    values = [
        abs(value)
        for result in results
        for slack in result.transition_slacks.values()
        for value in (slack.rise, slack.fall)
        if value != float("inf")
    ]
    return max(values, default=1.0e-12)


def draw_circuit_graph(
    circuit: Circuit,
    timing: TimingAnalysisResult,
    output_path: Path,
    slack_extent: float,
) -> None:
    """Render a circuit DAG with slack colors and top-K path highlights."""

    graph, positions = build_circuit_graph(circuit)
    nodes = _group_nodes(graph)
    style = _graph_style(circuit)
    path_edges = _classify_edges(graph, timing)
    labels = _node_labels(circuit, graph, nodes, timing)
    figure, axis = _new_figure(style)

    _draw_edges(graph, positions, path_edges, axis)
    gate_collection = _draw_nodes(
        circuit, graph, positions, nodes, timing, slack_extent, style, axis
    )
    nx.draw_networkx_labels(
        graph,
        positions,
        labels=labels,
        font_size=style.node_font_size,
        font_weight="medium",
        ax=axis,
    )
    _decorate_figure(figure, axis, gate_collection, circuit, style)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(str(output_path), dpi=180)
    plt.close(figure)


def _net_names(circuit: Circuit, net_type: NetType) -> list[str]:
    return [
        name for name, net in circuit.netlist.items() if net.net_type is net_type
    ]


def _add_port(
    graph: Any,
    positions: dict[str, tuple[float, float]],
    prefix: str,
    name: str,
    level: float,
    row: int,
    kind: str,
) -> None:
    node = f"{prefix}:{name}"
    graph.add_node(node, kind=kind, label=name)
    positions[node] = level, -float(row)


def _add_gate(
    circuit: Circuit,
    graph: Any,
    positions: dict[str, tuple[float, float]],
    gate: Gate,
    level: int,
    row: int,
) -> None:
    node = f"gate:{gate.name}"
    graph.add_node(node, kind="gate", gate=gate)
    positions[node] = float(level), -float(row)
    for input_name in gate.inputs:
        graph.add_edge(
            _input_source(circuit.netlist[input_name]), node, net=input_name
        )


def _input_source(input_net: Net) -> str:
    if input_net.net_type is NetType.INPUT:
        return f"pi:{input_net.name}"
    if input_net.driver is None:
        raise NetlistError(f"net {input_net.name!r} has no driver")
    return f"gate:{input_net.driver}"


def _group_nodes(graph: Any) -> _NodeGroups:
    by_kind = {
        kind: [
            node
            for node, data in graph.nodes(data=True)
            if data["kind"] == kind
        ]
        for kind in ("gate", "input", "output")
    }
    return _NodeGroups(by_kind["gate"], by_kind["input"], by_kind["output"])


def _graph_style(circuit: Circuit) -> _GraphStyle:
    is_large = len(circuit.gates) >= 50
    width = max(14.0, 2.15 * (len(circuit.topological_order) + 2))
    height = max(9.0, min(24.0, 0.42 * max(len(circuit.gates), 12)))
    return _GraphStyle(
        figure_size=(width, height),
        base_gate_size=3800 if is_large else 2600,
        port_size=2200 if is_large else 1600,
        node_font_size=9 if is_large else 8,
        guide_font_size=15 if is_large else 12,
    )


def _classify_edges(graph: Any, timing: TimingAnalysisResult) -> _PathEdges:
    ranks: dict[tuple[str, str], int] = {}
    for rank, critical_path in enumerate(timing.critical_paths, start=1):
        path = critical_path.path
        nodes = [f"pi:{path.input_net.name}"]
        nodes.extend(f"gate:{step.gate.name}" for step in path.steps)
        nodes.append(f"po:{path.output_net.name}")
        for edge in zip(nodes, nodes[1:]):
            ranks[edge] = min(rank, ranks.get(edge, rank))
    return _PathEdges(
        regular=[edge for edge in graph.edges if edge not in ranks],
        other_critical=[edge for edge, rank in ranks.items() if rank > 1],
        worst=[edge for edge, rank in ranks.items() if rank == 1],
    )


def _node_labels(
    circuit: Circuit,
    graph: Any,
    nodes: _NodeGroups,
    timing: TimingAnalysisResult,
) -> dict[str, str]:
    labels = {
        node: _gate_label(circuit, graph.nodes[node]["gate"], timing)
        for node in nodes.gates
    }
    labels.update(
        {node: graph.nodes[node]["label"] for node in nodes.inputs + nodes.outputs}
    )
    return labels


def _gate_label(
    circuit: Circuit,
    gate: Gate,
    timing: TimingAnalysisResult,
) -> str:
    slack = timing.node_slack(_gate_output_name(gate))
    return f"{gate.name}\n{circuit.cell_for(gate).name}\nS={slack:.4g} ns"


def _new_figure(style: _GraphStyle) -> tuple[Figure, Axes]:
    raw_figure, raw_axis = plt.subplots(
        figsize=style.figure_size,
        constrained_layout=True,
    )
    return cast(Figure, raw_figure), cast(Axes, raw_axis)


def _draw_edges(
    graph: Any,
    positions: dict[str, tuple[float, float]],
    edges: _PathEdges,
    axis: Axes,
) -> None:
    specifications = (
        (edges.regular, "#aab2bd", 1.0, 0.65),
        (edges.other_critical, "#f39c12", 2.8, 1.0),
        (edges.worst, "#c0392b", 4.5, 1.0),
    )
    for edge_list, color, width, alpha in specifications:
        nx.draw_networkx_edges(
            graph,
            positions,
            edgelist=edge_list,
            edge_color=color,
            width=width,
            alpha=alpha,
            ax=axis,
        )


def _draw_nodes(
    circuit: Circuit,
    graph: Any,
    positions: dict[str, tuple[float, float]],
    nodes: _NodeGroups,
    timing: TimingAnalysisResult,
    slack_extent: float,
    style: _GraphStyle,
    axis: Axes,
) -> Any:
    _draw_ports(graph, positions, nodes.inputs, "s", "#d6eaf8", "#2874a6", style, axis)
    _draw_ports(graph, positions, nodes.outputs, "D", "#e8daef", "#7d3c98", style, axis)
    gates = [cast(Gate, graph.nodes[node]["gate"]) for node in nodes.gates]
    slacks = [timing.node_slack(_gate_output_name(gate)) for gate in gates]
    size_factors = [circuit.cell_for(gate).size_factor for gate in gates]
    sizes = [_gate_node_area(style, factor) for factor in size_factors]
    outlines = [_gate_outline_width(factor) for factor in size_factors]
    return nx.draw_networkx_nodes(
        graph,
        positions,
        nodelist=nodes.gates,
        node_shape="o",
        node_color=cast(Any, slacks),
        cmap=plt.get_cmap("RdYlGn"),
        vmin=-slack_extent,
        vmax=slack_extent,
        edgecolors="#34495e",
        linewidths=outlines,
        node_size=cast(Any, sizes),
        ax=axis,
    )


def _draw_ports(
    graph: Any,
    positions: dict[str, tuple[float, float]],
    nodes: list[str],
    shape: str,
    fill: str,
    outline: str,
    style: _GraphStyle,
    axis: Axes,
) -> None:
    nx.draw_networkx_nodes(
        graph,
        positions,
        nodelist=nodes,
        node_shape=shape,
        node_color=fill,
        edgecolors=outline,
        linewidths=1.5,
        node_size=style.port_size,
        ax=axis,
    )


def _decorate_figure(
    figure: Figure,
    axis: Axes,
    gate_collection: Any,
    circuit: Circuit,
    style: _GraphStyle,
) -> None:
    colorbar = figure.colorbar(
        gate_collection,
        ax=axis,
        fraction=0.035,
        pad=0.025,
    )
    colorbar.set_label(
        "Worst rise/fall slack (ns)",
        fontsize=style.guide_font_size,
        fontweight="semibold",
        labelpad=14,
    )
    colorbar.ax.tick_params(
        labelsize=style.guide_font_size - 1,
        width=1.2,
        length=6,
    )
    axis.legend(
        handles=_legend_handles(),
        loc="upper right",
        fontsize=style.guide_font_size,
        framealpha=0.95,
        borderpad=0.8,
        labelspacing=0.6,
    )
    axis.set_title(
        f"{circuit.config.circuit_name} timing DAG",
        fontsize=style.guide_font_size + 4,
        fontweight="semibold",
        pad=18,
    )
    axis.set_axis_off()


def _legend_handles() -> list[Line2D]:
    handles = [
        Line2D([0], [0], color="#c0392b", linewidth=4.5, label="Rank-1 critical path"),
        Line2D([0], [0], color="#f39c12", linewidth=2.8, label="Other top-K paths"),
        _port_legend("s", "#d6eaf8", "#2874a6", 12, "Primary input"),
        _port_legend("D", "#e8daef", "#7d3c98", 11, "Primary output"),
    ]
    handles.extend(_size_legend(size_factor) for size_factor in (1.0, 2.0, 4.0))
    return handles


def _port_legend(
    marker: str,
    fill: str,
    outline: str,
    size: float,
    label: str,
) -> Line2D:
    return Line2D(
        [0],
        [0],
        marker=marker,
        color="w",
        markerfacecolor=fill,
        markeredgecolor=outline,
        markersize=size,
        label=label,
    )


def _size_legend(size_factor: float) -> Line2D:
    return Line2D(
        [0],
        [0],
        marker="o",
        color="w",
        markerfacecolor="#bdc3c7",
        markeredgecolor="#34495e",
        markeredgewidth=_gate_outline_width(size_factor),
        markersize=8.0 * sqrt(size_factor),
        label=f"X{size_factor:g} cell size",
    )


def _gate_node_area(style: _GraphStyle, size_factor: float) -> float:
    """Scale marker area so stronger cells are visibly larger."""

    return style.base_gate_size * size_factor


def _gate_outline_width(size_factor: float) -> float:
    """Give progressively stronger cells progressively bolder outlines."""

    return 1.5 + 1.25 * (size_factor - 1.0)


def _gate_output_name(gate: Gate) -> str:
    return gate.output
