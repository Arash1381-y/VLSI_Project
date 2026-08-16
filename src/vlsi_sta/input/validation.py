"""Structured validation reporting for command-line circuit loading."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from vlsi_sta.domain.cell import CellLibraryError
from vlsi_sta.input.config import ConfigError
from vlsi_sta.input.netlist import NetlistError


@dataclass(frozen=True)
class ValidationFailure:
    code: str
    message: str
    file: str | None = None
    line: int | None = None


def failure_from_exception(
    error: ConfigError | CellLibraryError | NetlistError,
    default_file: Path,
) -> ValidationFailure:
    """Convert an input exception to a stable machine-readable failure."""

    if isinstance(error, NetlistError):
        return ValidationFailure(
            code=error.code,
            message=str(error),
            file=str(error.path or default_file),
            line=error.line,
        )
    code = (
        "configuration_error"
        if isinstance(error, ConfigError)
        else "cell_library_error"
    )
    line_match = re.search(r"line (\d+)", str(error))
    return ValidationFailure(
        code=code,
        message=str(error),
        file=str(default_file),
        line=int(line_match.group(1)) if line_match else None,
    )


def write_validation_report(
    output_directory: Path,
    *,
    circuit_name: str | None,
    config_path: Path,
    netlist_path: Path,
    stages: dict[str, str],
    valid: bool,
    errors: tuple[ValidationFailure, ...] = (),
    counts: dict[str, int] | None = None,
) -> Path:
    """Write the validation artifact even when circuit construction fails."""

    output_directory.mkdir(parents=True, exist_ok=True)
    report = {
        "circuit_name": circuit_name,
        "inputs": {
            "config": str(config_path.resolve()),
            "netlist": str(netlist_path.resolve()),
        },
        "stages": stages,
        "valid": valid,
        "errors": [
            {
                "code": error.code,
                "message": error.message,
                "file": error.file,
                "line": error.line,
            }
            for error in errors
        ],
        "counts": counts or {},
    }
    output_path = output_directory / "validation_report.json"
    with output_path.open("w", encoding="utf-8") as output_file:
        json.dump(report, output_file, indent=2, sort_keys=True)
        output_file.write("\n")
    return output_path

