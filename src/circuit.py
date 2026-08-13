"""Circuit graph ownership and cached physical metrics."""

from __future__ import annotations

import logging

from .cell import Cell, CellLibrary, RiseFall
from .config import Config
from .netlist import Gate, Net, NetType, NetlistError


logger = logging.getLogger(__name__)


class Circuit:
    """Own a validated circuit graph and its cell-dependent cached metrics."""

    def __init__(
        self,
        netlist: dict[str, Net],
        gates: dict[str, Gate],
        config: Config,
        cell_library: CellLibrary,
    ) -> None:
        self.netlist = netlist
        self.gates = gates
        self.config = config
        self.cell_library = cell_library
        logger.info(
            "Preparing circuit %s (%d gates, %d nets)",
            config.circuit_name,
            len(gates),
            len(netlist),
        )

        logger.info("Building the gate topological order")
        self.topological_order = self._make_topological_order()
        logger.info("Computing gate fanout capacitances")
        self.fanout_capacitances = self._compute_fanout_capacitances()
        logger.info("Computing gate rise/fall delays")
        self.gate_delays = self._compute_gate_delays()
        self.area = self._compute_area()
        logger.info("Computing circuit power")
        self.leakage_power, self.dynamic_power = self._compute_power()
        self.power = self.leakage_power + self.dynamic_power

    def _compute_area(self) -> float:
        """Return the total area of all instantiated gates."""

        return sum(gate.cell.area for gate in self.gates.values())

    def _compute_power(self) -> tuple[float, float]:
        """Return total leakage and activity-based dynamic power in uW."""

        conditions = self.config.operating_conditions
        voltage_squared = conditions.supply_voltage**2
        conversion_factor = self.cell_library.dynamic_power_to_uW_factor
        leakage_power = 0.0
        dynamic_power = 0.0

        for gate in self.gates.values():
            if gate.output is None:
                raise NetlistError(f"gate {gate.name!r} has no output net")

            leakage_power += gate.cell.leakage_power
            switching_capacitance = (
                self.fanout_capacitances[gate.name]
                + gate.cell.internal_capacitance
            )
            dynamic_power += (
                self.config.activity_factor(gate.output.name)
                * switching_capacitance
                * voltage_squared
                * conditions.frequency_hz
                * conversion_factor
            )

        return leakage_power, dynamic_power

    def _compute_fanout_capacitances(self) -> dict[str, float]:
        """Return the total load capacitance at each gate output."""

        cap_fanout: dict[str, float] = {}
        for level in self.topological_order:
            for gate in level:
                net = gate.output
                if net is None:
                    raise NetlistError(f"gate {gate.name!r} has no output net")

                total_cap = 0.0
                if net.net_type is NetType.OUTPUT:
                    total_cap += self.config.output_load(net.name)
                for pin_number, load_gate in net.loads:
                    total_cap += load_gate.cell.input_pins[pin_number].capacitance
                cap_fanout[gate.name] = total_cap

        return cap_fanout

    def _compute_gate_delays(self) -> dict[str, RiseFall]:
        """Compute the rise and fall propagation delay of each gate."""

        gate_delays: dict[str, RiseFall] = {}
        kappa = self.cell_library.delay_unit_conversion_kappa
        for gate in self.gates.values():
            load_capacitance = self.fanout_capacitances[gate.name]
            rise_delay = gate.cell.intrinsic_delay.rise + (
                kappa * gate.cell.output_resistance.rise * load_capacitance
            )
            fall_delay = gate.cell.intrinsic_delay.fall + (
                kappa * gate.cell.output_resistance.fall * load_capacitance
            )
            gate_delays[gate.name] = RiseFall(rise_delay, fall_delay)

        return gate_delays

    def _make_topological_order(self) -> list[list[Gate]]:
        """Group gates into levels that can be evaluated in parallel."""

        dependencies: dict[str, set[str]] = {}
        successors: dict[str, list[str]] = {name: [] for name in self.gates}
        gate_position = {name: index for index, name in enumerate(self.gates)}

        for gate_name, gate in self.gates.items():
            gate_dependencies: set[str] = set()
            for input_net in gate.inputs:
                if input_net.driver is not None:
                    gate_dependencies.add(input_net.driver.name)
            dependencies[gate_name] = gate_dependencies
            for dependency_name in gate_dependencies:
                successors[dependency_name].append(gate_name)

        ready = [name for name in self.gates if not dependencies[name]]
        levels: list[list[Gate]] = []
        ordered_gate_count = 0

        while ready:
            levels.append([self.gates[name] for name in ready])
            ordered_gate_count += len(ready)
            next_level: list[str] = []

            for gate_name in ready:
                for successor_name in successors[gate_name]:
                    dependencies[successor_name].remove(gate_name)
                    if not dependencies[successor_name]:
                        next_level.append(successor_name)
            next_level.sort(key=gate_position.__getitem__)
            ready = next_level

        if ordered_gate_count != len(self.gates):
            cyclic = sorted(
                name for name, pending in dependencies.items() if pending
            )
            raise NetlistError(
                f"combinational loop involving gates: {', '.join(cyclic)}",
                "combinational_loop",
            )

        return levels


def replace_gate_cell(
    circuit: Circuit,
    gate_name: str,
    replacement: Cell,
) -> Circuit:
    """Return an independent circuit with one gate cell replaced."""

    original_gate = circuit.gates.get(gate_name)
    if original_gate is None:
        raise NetlistError(f"circuit has no gate {gate_name!r}")
    if replacement.family != original_gate.cell.family:
        raise NetlistError(
            f"cannot replace {original_gate.cell.name!r} with different-family "
            f"cell {replacement.name!r}"
        )
    if replacement.num_inputs != len(original_gate.inputs):
        raise NetlistError(
            f"replacement cell {replacement.name!r} has an incompatible input count"
        )
    if circuit.cell_library.find(replacement.name) is not replacement:
        raise NetlistError(
            f"replacement cell {replacement.name!r} is not from the circuit library"
        )

    nets = {
        name: Net(net_type=net.net_type, name=name)
        for name, net in circuit.netlist.items()
    }
    gates: dict[str, Gate] = {}
    for name, gate in circuit.gates.items():
        cell = replacement if name == gate_name else gate.cell
        cloned_gate = Gate(
            cell=cell,
            inputs=[nets[input_net.name] for input_net in gate.inputs],
            output=nets[gate.output.name] if gate.output is not None else None,
            name=name,
        )
        if cloned_gate.output is None:
            raise NetlistError(f"gate {name!r} has no output net")
        cloned_gate.output.driver = cloned_gate
        for pin_number, input_net in enumerate(cloned_gate.inputs):
            input_net.loads.append((pin_number, cloned_gate))
        gates[name] = cloned_gate

    return Circuit(nets, gates, circuit.config, circuit.cell_library)
