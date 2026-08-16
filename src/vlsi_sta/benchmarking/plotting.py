"""Create suite-level plots from benchmark evaluation CSV reports."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.figure import Figure, FigureBase
from matplotlib.ticker import PercentFormatter


HEURISTICS = (
    "brute_force",
    "slack_weighted_capacitance",
    "criticality_effort_gap",
    "random_greedy",
)
DISPLAY_NAMES = {
    "brute_force": "Brute force",
    "slack_weighted_capacitance": "Slack-weighted",
    "criticality_effort_gap": "Criticality/effort gap",
    "random_greedy": "Random greedy",
}
COLORS = {
    "brute_force": "#6C5CE7",
    "slack_weighted_capacitance": "#0984E3",
    "criticality_effort_gap": "#00A884",
    "random_greedy": "#E17055",
}


class BenchmarkPlotError(RuntimeError):
    """Raised when benchmark reports are missing or malformed."""


def _rows(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(encoding="utf-8", newline="") as source:
            rows = list(csv.DictReader(source))
    except (OSError, csv.Error) as exc:
        raise BenchmarkPlotError(f"cannot read {path}: {exc}") from exc
    if not rows:
        raise BenchmarkPlotError(f"report contains no rows: {path}")
    return rows


def _number(row: Mapping[str, str], field: str) -> float:
    try:
        return float(row[field])
    except (KeyError, TypeError, ValueError) as exc:
        raise BenchmarkPlotError(f"invalid numerical field {field!r}") from exc


def _integer_or_none(row: Mapping[str, str], field: str) -> int | None:
    value = row.get(field, "")
    if value == "":
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise BenchmarkPlotError(f"invalid integer field {field!r}") from exc


def _boolean(row: Mapping[str, str], field: str) -> bool:
    value = row.get(field)
    if value not in {"True", "False"}:
        raise BenchmarkPlotError(f"invalid boolean field {field!r}")
    return value == "True"


def _by_heuristic(
    rows: Sequence[dict[str, str]],
) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row["heuristic"]].append(row)
    missing = set(HEURISTICS) - set(grouped)
    if missing:
        raise BenchmarkPlotError(
            f"evaluation is missing heuristics: {', '.join(sorted(missing))}"
        )
    return grouped


def _case_groups(
    rows: Sequence[dict[str, str]],
) -> dict[tuple[str, str], list[dict[str, str]]]:
    grouped: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[(row["case_id"], row["heuristic"])].append(row)
    return grouped


def _case_values(
    case_groups: Mapping[tuple[str, str], Sequence[dict[str, str]]],
    heuristic: str,
    field: str,
    *,
    successful_only: bool = False,
) -> list[float]:
    values: list[float] = []
    for (case_id, item_heuristic), case_rows in case_groups.items():
        del case_id
        if item_heuristic != heuristic:
            continue
        selected = [
            row for row in case_rows
            if not successful_only or _boolean(row, "repair_success")
        ]
        if selected:
            values.append(float(np.median([_number(row, field) for row in selected])))
    return values


def _repair_rates_by_case(
    case_groups: Mapping[tuple[str, str], Sequence[dict[str, str]]],
    heuristic: str,
) -> dict[str, float]:
    return {
        case_id: sum(_boolean(row, "repair_success") for row in rows) / len(rows)
        for (case_id, item_heuristic), rows in case_groups.items()
        if item_heuristic == heuristic
    }


def _bootstrap_mean_interval(
    values: Sequence[float], seed: int = 1405, samples: int = 5000
) -> tuple[float, float]:
    data = np.asarray(values, dtype=float)
    rng = np.random.default_rng(seed)
    means = np.mean(
        rng.choice(data, size=(samples, len(data)), replace=True), axis=1
    )
    low, high = np.quantile(means, (0.025, 0.975))
    return float(low), float(high)


def _style_axis(axis: Any) -> None:
    axis.grid(True, axis="y", alpha=0.22, linewidth=0.8)
    axis.spines[["top", "right"]].set_visible(False)


def _labels(axis: Any, bars: Any, formatter: Any) -> None:
    axis.bar_label(bars, labels=[formatter(value) for value in bars.datavalues],
                   padding=4, fontsize=9)


def plot_overview(
    case_groups: Mapping[tuple[str, str], Sequence[dict[str, str]]],
    output: Path,
    dpi: int,
) -> None:
    """Plot repair rate, runtime, STA calls, and successful final cost."""

    figure, axes = plt.subplots(2, 2, figsize=(15, 10), constrained_layout=True)
    axes = cast(Any, axes)
    x = np.arange(len(HEURISTICS))
    colors = [COLORS[item] for item in HEURISTICS]
    labels = [DISPLAY_NAMES[item] for item in HEURISTICS]

    repair_values: list[float] = []
    lower_errors: list[float] = []
    upper_errors: list[float] = []
    for heuristic in HEURISTICS:
        rates = list(_repair_rates_by_case(case_groups, heuristic).values())
        mean = float(np.mean(rates))
        low, high = _bootstrap_mean_interval(rates)
        repair_values.append(mean)
        lower_errors.append(mean - low)
        upper_errors.append(high - mean)
    bars = axes[0, 0].bar(x, repair_values, color=colors)
    axes[0, 0].errorbar(
        x, repair_values, yerr=(lower_errors, upper_errors), fmt="none",
        color="#20252B", capsize=5, linewidth=1.4,
    )
    axes[0, 0].set_ylim(max(0.0, min(repair_values) - 0.08), 1.025)
    axes[0, 0].yaxis.set_major_formatter(PercentFormatter(1))
    axes[0, 0].set_title("Constraint repair rate (case-bootstrap 95% CI)")
    _labels(axes[0, 0], bars, lambda value: f"{value:.1%}")

    runtime = [
        _case_values(case_groups, heuristic, "runtime_seconds")
        for heuristic in HEURISTICS
    ]
    boxes = axes[0, 1].boxplot(runtime, patch_artist=True, labels=labels,
                               showfliers=False)
    for patch, color in zip(boxes["boxes"], colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.82)
    axes[0, 1].set_yscale("log")
    axes[0, 1].set_ylabel("Runtime per run (seconds, log scale)")
    axes[0, 1].set_title("Runtime distribution across cases")

    sta_calls = [
        float(np.mean(_case_values(case_groups, heuristic, "sta_calls")))
        for heuristic in HEURISTICS
    ]
    bars = axes[1, 0].bar(x, sta_calls, color=colors)
    axes[1, 0].set_yscale("log")
    axes[1, 0].set_ylabel("Mean STA calls per run (log scale)")
    axes[1, 0].set_title("Analyzer work required by each heuristic")
    _labels(axes[1, 0], bars, lambda value: f"{value:.1f}")

    final_cost = [
        _case_values(
            case_groups, heuristic, "final_cost", successful_only=True
        )
        for heuristic in HEURISTICS
    ]
    boxes = axes[1, 1].boxplot(final_cost, patch_artist=True, labels=labels,
                               showfliers=False)
    for patch, color in zip(boxes["boxes"], colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.82)
    axes[1, 1].set_ylabel("Final normalized cost")
    axes[1, 1].set_title("Final cost among repaired cases")

    for axis in axes.flat:
        _style_axis(axis)
        axis.set_xticks(x, labels, rotation=14, ha="right")
    figure.suptitle("Benchmark heuristic overview", fontsize=18, fontweight="bold")
    _save(figure, output, dpi)


def plot_repair_convergence(
    grouped: Mapping[str, Sequence[dict[str, str]]], output: Path, dpi: int
) -> None:
    """Plot the fraction of all runs repaired by each attempted iteration."""

    figure, axis = plt.subplots(figsize=(12, 7), constrained_layout=True)
    axis = cast(Any, axis)
    maximum_iteration = max(
        _integer_or_none(row, "total_iterations") or 0
        for rows in grouped.values() for row in rows
    )
    x = np.arange(maximum_iteration + 1)
    for heuristic in HEURISTICS:
        rows = grouped[heuristic]
        repair_iterations = [
            _integer_or_none(row, "repair_iteration") for row in rows
        ]
        y = [
            sum(value is not None and value <= iteration for value in repair_iterations)
            / len(repair_iterations)
            for iteration in x
        ]
        axis.step(
            x, y, where="post", label=DISPLAY_NAMES[heuristic],
            color=COLORS[heuristic], linewidth=2.4,
        )
    axis.set_xlabel("Total attempted optimizer iteration")
    axis.set_ylabel("Fraction of all runs with timing repaired")
    axis.set_title("Timing-repair convergence (failures remain unrepaired)")
    axis.yaxis.set_major_formatter(PercentFormatter(1))
    axis.set_ylim(0.0, 1.02)
    axis.set_xlim(0, maximum_iteration)
    axis.legend(frameon=False, loc="lower right")
    _style_axis(axis)
    _save(figure, output, dpi)


def plot_case_reliability(
    case_groups: Mapping[tuple[str, str], Sequence[dict[str, str]]],
    generation_rows: Sequence[dict[str, str]],
    output: Path,
    dpi: int,
) -> None:
    """Show per-case reliability and its relation to initial difficulty."""

    metadata = {row["case_id"]: row for row in generation_rows}
    ordered_cases = sorted(
        metadata,
        key=lambda case_id: (
            metadata[case_id]["source_type"],
            _number(metadata[case_id], "initial_wns_ns"),
            case_id,
        ),
    )
    rates = {
        heuristic: _repair_rates_by_case(case_groups, heuristic)
        for heuristic in HEURISTICS
    }
    matrix = np.asarray([
        [rates[heuristic][case_id] for case_id in ordered_cases]
        for heuristic in HEURISTICS
    ])
    figure, axes = plt.subplots(
        2, 1, figsize=(18, 10), constrained_layout=True,
        gridspec_kw={"height_ratios": (1.0, 1.35)},
    )
    axes = cast(Any, axes)
    image = axes[0].imshow(
        matrix, aspect="auto", interpolation="nearest", vmin=0.0, vmax=1.0,
        cmap="RdYlGn",
    )
    axes[0].set_yticks(np.arange(len(HEURISTICS)),
                       [DISPLAY_NAMES[item] for item in HEURISTICS])
    tick_positions = np.arange(0, len(ordered_cases), 10)
    axes[0].set_xticks(tick_positions,
                       [ordered_cases[index] for index in tick_positions],
                       rotation=35, ha="right")
    axes[0].set_title("Per-case repair probability (cases ordered by source and initial WNS)")
    colorbar = figure.colorbar(image, ax=axes[0], pad=0.012, fraction=0.025)
    colorbar.set_label("Repair probability")
    colorbar.ax.yaxis.set_major_formatter(PercentFormatter(1))

    random_rates = rates["random_greedy"]
    for source, marker in (("generated", "o"), ("seeded", "s")):
        selected = [row for row in generation_rows if row["source_type"] == source]
        axes[1].scatter(
            [-_number(row, "initial_wns_ns") for row in selected],
            [random_rates[row["case_id"]] for row in selected],
            s=[24.0 + 0.6 * _number(row, "gate_count") for row in selected],
            marker=marker, alpha=0.66, edgecolor="white", linewidth=0.5,
            label=source.title(), color=COLORS["random_greedy"],
        )
    axes[1].set_xlabel("Initial timing violation, −WNS (ns)")
    axes[1].set_ylabel("Random-greedy repair probability")
    axes[1].yaxis.set_major_formatter(PercentFormatter(1))
    axes[1].set_ylim(-0.03, 1.03)
    axes[1].set_title("Random-greedy reliability versus violation severity (marker size = gates)")
    axes[1].legend(frameon=False)
    _style_axis(axes[1])
    _save(figure, output, dpi)


def plot_scalability(
    case_groups: Mapping[tuple[str, str], Sequence[dict[str, str]]],
    generation_rows: Sequence[dict[str, str]],
    output: Path,
    dpi: int,
) -> None:
    """Plot per-case runtime against gate count with log-linear fits."""

    gate_counts = {row["case_id"]: _number(row, "gate_count")
                   for row in generation_rows}
    figure, axis = plt.subplots(figsize=(12, 7.5), constrained_layout=True)
    axis = cast(Any, axis)
    for heuristic in HEURISTICS:
        points = [
            (gate_counts[case_id], float(np.median([
                _number(row, "runtime_seconds") for row in rows
            ])))
            for (case_id, item_heuristic), rows in case_groups.items()
            if item_heuristic == heuristic
        ]
        x = np.asarray([item[0] for item in points])
        y = np.asarray([item[1] for item in points])
        axis.scatter(x, y, s=24, alpha=0.3, color=COLORS[heuristic])
        coefficient, intercept = np.polyfit(x, np.log(y), 1)
        fit_x = np.linspace(float(np.min(x)), float(np.max(x)), 100)
        axis.plot(
            fit_x, np.exp(intercept + coefficient * fit_x), linewidth=2.3,
            color=COLORS[heuristic], label=DISPLAY_NAMES[heuristic],
        )
    axis.set_yscale("log")
    axis.set_xlabel("Gate count")
    axis.set_ylabel("Median optimizer runtime per case (seconds, log scale)")
    axis.set_title("Runtime scaling with circuit size")
    axis.legend(frameon=False, ncol=2)
    _style_axis(axis)
    _save(figure, output, dpi)


def plot_selection_quality(
    summary: Mapping[str, Mapping[str, object]], output: Path, dpi: int
) -> None:
    """Plot decision-level oracle agreement and planted-mutation recovery."""

    figure, axes = plt.subplots(1, 2, figsize=(15, 6), constrained_layout=True)
    axes = cast(Any, axes)
    x = np.arange(len(HEURISTICS))
    width = 0.19
    rate_metrics = (
        ("gate_hit_rate", "Oracle-best gate"),
        ("exact_move_hit_rate", "Oracle-best exact move"),
        ("planted_gate_precision", "Planted precision"),
        ("planted_gate_recall", "Planted recall"),
    )
    rate_colors = ("#2D3436", "#636E72", "#74B9FF", "#00B894")
    for index, ((field, label), color) in enumerate(zip(rate_metrics, rate_colors)):
        values = [float(cast(Any, summary[item][field])) for item in HEURISTICS]
        axes[0].bar(x + (index - 1.5) * width, values, width, label=label,
                    color=color)
    axes[0].set_xticks(x, [DISPLAY_NAMES[item] for item in HEURISTICS],
                       rotation=14, ha="right")
    axes[0].yaxis.set_major_formatter(PercentFormatter(1))
    axes[0].set_title("Gate-selection quality")
    axes[0].legend(frameon=False, fontsize=9)
    _style_axis(axes[0])

    regrets = [
        float(cast(Any, summary[item]["median_wns_regret_ns"]))
        for item in HEURISTICS
    ]
    bars = axes[1].bar(x, regrets, color=[COLORS[item] for item in HEURISTICS])
    axes[1].set_xticks(x, [DISPLAY_NAMES[item] for item in HEURISTICS],
                       rotation=14, ha="right")
    axes[1].set_ylabel("Median WNS regret (ns; lower is better)")
    axes[1].set_title("Timing regret against exhaustive one-move oracle")
    _labels(axes[1], bars, lambda value: f"{value:.4f}")
    _style_axis(axes[1])
    figure.suptitle("Optimizer decision diagnostics", fontsize=18, fontweight="bold")
    _save(figure, output, dpi)


def _save(figure: FigureBase, path: Path, dpi: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cast(Figure, figure).savefig(path, dpi=dpi, bbox_inches="tight",
                                facecolor="white")
    plt.close(figure)


def plot_evaluation(evaluation_directory: Path, output_directory: Path, dpi: int) -> tuple[Path, ...]:
    """Generate the complete plot set for one evaluation directory."""

    evaluation_directory = evaluation_directory.resolve()
    suite_directory = evaluation_directory.parents[1]
    run_rows = _rows(evaluation_directory / "case_runs.csv")
    generation_rows = _rows(suite_directory / "generation_cases.csv")
    summary_document = json.loads(
        (evaluation_directory / "evaluation_summary.json").read_text(encoding="utf-8")
    )
    summary = summary_document.get("heuristics")
    if not isinstance(summary, dict):
        raise BenchmarkPlotError("evaluation summary has no heuristic mapping")
    typed_summary = cast(dict[str, dict[str, object]], summary)
    grouped = _by_heuristic(run_rows)
    cases = _case_groups(run_rows)
    paths = (
        output_directory / "heuristic_overview.png",
        output_directory / "repair_convergence.png",
        output_directory / "case_reliability.png",
        output_directory / "runtime_scalability.png",
        output_directory / "selection_quality.png",
    )
    plot_overview(cases, paths[0], dpi)
    plot_repair_convergence(grouped, paths[1], dpi)
    plot_case_reliability(cases, generation_rows, paths[2], dpi)
    plot_scalability(cases, generation_rows, paths[3], dpi)
    plot_selection_quality(typed_summary, paths[4], dpi)
    return paths


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vlsi-sta benchmark plot",
        description="Plot a complete STA benchmark evaluation run."
    )
    parser.add_argument("evaluation_directory", type=Path)
    parser.add_argument(
        "--output-dir", type=Path,
        help="destination directory (default: <evaluation>/plots)",
    )
    parser.add_argument("--dpi", type=int, default=180)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    arguments = build_argument_parser().parse_args(argv)
    evaluation = arguments.evaluation_directory
    output = arguments.output_dir or evaluation / "plots"
    if arguments.dpi <= 0:
        raise SystemExit("--dpi must be positive")
    try:
        paths = plot_evaluation(evaluation, output, arguments.dpi)
    except (BenchmarkPlotError, OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"error: {exc}") from exc
    for path in paths:
        print(path)


if __name__ == "__main__":
    main()
