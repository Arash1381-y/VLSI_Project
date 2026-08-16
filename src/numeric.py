"""Shared floating-point comparison utilities."""

from __future__ import annotations

import math
from typing import Literal


RELATIVE_TOLERANCE = 1.0e-12


def compare_floats(
    number_one: float,
    number_two: float,
    tolerance: float = RELATIVE_TOLERANCE,
) -> Literal[-1, 0, 1]:
    """Compare two floats after applying a scale-aware tolerance.

    Return ``-1`` when ``number_one`` is smaller, ``1`` when it is larger,
    and ``0`` when their difference is within the requested tolerance.
    """

    if not math.isfinite(tolerance) or tolerance < 0.0:
        raise ValueError(
            "floating-point comparison tolerance must be finite and nonnegative"
        )
    if math.isnan(number_one) or math.isnan(number_two):
        raise ValueError("cannot compare NaN values")
    if number_one == number_two:
        return 0

    margin = tolerance * max(1.0, abs(number_one), abs(number_two))
    difference = number_one - number_two
    if abs(difference) <= margin:
        return 0
    return -1 if difference < 0.0 else 1
