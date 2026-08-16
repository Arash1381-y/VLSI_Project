"""Immutable circuit topology, cell assignments, and physical metrics."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .cell import Cell, CellLibrary, RiseFall
from .config import Config
from .netlist import Gate, Net, NetType, NetlistError


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CircuitTopology:
    """Cell-independent circuit structure shared by every sizing variant."""

    netlist: Mapping[str, Net]
    gates: Mapping[str, Gate]
    topological_order: tuple[tuple[Gate, ...], ...]

    @classmethod
    def create(
        cls,
        netlist: Mapping[str, Net],
        gates: Mapping[str, Gate],
    ) -> CircuitTopology:
        frozen_nets = MappingProxyType(dict(netlist))
        frozen_gates = MappingProxyType(dict(gates))
        order = _make_topological_order(frozen_nets, frozen_gates)
        return cls(frozen_nets, frozen_gates, order)


@dataclass(frozen=True)
class _ReplacementMetrics:
    area: float
    leakage_power: float
    dynamic_power: float

    @property
    def power(self) -> float:
        return self.leakage_power + self.dynamic_power


class Circuit:
    """Own one immutable cell assignment over shared circuit topology."""

    def __init__(
        self,
        netlist: Mapping[str, Net],
        gates: Mapping[str, Gate],
        gate_cells: Mapping[str, Cell],
        config: Config,
        cell_library: CellLibrary,
    ) -> None:
        topology = CircuitTopology.create(netlist, gates)
        cells = _validated_cell_assignments(topology, gate_cells, cell_library)
        logger.info(
            "Preparing circuit %s (%d gates, %d nets)",
            config.circuit_name,
            len(gates),
            len(netlist),
        )

        self.topology = topology
        self.netlist = topology.netlist
        self.gates = topology.gates
        self.topological_order = topology.topological_order
        self.gate_cells = cells
        self.config = config
        self.cell_library = cell_library

        logger.info("Computing gate fanout capacitances")
        self.fanout_capacitances = MappingProxyType(
            self._compute_fanout_capacitances()
        )
        logger.info("Computing gate rise/fall delays")
        self.gate_delays = MappingProxyType(self._compute_gate_delays())
        self.area = self._compute_area()
        logger.info("Computing circuit power")
        self.leakage_power, self.dynamic_power = self._compute_power()
        self.power = self.leakage_power + self.dynamic_power

    @classmethod
    def _from_variant(
        cls,
        original: Circuit,
        gate_cells: Mapping[str, Cell],
        fanout_capacitances: Mapping[str, float],
        gate_delays: Mapping[str, RiseFall],
        metrics: _ReplacementMetrics,
    ) -> Circuit:
        """Build a variant from shared topology and precomputed metrics."""

        variant = cls.__new__(cls)
        variant.topology = original.topology
        variant.netlist = original.netlist
        variant.gates = original.gates
        variant.topological_order = original.topological_order
        variant.gate_cells = MappingProxyType(dict(gate_cells))
        variant.config = original.config
        variant.cell_library = original.cell_library
        variant.fanout_capacitances = MappingProxyType(
            dict(fanout_capacitances)
        )
        variant.gate_delays = MappingProxyType(dict(gate_delays))
        variant.area = metrics.area
        variant.leakage_power = metrics.leakage_power
        variant.dynamic_power = metrics.dynamic_power
        variant.power = metrics.power
        return variant

    def cell_for(self, gate: Gate | str) -> Cell:
        """Return this circuit variant's cell for a structural gate."""

        gate_name = gate if isinstance(gate, str) else gate.name
        try:
            return self.gate_cells[gate_name]
        except KeyError as exc:
            raise NetlistError(f"circuit has no gate {gate_name!r}") from exc

    def input_net(self, gate: Gate, pin_number: int) -> Net:
        """Return the net connected to one structural gate input pin."""

        return self.netlist[gate.inputs[pin_number]]

    def output_net(self, gate: Gate) -> Net:
        """Return the net driven by one structural gate."""

        return self.netlist[gate.output]

    def with_gate_cell(self, gate_name: str, replacement: Cell) -> Circuit:
        """Return a sizing variant with incrementally updated metrics."""

        gate = _validated_replacement_gate(self, gate_name, replacement)
        original_cell = self.cell_for(gate)
        if replacement is original_cell:
            return self

        gate_cells = dict(self.gate_cells)
        gate_cells[gate_name] = replacement
        fanouts = dict(self.fanout_capacitances)
        affected_predecessors: set[str] = set()
        for pin_number, input_name in enumerate(gate.inputs):
            driver_name = self.netlist[input_name].driver
            if driver_name is None:
                continue
            capacitance_delta = (
                replacement.input_pins[pin_number].capacitance
                - original_cell.input_pins[pin_number].capacitance
            )
            fanouts[driver_name] += capacitance_delta
            affected_predecessors.add(driver_name)

        delays = dict(self.gate_delays)
        for affected_name in affected_predecessors | {gate_name}:
            delays[affected_name] = _gate_delay(
                gate_cells[affected_name],
                fanouts[affected_name],
                self.cell_library.delay_unit_conversion_kappa,
            )

        metrics = _replacement_metrics(self, gate, replacement)
        return type(self)._from_variant(
            self, gate_cells, fanouts, delays, metrics
        )

    def _compute_area(self) -> float:
        return sum(self.cell_for(gate).area for gate in self.gates.values())

    def _compute_power(self) -> tuple[float, float]:
        conditions = self.config.operating_conditions
        dynamic_factor = (
            conditions.supply_voltage**2
            * conditions.frequency_hz
            * self.cell_library.dynamic_power_to_uW_factor
        )
        leakage_power = 0.0
        dynamic_power = 0.0
        for gate in self.gates.values():
            cell = self.cell_for(gate)
            leakage_power += cell.leakage_power
            switching_capacitance = (
                self.fanout_capacitances[gate.name]
                + cell.internal_capacitance
            )
            dynamic_power += (
                self.config.activity_factor(gate.output)
                * switching_capacitance
                * dynamic_factor
            )
        return leakage_power, dynamic_power

    def _compute_fanout_capacitances(self) -> dict[str, float]:
        fanouts: dict[str, float] = {}
        for level in self.topological_order:
            for gate in level:
                net = self.output_net(gate)
                total_capacitance = 0.0
                if net.net_type is NetType.OUTPUT:
                    total_capacitance += self.config.output_load(net.name)
                for pin_number, load_name in net.loads:
                    load_cell = self.cell_for(load_name)
                    total_capacitance += load_cell.input_pins[
                        pin_number
                    ].capacitance
                fanouts[gate.name] = total_capacitance
        return fanouts

    def _compute_gate_delays(self) -> dict[str, RiseFall]:
        return {
            gate.name: _gate_delay(
                self.cell_for(gate),
                self.fanout_capacitances[gate.name],
                self.cell_library.delay_unit_conversion_kappa,
            )
            for gate in self.gates.values()
        }


def replace_gate_cell(
    circuit: Circuit,
    gate_name: str,
    replacement: Cell,
) -> Circuit:
    """Return an immutable circuit variant with one gate cell replaced."""

    return circuit.with_gate_cell(gate_name, replacement)


def replacement_area_and_power(
    circuit: Circuit,
    gate_name: str,
    replacement: Cell,
) -> tuple[float, float]:
    """Return delta-updated area and power without constructing a variant."""

    gate = _validated_replacement_gate(circuit, gate_name, replacement)
    metrics = _replacement_metrics(circuit, gate, replacement)
    return metrics.area, metrics.power


def _replacement_metrics(
    circuit: Circuit,
    gate: Gate,
    replacement: Cell,
) -> _ReplacementMetrics:
    """Update area and power from only the resized gate and its drivers."""

    original_cell = circuit.cell_for(gate)
    area = circuit.area - original_cell.area + replacement.area
    leakage_power = (
        circuit.leakage_power
        - original_cell.leakage_power
        + replacement.leakage_power
    )
    conditions = circuit.config.operating_conditions
    dynamic_factor = (
        conditions.supply_voltage**2
        * conditions.frequency_hz
        * circuit.cell_library.dynamic_power_to_uW_factor
    )
    dynamic_power = circuit.dynamic_power + (
        circuit.config.activity_factor(gate.output)
        * (replacement.internal_capacitance - original_cell.internal_capacitance)
        * dynamic_factor
    )
    for pin_number, input_name in enumerate(gate.inputs):
        driver_name = circuit.netlist[input_name].driver
        if driver_name is None:
            continue
        capacitance_delta = (
            replacement.input_pins[pin_number].capacitance
            - original_cell.input_pins[pin_number].capacitance
        )
        driver_output = circuit.gates[driver_name].output
        dynamic_power += (
            circuit.config.activity_factor(driver_output)
            * capacitance_delta
            * dynamic_factor
        )
    return _ReplacementMetrics(area, leakage_power, dynamic_power)


def _validated_replacement_gate(
    circuit: Circuit,
    gate_name: str,
    replacement: Cell,
) -> Gate:
    try:
        gate = circuit.gates[gate_name]
    except KeyError as exc:
        raise NetlistError(f"circuit has no gate {gate_name!r}") from exc
    original_cell = circuit.cell_for(gate)
    if replacement.family != gate.cell_family:
        raise NetlistError(
            f"cannot replace {original_cell.name!r} with different-family "
            f"cell {replacement.name!r}"
        )
    if replacement.num_inputs != len(gate.inputs):
        raise NetlistError(
            f"replacement cell {replacement.name!r} has an incompatible input count"
        )
    if circuit.cell_library.find(replacement.name) is not replacement:
        raise NetlistError(
            f"replacement cell {replacement.name!r} is not from the circuit library"
        )
    return gate


def _validated_cell_assignments(
    topology: CircuitTopology,
    gate_cells: Mapping[str, Cell],
    cell_library: CellLibrary,
) -> Mapping[str, Cell]:
    if set(gate_cells) != set(topology.gates):
        raise NetlistError("cell assignments must exactly match topology gates")
    for gate_name, gate in topology.gates.items():
        cell = gate_cells[gate_name]
        if cell_library.find(cell.name) is not cell:
            raise NetlistError(
                f"cell {cell.name!r} assigned to {gate_name!r} is not from the library"
            )
        if cell.family != gate.cell_family:
            raise NetlistError(
                f"cell {cell.name!r} is not equivalent to the "
                f"{gate.cell_family!r} gate {gate_name!r}"
            )
        if cell.num_inputs != len(gate.inputs):
            raise NetlistError(
                f"cell {cell.name!r} has an incompatible input count for {gate_name!r}"
            )
    return MappingProxyType(dict(gate_cells))


def _gate_delay(cell: Cell, load_capacitance: float, kappa: float) -> RiseFall:
    return RiseFall(
        cell.intrinsic_delay.rise
        + kappa * cell.output_resistance.rise * load_capacitance,
        cell.intrinsic_delay.fall
        + kappa * cell.output_resistance.fall * load_capacitance,
    )


def _make_topological_order(
    netlist: Mapping[str, Net],
    gates: Mapping[str, Gate],
) -> tuple[tuple[Gate, ...], ...]:
    dependencies: dict[str, set[str]] = {}
    successors: dict[str, list[str]] = {name: [] for name in gates}
    gate_position = {name: index for index, name in enumerate(gates)}
    for gate_name, gate in gates.items():
        gate_dependencies = {
            driver_name
            for input_name in gate.inputs
            if (driver_name := netlist[input_name].driver) is not None
        }
        dependencies[gate_name] = gate_dependencies
        for dependency_name in gate_dependencies:
            successors[dependency_name].append(gate_name)

    ready = [name for name in gates if not dependencies[name]]
    levels: list[tuple[Gate, ...]] = []
    ordered_gate_count = 0
    while ready:
        levels.append(tuple(gates[name] for name in ready))
        ordered_gate_count += len(ready)
        next_level: list[str] = []
        for gate_name in ready:
            for successor_name in successors[gate_name]:
                dependencies[successor_name].remove(gate_name)
                if not dependencies[successor_name]:
                    next_level.append(successor_name)
        next_level.sort(key=gate_position.__getitem__)
        ready = next_level

    if ordered_gate_count != len(gates):
        cyclic = sorted(name for name, pending in dependencies.items() if pending)
        raise NetlistError(
            f"combinational loop involving gates: {', '.join(cyclic)}",
            "combinational_loop",
        )
    return tuple(levels)
