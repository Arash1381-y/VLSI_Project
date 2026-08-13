"""Post-process optimization CSV reports into comparison figures."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.figure import Figure, FigureBase
from matplotlib.ticker import MaxNLocator


HISTORY_PREFIX = "optimization_"
SUMMARY_FILENAME = "optimization_summary.csv"
METRICS = (
    ("cost", "Normalized cost"),
    ("wns_ns", "WNS (ns)"),
    ("tns_ns", "TNS (ns)"),
    ("circuit_delay_ns", "Circuit delay (ns)"),
    ("area", "Area"),
    ("power_uW", "Power (µW)"),
)


class PlotError(RuntimeError):
    """Raised when optimization CSVs cannot be loaded or plotted."""


@dataclass(frozen=True)
class OptimizationHistory:
    heuristic: str
    iterations: tuple[int, ...]
    values: dict[str, tuple[float, ...]]


@dataclass(frozen=True)
class OptimizationSummary:
    heuristic: str
    total_iterations: int
    accepted_iterations: int
    elapsed_seconds: float
    final_wns: float


def _display_name(name: str) -> str:
    return name.replace("_", " ").title()


def _read_rows(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(newline="", encoding="utf-8") as input_file:
            rows = list(csv.DictReader(input_file))
    except (OSError, csv.Error) as exc:
        raise PlotError(f"cannot read {path}: {exc}") from exc
    if not rows:
        raise PlotError(f"CSV contains no data rows: {path}")
    return rows


def _integer(row: dict[str, str], field: str, path: Path) -> int:
    try:
        return int(row[field])
    except (KeyError, TypeError, ValueError) as exc:
        raise PlotError(f"invalid {field!r} value in {path}") from exc


def _number(row: dict[str, str], field: str, path: Path) -> float:
    try:
        return float(row[field])
    except (KeyError, TypeError, ValueError) as exc:
        raise PlotError(f"invalid {field!r} value in {path}") from exc


def load_histories(directory: Path) -> tuple[OptimizationHistory, ...]:
    """Load every heuristic history in one circuit output directory."""

    histories: list[OptimizationHistory] = []
    for path in sorted(directory.glob(f"{HISTORY_PREFIX}*.csv")):
        if path.name == SUMMARY_FILENAME:
            continue
        heuristic = path.stem.removeprefix(HISTORY_PREFIX)
        rows = _read_rows(path)
        histories.append(
            OptimizationHistory(
                heuristic=heuristic,
                iterations=tuple(
                    _integer(row, "iteration", path) for row in rows
                ),
                values={
                    field: tuple(_number(row, field, path) for row in rows)
                    for field, _ in METRICS
                },
            )
        )
    if not histories:
        raise PlotError(f"no optimization history CSVs found in {directory}")
    return tuple(histories)


def load_summaries(directory: Path) -> tuple[OptimizationSummary, ...]:
    """Load the final outcome and runtime of each heuristic."""

    path = directory / SUMMARY_FILENAME
    rows = _read_rows(path)
    return tuple(
        OptimizationSummary(
            heuristic=row["method"],
            total_iterations=_integer(row, "total_iterations", path),
            accepted_iterations=_integer(row, "accepted_iterations", path),
            elapsed_seconds=_number(row, "elapsed_seconds", path),
            final_wns=_number(row, "wns_ns", path),
        )
        for row in rows
    )


def plot_convergence(
    circuit_name: str,
    histories: tuple[OptimizationHistory, ...],
    output_path: Path,
    dpi: int,
) -> None:
    """Plot six optimization metrics against accepted iteration number."""

    figure, axes_grid = plt.subplots(3, 2, figsize=(12, 12), constrained_layout=True)
    axes = tuple(axis for row in axes_grid for axis in row)
    colors = plt.get_cmap("tab10")

    for metric_index, ((field, label), axis) in enumerate(zip(METRICS, axes)):
        for heuristic_index, history in enumerate(histories):
            axis.plot(
                history.iterations,
                history.values[field],
                label=_display_name(history.heuristic),
                color=colors(heuristic_index),
                marker="o",
                markersize=4,
                linewidth=1.8,
            )
        if field in {"wns_ns", "tns_ns"}:
            axis.axhline(0.0, color="black", linewidth=1.0, linestyle="--")
        axis.set_title(label)
        axis.set_xlabel("Total iteration")
        axis.set_ylabel(label)
        axis.xaxis.set_major_locator(MaxNLocator(integer=True))
        axis.grid(True, alpha=0.3)
        if metric_index == 0:
            axis.legend()

    figure.suptitle(f"Optimization convergence — {circuit_name}", fontsize=16)
    _save_figure(figure, output_path, dpi)


def plot_outcomes(
    circuit_name: str,
    summaries: tuple[OptimizationSummary, ...],
    output_path: Path,
    dpi: int,
) -> None:
    """Compare runtime, total iterations, and final WNS."""

    labels = [_display_name(summary.heuristic) for summary in summaries]
    colors = [plt.get_cmap("tab10")(index) for index in range(len(summaries))]
    figure, axes = plt.subplots(1, 3, figsize=(15, 4.8), constrained_layout=True)
    data = (
        ([summary.elapsed_seconds for summary in summaries], "Runtime (s)"),
        (
            [summary.total_iterations for summary in summaries],
            "Total iterations",
        ),
        ([summary.final_wns for summary in summaries], "Final WNS (ns)"),
    )
    for axis, (values, title) in zip(axes, data):
        axis.bar(labels, values, color=colors)
        axis.set_title(title)
        axis.tick_params(axis="x", rotation=18)
        axis.grid(True, axis="y", alpha=0.3)
        if title == "Final WNS (ns)":
            axis.axhline(0.0, color="black", linewidth=1.0, linestyle="--")
    axes[1].yaxis.set_major_locator(MaxNLocator(integer=True))

    figure.suptitle(f"Optimization outcomes — {circuit_name}", fontsize=16)
    _save_figure(figure, output_path, dpi)


def _save_figure(figure: FigureBase, output_path: Path, dpi: int) -> None:
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cast(Figure, figure).savefig(
            output_path,
            dpi=dpi,
            bbox_inches="tight",
        )
    except OSError as exc:
        raise PlotError(f"cannot save {output_path}: {exc}") from exc
    finally:
        plt.close(figure)


def discover_circuit_directories(target: Path) -> tuple[Path, ...]:
    """Accept one circuit output directory or a root containing many."""

    if (target / SUMMARY_FILENAME).is_file():
        return (target,)
    directories = tuple(
        path
        for path in sorted(target.iterdir())
        if path.is_dir() and (path / SUMMARY_FILENAME).is_file()
    )
    if not directories:
        raise PlotError(f"no circuit optimization outputs found under {target}")
    return directories


def plot_directory(
    directory: Path,
    output_root: Path | None,
    image_format: str,
    dpi: int,
) -> tuple[Path, Path]:
    """Generate both comparison figures for one circuit directory."""

    destination = directory if output_root is None else output_root / directory.name
    convergence_path = destination / f"optimization_convergence.{image_format}"
    outcomes_path = destination / f"optimization_outcomes.{image_format}"
    plot_convergence(directory.name, load_histories(directory), convergence_path, dpi)
    plot_outcomes(directory.name, load_summaries(directory), outcomes_path, dpi)
    return convergence_path, outcomes_path


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Plot optimization heuristic comparisons from experiment CSVs."
    )
    parser.add_argument(
        "input",
        type=Path,
        help="one circuit output directory or a root containing circuit outputs",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="optional plot root; defaults to each circuit output directory",
    )
    parser.add_argument(
        "--format",
        choices=("png", "svg", "pdf"),
        default="png",
        help="output image format (default: png)",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=160,
        help="raster resolution (default: 160)",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_argument_parser()
    arguments = parser.parse_args(argv)
    if arguments.dpi <= 0:
        parser.error("--dpi must be positive")

    try:
        directories = discover_circuit_directories(arguments.input)
        for directory in directories:
            generated = plot_directory(
                directory,
                arguments.output_dir,
                arguments.format,
                arguments.dpi,
            )
            for path in generated:
                print(path)
    except (OSError, PlotError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
