"""Entry point for circuit analysis."""

from __future__ import annotations

from .application import run_application


def main(argv: list[str] | None = None) -> None:
    run_application(argv)


if __name__ == "__main__":
    main()
