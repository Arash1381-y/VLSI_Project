"""Unified command-line entry point for all project workflows."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from vlsi_sta.app.application import run_application
from vlsi_sta.benchmarking.cli import main as run_benchmark
from vlsi_sta.reporting.plot_optimization import main as plot_optimization
from vlsi_sta.viewer.server import main as run_viewer


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vlsi-sta",
        description="Analyze, optimize, benchmark, and visualize gate-level circuits.",
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=("analyze", "benchmark", "plot", "view"),
        help="workflow to run",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = build_argument_parser()
    if not arguments or arguments[0] in {"-h", "--help"}:
        parser.print_help()
        return

    command = arguments.pop(0)
    if command == "analyze":
        run_application(arguments)
    elif command == "benchmark":
        run_benchmark(arguments)
    elif command == "view":
        run_viewer(arguments)
    elif command == "plot":
        _run_plot(parser, arguments)
    else:
        parser.error(f"unknown command: {command}")


def _run_plot(parser: argparse.ArgumentParser, arguments: list[str]) -> None:
    if not arguments:
        parser.error("plot requires the 'optimization' subcommand")
    plot_kind = arguments.pop(0)
    if plot_kind != "optimization":
        parser.error(f"unknown plot type: {plot_kind}")
    plot_optimization(arguments)


if __name__ == "__main__":
    main()
