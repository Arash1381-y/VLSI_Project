"""Cell-library data model and JSON parser."""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import cast


logger = logging.getLogger(__name__)


class CellLibraryError(ValueError):
    """Raised when a cell-library file is malformed."""


class TimingSense(str, Enum):
    POSITIVE_UNATE = "positive_unate"
    NEGATIVE_UNATE = "negative_unate"
    NON_UNATE = "non_unate"


def _object(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise CellLibraryError(f"{context} must be an object")
    if not all(isinstance(key, str) for key in value):
        raise CellLibraryError(f"{context} must have string keys")
    return cast(dict[str, object], value)


def _list(value: object, context: str) -> list[object]:
    if not isinstance(value, list):
        raise CellLibraryError(f"{context} must be an array")
    return cast(list[object], value)


def _required(data: Mapping[str, object], key: str, context: str) -> object:
    try:
        return data[key]
    except KeyError as exc:
        raise CellLibraryError(f"{context} is missing {key!r}") from exc


def _string(value: object, context: str) -> str:
    if not isinstance(value, str):
        raise CellLibraryError(f"{context} must be a string")
    return value


def _number(value: object, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CellLibraryError(f"{context} must be a number")
    return float(value)


def _integer(value: object, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CellLibraryError(f"{context} must be an integer")
    return value


def _timing_sense(value: object, context: str) -> TimingSense:
    text = _string(value, context)
    try:
        return TimingSense(text)
    except ValueError as exc:
        raise CellLibraryError(f"{context} has unknown value {text!r}") from exc


@dataclass(frozen=True)
class InputPin:
    name: str
    capacitance: float
    logical_effort: float
    timing_sense: TimingSense


@dataclass(frozen=True)
class RiseFall:
    rise: float
    fall: float


@dataclass(frozen=True)
class Cell:
    """One characterized standard cell."""

    name: str
    family: str
    size: str
    size_factor: float
    num_inputs: int
    input_pins: tuple[InputPin, ...]
    timing_sense: TimingSense
    intrinsic_delay: RiseFall
    output_resistance: RiseFall
    internal_capacitance: float
    parasitic_delay: float
    leakage_power: float
    area: float

    @classmethod
    def from_dict(cls, name: str, data: Mapping[str, object]) -> Cell:
        """Build and validate a cell from its JSON object."""

        context = f"cell {name!r}"
        pins: list[InputPin] = []
        pin_values = _list(_required(data, "input_pins", context), f"{context}.input_pins")
        for index, pin_value in enumerate(pin_values):
            pin_context = f"{context}.input_pins[{index}]"
            pin = _object(pin_value, pin_context)
            pins.append(
                InputPin(
                    name=_string(_required(pin, "name", pin_context), f"{pin_context}.name"),
                    capacitance=_number(
                        _required(pin, "capacitance", pin_context),
                        f"{pin_context}.capacitance",
                    ),
                    logical_effort=_number(
                        _required(pin, "logical_effort", pin_context),
                        f"{pin_context}.logical_effort",
                    ),
                    timing_sense=_timing_sense(
                        _required(pin, "timing_sense", pin_context),
                        f"{pin_context}.timing_sense",
                    ),
                )
            )

        intrinsic = _object(
            _required(data, "intrinsic_delay", context),
            f"{context}.intrinsic_delay",
        )
        resistance = _object(
            _required(data, "output_resistance", context),
            f"{context}.output_resistance",
        )
        cell = cls(
            name=name,
            family=_string(_required(data, "family", context), f"{context}.family"),
            size=_string(_required(data, "size", context), f"{context}.size"),
            size_factor=_number(
                _required(data, "size_factor", context), f"{context}.size_factor"
            ),
            num_inputs=_integer(
                _required(data, "num_inputs", context), f"{context}.num_inputs"
            ),
            input_pins=tuple(pins),
            timing_sense=_timing_sense(
                _required(data, "timing_sense", context), f"{context}.timing_sense"
            ),
            intrinsic_delay=RiseFall(
                _number(_required(intrinsic, "rise", context), f"{context}.intrinsic_delay.rise"),
                _number(_required(intrinsic, "fall", context), f"{context}.intrinsic_delay.fall"),
            ),
            output_resistance=RiseFall(
                _number(_required(resistance, "rise", context), f"{context}.output_resistance.rise"),
                _number(_required(resistance, "fall", context), f"{context}.output_resistance.fall"),
            ),
            internal_capacitance=_number(
                _required(data, "internal_capacitance", context),
                f"{context}.internal_capacitance",
            ),
            parasitic_delay=_number(
                _required(data, "parasitic_delay", context), f"{context}.parasitic_delay"
            ),
            leakage_power=_number(
                _required(data, "leakage_power", context), f"{context}.leakage_power"
            ),
            area=_number(_required(data, "area", context), f"{context}.area"),
        )

        if cell.num_inputs != len(cell.input_pins):
            raise CellLibraryError(
                f"cell {name!r} declares {cell.num_inputs} inputs but defines "
                f"{len(cell.input_pins)} pins"
            )
        return cell


class CellLibrary(Mapping[str, Cell]):
    """A name-indexed collection of cells parsed from ``cell_library.json``."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        try:
            with self.path.open("r", encoding="utf-8") as library_file:
                raw_document: object = json.load(library_file)
        except (OSError, json.JSONDecodeError) as exc:
            raise CellLibraryError(f"cannot read cell library {self.path}: {exc}") from exc

        document = _object(raw_document, "cell library")
        cells_data = _object(
            _required(document, "cells", "cell library"), "cell library.cells"
        )

        metadata = document.get("metadata", {})
        self.metadata = _object(metadata, "cell library.metadata")
        units_value = self.metadata.get("units", {})
        units = _object(units_value, "cell library.metadata.units")
        self.units: Mapping[str, str] = {
            key: _string(value, f"cell library.metadata.units.{key}")
            for key, value in units.items()
        }
        self.delay_unit_conversion_kappa = _number(
            _required(
                self.metadata,
                "delay_unit_conversion_kappa",
                "cell library.metadata",
            ),
            "cell library.metadata.delay_unit_conversion_kappa",
        )
        if self.delay_unit_conversion_kappa <= 0.0:
            raise CellLibraryError(
                "cell library.metadata.delay_unit_conversion_kappa must be positive"
            )
        self.logical_effort_tau = _number(
            _required(
                self.metadata,
                "logical_effort_tau",
                "cell library.metadata",
            ),
            "cell library.metadata.logical_effort_tau",
        )
        if self.logical_effort_tau <= 0.0:
            raise CellLibraryError(
                "cell library.metadata.logical_effort_tau must be positive"
            )
        self.dynamic_power_to_uW_factor: float = _number(
            _required(
                self.metadata,
                "dynamic_power_to_uW_factor",
                "cell library.metadata",
            ),
            "cell library.metadata.dynamic_power_to_uW_factor",
        )
        if self.dynamic_power_to_uW_factor <= 0.0:
            raise CellLibraryError(
                "cell library.metadata.dynamic_power_to_uW_factor must be positive"
            )
        self.cells: dict[str, Cell] = {}
        for name, definition in cells_data.items():
            self.cells[name] = Cell.from_dict(
                name, _object(definition, f"cell {name!r}")
            )
        logger.debug("Loaded %d cells from %s", len(self.cells), self.path)

    def __getitem__(self, name: str) -> Cell:
        return self.cells[name]

    def __iter__(self) -> Iterator[str]:
        return iter(self.cells)

    def __len__(self) -> int:
        return len(self.cells)

    def find(self, name: str) -> Cell | None:
        """Return the named cell, or ``None`` when it is unavailable."""

        return self.cells.get(name)

    def variants(self, family: str) -> tuple[Cell, ...]:
        """Return all drive-strength variants of a logic family."""

        return tuple(cell for cell in self.cells.values() if cell.family == family)

    def sizing_candidates(
        self,
        family: str,
        input_pin: int,
        target_capacitance: float,
    ) -> tuple[Cell, ...]:
        """Return target brackets or the two nearest boundary variants."""

        variants = sorted(
            (
                cell
                for cell in self.variants(family)
                if input_pin < len(cell.input_pins)
            ),
            key=lambda cell: (
                cell.input_pins[input_pin].capacitance,
                cell.size_factor,
            ),
        )
        if not variants:
            raise CellLibraryError(
                f"cell family {family!r} has no input pin {input_pin}"
            )

        for index, cell in enumerate(variants):
            capacitance = cell.input_pins[input_pin].capacitance
            if math.isclose(capacitance, target_capacitance):
                return (cell,)
            if capacitance > target_capacitance:
                if index == 0:
                    return tuple(variants[:2])
                return (variants[index - 1], cell)
        return tuple(variants[-2:])
