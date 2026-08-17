from __future__ import annotations

import runpy
import sys
from pathlib import Path

import pytest


PACKAGE_INIT = Path(__file__).resolve().parents[1] / "src" / "vlsi_sta" / "__init__.py"


def test_package_rejects_unsupported_python(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "version_info", (3, 9, 18))

    with pytest.raises(
        RuntimeError,
        match=r"requires Python 3\.10 or newer; found Python 3\.9\.18",
    ):
        runpy.run_path(str(PACKAGE_INIT))

