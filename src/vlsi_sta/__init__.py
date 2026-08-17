"""Static timing analysis, optimization, and benchmarking tools."""

import sys


MINIMUM_PYTHON = (3, 10)

if sys.version_info < MINIMUM_PYTHON:
    required = ".".join(str(part) for part in MINIMUM_PYTHON)
    current = ".".join(str(part) for part in sys.version_info[:3])
    raise RuntimeError(
        f"vlsi-sta requires Python {required} or newer; found Python {current}"
    )
