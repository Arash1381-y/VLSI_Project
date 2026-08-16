"""Frozen records shared by benchmark generation and evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SearchStatistics:
    starts: int
    expansions: int
    unique_states: int
    elapsed_seconds: float
    termination: str


@dataclass(frozen=True)
class MutationRecord:
    gate: str
    reference_cell: str
    initial_cell: str
    size_steps: int
    on_reference_critical_path: bool
    wns_delta_ns: float
    tns_delta_ns: float
    delay_delta_ns: float
    area_delta: float
    power_delta_uW: float


@dataclass(frozen=True)
class GeneratedCase:
    case_id: str
    source_type: str
    source_name: str
    directory: Path
    random_seed: int
    gate_count: int
    logic_depth: int
    input_count: int
    output_count: int
    reference_wns_ns: float
    initial_wns_ns: float
    reference_area: float
    reference_power_uW: float
    area_headroom: float
    power_headroom: float
    mutations: tuple[MutationRecord, ...]
    search: SearchStatistics


@dataclass(frozen=True)
class GenerationFailure:
    case_id: str
    source_type: str
    source_name: str
    attempts: int
    reason: str


@dataclass(frozen=True)
class GenerationResult:
    suite_directory: Path
    requested_cases: int
    generated_cases: tuple[GeneratedCase, ...]
    failures: tuple[GenerationFailure, ...]


@dataclass(frozen=True)
class EvaluationResult:
    suite_directory: Path
    evaluation_directory: Path
    case_run_count: int
    successful_repairs: int
