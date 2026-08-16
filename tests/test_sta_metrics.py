from __future__ import annotations

from pathlib import Path

import pytest

from vlsi_sta.domain.cell import CellLibrary
from vlsi_sta.domain.circuit import Circuit
from vlsi_sta.input.config import Config
from vlsi_sta.input.netlist import NetListParser
from vlsi_sta.analysis.sta import analyze_timing, analyze_timing_metrics


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
    directory = ROOT / "examples" / "circuits" / "valid" / case_name
    config = Config(directory / "config.json")
    library = CellLibrary(config.cell_library_path)
    nets, gates, cells = NetListParser(directory / "netlist.txt", library).parse()
    circuit = Circuit(nets, gates, cells, config, library)

    full = analyze_timing(circuit)
    compact = analyze_timing_metrics(circuit)

    assert compact.wns == pytest.approx(full.wns)
    assert compact.tns == pytest.approx(full.tns)
    assert compact.circuit_delay == pytest.approx(full.circuit_delay)
