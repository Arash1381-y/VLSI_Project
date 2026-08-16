from __future__ import annotations

import pytest

from src.numeric import compare_floats


def test_compare_floats_returns_order_outside_tolerance() -> None:
    assert compare_floats(2.0, 1.0) == 1
    assert compare_floats(1.0, 2.0) == -1


def test_compare_floats_treats_scaled_small_differences_as_equal() -> None:
    assert compare_floats(1.0 + 5.0e-13, 1.0) == 0
    assert compare_floats(1.0e9 + 5.0e-4, 1.0e9) == 0
    assert compare_floats(1.0 + 2.0e-12, 1.0) == 1


def test_compare_floats_accepts_an_explicit_tolerance() -> None:
    assert compare_floats(1.01, 1.0, 0.02) == 0
    assert compare_floats(1.03, 1.0, 0.02) == 1


@pytest.mark.parametrize("tolerance", (-1.0, float("inf"), float("nan")))
def test_compare_floats_rejects_invalid_tolerances(tolerance: float) -> None:
    with pytest.raises(ValueError):
        compare_floats(1.0, 1.0, tolerance)
