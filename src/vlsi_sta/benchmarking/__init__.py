"""Configurable generation and evaluation of STA sizing benchmarks."""

from vlsi_sta.benchmarking.config import BenchmarkConfig, BenchmarkConfigError
from .evaluation import EvaluationResult, evaluate_suite
from .generation import GenerationResult, generate_suite

__all__ = [
    "BenchmarkConfig",
    "BenchmarkConfigError",
    "EvaluationResult",
    "GenerationResult",
    "evaluate_suite",
    "generate_suite",
]
