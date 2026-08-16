from __future__ import annotations

from pathlib import Path

import pytest

from src.cell import CellLibrary
from src.circuit import Circuit
from src.config import Config
from src.netlist import NetListParser
from src.sta import analyze_timing, analyze_timing_metrics


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "case_name",
    (
        "c01_inverter_chain",
        "c03_fanout_branch",
        "c13_large_reconvergent_network",
        "c15_high_fanout_sizing_fabric",
    ),
)
def test_compact_timing_metrics_match_full_sta(case_name: str) -> None:
    directory = ROOT / "Input_Files" / "circuits" / "valid" / case_name
    config = Config(directory / "config.json")
    library = CellLibrary(config.cell_library_path)
    nets, gates, cells = NetListParser(directory / "netlist.txt", library).parse()
    circuit = Circuit(nets, gates, cells, config, library)

    full = analyze_timing(circuit)
    compact = analyze_timing_metrics(circuit)

    assert compact.wns == pytest.approx(full.wns)
    assert compact.tns == pytest.approx(full.tns)
    assert compact.circuit_delay == pytest.approx(full.circuit_delay)
