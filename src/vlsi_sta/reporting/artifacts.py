"""Artifact persistence shared by all experiment report modules."""

from __future__ import annotations

import csv
import json
import logging
from collections.abc import Iterable, Sequence
from pathlib import Path


logger = logging.getLogger(__name__)


class ExperimentError(RuntimeError):
    """Raised when an experiment cannot be configured, run, or saved."""


class ArtifactWriter:
    """Write named experiment artifacts and maintain their manifest."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ExperimentError(
                f"cannot create output directory {self.directory}: {exc}"
            ) from exc

        self.artifacts: dict[str, str] = {
            filename: filename
            for filename in ("run.log", "validation_report.json")
            if (self.directory / filename).is_file()
        }

    def write_json(self, artifact_name: str, data: object) -> Path:
        """Serialize one artifact as formatted JSON and return its path."""

        output_path = self._artifact_path(artifact_name, ".json")
        try:
            with output_path.open("w", encoding="utf-8") as output_file:
                json.dump(data, output_file, indent=2, sort_keys=True)
                output_file.write("\n")
        except (OSError, TypeError, ValueError) as exc:
            raise ExperimentError(
                f"cannot write JSON artifact {output_path}: {exc}"
            ) from exc
        self._record(output_path)
        return output_path

    def write_csv(
        self,
        artifact_name: str,
        header: Sequence[str],
        rows: Iterable[Sequence[object]],
    ) -> Path:
        """Serialize tabular rows as CSV and return its path."""

        output_path = self._artifact_path(artifact_name, ".csv")
        try:
            with output_path.open("w", encoding="utf-8", newline="") as output_file:
                writer = csv.writer(output_file)
                writer.writerow(header)
                writer.writerows(rows)
        except (OSError, csv.Error, TypeError, ValueError) as exc:
            raise ExperimentError(
                f"cannot write CSV artifact {output_path}: {exc}"
            ) from exc
        self._record(output_path)
        return output_path

    def record_existing(self, path: Path) -> None:
        """Add a non-tabular artifact written by another subsystem."""

        self._record(path)

    def _record(self, path: Path) -> None:
        logger.debug("Saved experiment artifact to %s", path)
        self.artifacts[path.name] = path.name

    def _artifact_path(self, artifact_name: str, suffix: str) -> Path:
        if not artifact_name or Path(artifact_name).name != artifact_name:
            raise ExperimentError(f"invalid artifact name: {artifact_name!r}")
        filename = (
            artifact_name
            if artifact_name.endswith(suffix)
            else f"{artifact_name}{suffix}"
        )
        return self.directory / filename

