"""Gate-level netlist models, parsing, and structural validation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from vlsi_sta.domain.cell import Cell, CellLibrary


class NetlistError(ValueError):
    """Raised when a netlist is syntactically or structurally invalid."""

    def __init__(
        self,
        message: str,
        code: str = "netlist_validation_error",
        path: Path | None = None,
        line: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.path = path
        self.line = line


class NetType(Enum):
    INPUT = 0
    INTERNAL = 1
    OUTPUT = 2


@dataclass(frozen=True)
class Net:
    """Immutable net connectivity expressed through gate instance names."""

    name: str
    net_type: NetType
    driver: str | None
    loads: tuple[tuple[int, str], ...]


@dataclass(frozen=True)
class Gate:
    """Immutable logical gate independent of its discrete size assignment."""

    name: str
    cell_family: str
    inputs: tuple[str, ...]
    output: str


@dataclass
class _NetBuilder:
    """Mutable parser-only state finalized into a frozen :class:`Net`."""

    net_type: NetType = NetType.INTERNAL
    driver: str | None = None
    loads: list[tuple[int, str]] = field(default_factory=list)


@dataclass(frozen=True, eq=False)
class PathStep:
    """One gate traversal through a specific input pin."""

    gate: Gate
    input_pin: int


@dataclass(frozen=True, eq=False)
class CircuitPath:
    """A pin-sensitive path from a primary input to a primary output."""

    input_net: Net
    steps: tuple[PathStep, ...]
    output_net: Net

    @property
    def gates(self) -> tuple[Gate, ...]:
        return tuple(step.gate for step in self.steps)


class NetListParser:
    def __init__(
        self,
        netlist_path: str | Path,
        cell_library: CellLibrary | Mapping[str, Cell],
    ) -> None:
        self.netlist_path = Path(netlist_path)
        self.cell_library = cell_library
        self._nets: dict[str, _NetBuilder] = {}
        self.gates: dict[str, Gate] = {}
        self.gate_cells: dict[str, Cell] = {}
        self._input_names: set[str] = set()
        self._output_names: set[str] = set()

    def parse(
        self,
    ) -> tuple[dict[str, Net], dict[str, Gate], dict[str, Cell]]:
        """Parse, connect, and validate the gate-level netlist."""

        self._nets.clear()
        self.gates.clear()
        self.gate_cells.clear()
        self._input_names.clear()
        self._output_names.clear()

        try:
            with self.netlist_path.open("r", encoding="utf-8") as netlist_file:
                for line_number, raw_line in enumerate(netlist_file, start=1):
                    line = raw_line.partition("#")[0].strip()
                    if not line:
                        continue
                    self._parse_line(line.split(), line_number)
        except OSError as exc:
            raise NetlistError(
                f"cannot read netlist {self.netlist_path}: {exc}"
            ) from exc

        self._validate_drivers()
        netlist = {
            name: Net(
                name=name,
                net_type=builder.net_type,
                driver=builder.driver,
                loads=tuple(builder.loads),
            )
            for name, builder in self._nets.items()
        }
        return netlist, dict(self.gates), dict(self.gate_cells)

    def _parse_line(self, tokens: list[str], line_number: int) -> None:
        if tokens[0] in {"INPUT", "OUTPUT"}:
            if len(tokens) != 2:
                raise self._error(
                    line_number,
                    f"{tokens[0]} expects one signal name",
                    "netlist_syntax_error",
                )
            self._declare_port(tokens[0], tokens[1], line_number)
            return

        if len(tokens) < 4:
            raise self._error(
                line_number,
                "gate syntax is: <instance> <cell> <input...> <output>",
                "netlist_syntax_error",
            )
        self._add_gate(
            tokens[0],
            tokens[1],
            tokens[2:-1],
            tokens[-1],
            line_number,
        )

    def _declare_port(self, kind: str, name: str, line_number: int) -> None:
        if kind == "INPUT":
            if name in self._input_names:
                raise self._error(
                    line_number,
                    f"duplicate INPUT declaration for {name!r}",
                )
            if name in self._output_names:
                raise self._error(
                    line_number,
                    f"signal {name!r} is both INPUT and OUTPUT",
                )
            net = self._net(name)
            if net.driver is not None:
                raise self._error(
                    line_number,
                    f"primary input {name!r} is driven by a gate",
                )
            net.net_type = NetType.INPUT
            self._input_names.add(name)
            return

        if name in self._output_names:
            raise self._error(
                line_number,
                f"duplicate OUTPUT declaration for {name!r}",
            )
        if name in self._input_names:
            raise self._error(
                line_number,
                f"signal {name!r} is both INPUT and OUTPUT",
            )
        self._net(name).net_type = NetType.OUTPUT
        self._output_names.add(name)

    def _add_gate(
        self,
        gate_name: str,
        cell_name: str,
        input_names: list[str],
        output_name: str,
        line_number: int,
    ) -> None:
        if gate_name in self.gates:
            raise self._error(
                line_number,
                f"duplicate gate instance {gate_name!r}",
            )

        try:
            cell = self.cell_library[cell_name]
        except KeyError as exc:
            raise self._error(
                line_number,
                f"undefined cell {cell_name!r}",
                "undefined_cell",
            ) from exc
        if len(input_names) != cell.num_inputs:
            raise self._error(
                line_number,
                f"cell {cell_name!r} expects {cell.num_inputs} inputs, "
                f"got {len(input_names)}",
                "invalid_input_count",
            )

        for name in input_names:
            self._net(name)
        output = self._net(output_name)
        if output_name in self._input_names:
            raise self._error(
                line_number,
                f"gate {gate_name!r} drives primary input {output_name!r}",
            )
        if output.driver is not None:
            raise self._error(
                line_number,
                f"net {output_name!r} has multiple drivers "
                f"({output.driver!r} and {gate_name!r})",
                "multiple_drivers",
            )

        gate = Gate(
            name=gate_name,
            cell_family=cell.family,
            inputs=tuple(input_names),
            output=output_name,
        )
        output.driver = gate_name
        for pin_index, input_name in enumerate(input_names):
            self._nets[input_name].loads.append((pin_index, gate_name))
        self.gates[gate_name] = gate
        self.gate_cells[gate_name] = cell

    def _net(self, name: str) -> _NetBuilder:
        if not name:
            raise NetlistError("signal names cannot be empty")
        if name not in self._nets:
            self._nets[name] = _NetBuilder()
        return self._nets[name]

    def _validate_drivers(self) -> None:
        for name, net in self._nets.items():
            if name in self._input_names:
                continue
            if net.driver is None:
                role = "primary output" if name in self._output_names else "signal"
                code = "undriven_output" if role == "primary output" else "undriven_signal"
                raise NetlistError(f"{role} {name!r} is undriven", code)

    def _error(
        self,
        line_number: int,
        message: str,
        code: str = "netlist_validation_error",
    ) -> NetlistError:
        return NetlistError(
            f"{self.netlist_path}:{line_number}: {message}",
            code,
            self.netlist_path,
            line_number,
        )
