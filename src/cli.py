"""Command-line argument definitions for the circuit analysis application."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CommandLineArguments:
    netlist: Path
    config: Path
    output_dir: Path | None
    debug: bool


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Parse and analyze a gate-level circuit."
    )
    parser.add_argument("netlist", type=Path, help="gate-level netlist file")
    parser.add_argument("config", type=Path, help="analysis configuration JSON")
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="experiment output directory (default: outputs/<circuit-name>)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="enable debug logging",
    )
    return parser


def parse_arguments(
    parser: argparse.ArgumentParser,
    argv: list[str] | None,
) -> CommandLineArguments:
    namespace = parser.parse_args(argv)
    return CommandLineArguments(
        netlist=namespace.netlist,
        config=namespace.config,
        output_dir=namespace.output_dir,
        debug=namespace.debug,
    )
