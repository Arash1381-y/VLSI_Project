"""Command-line interface for benchmark generation and evaluation."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence

from .config import BenchmarkConfig, BenchmarkConfigError
from .evaluation import BenchmarkEvaluationError, evaluate_suite
from .generation import BenchmarkGenerationError, generate_suite


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python3 -m src.benchmarking",
        description="Generate and evaluate deterministic STA sizing benchmarks.",
    )
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
        help="progress logging level (default: INFO)",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("generate", help="generate a benchmark suite")
    generate.add_argument("configuration", help="benchmark JSON configuration")
    evaluate = commands.add_parser("evaluate", help="evaluate a generated suite")
    evaluate.add_argument("suite_directory", help="generated suite directory")
    arguments = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, arguments.log_level),
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    # Benchmark searches run STA and circuit analysis thousands of times. At
    # INFO, show only concise benchmark progress; DEBUG restores subsystem logs.
    if arguments.log_level == "INFO":
        logging.getLogger("src").setLevel(logging.WARNING)
        logging.getLogger("src.benchmarking").setLevel(logging.INFO)
    try:
        if arguments.command == "generate":
            result = generate_suite(BenchmarkConfig.load(arguments.configuration))
            print(result.suite_directory)
        else:
            result = evaluate_suite(arguments.suite_directory)
            print(result.evaluation_directory)
    except (
        BenchmarkConfigError,
        BenchmarkGenerationError,
        BenchmarkEvaluationError,
    ) as exc:
        parser.exit(2, f"error: {exc}\n")


if __name__ == "__main__":
    main(sys.argv[1:])
