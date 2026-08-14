from __future__ import annotations

import json
import threading
from pathlib import Path
from urllib.request import urlopen

from scripts.circuit_visualizer import (
    ASSETS,
    build_argument_parser,
    create_server,
    load_topology,
)


SAMPLE_TOPOLOGY = {
    "schema_version": 2,
    "circuit_name": "sample",
    "counts": {
        "nodes": 3,
        "gates": 1,
        "nets": 2,
        "primary_inputs": 1,
        "primary_outputs": 1,
    },
    "nodes": [
        {"id": "input:A", "kind": "input", "name": "A", "level": 0},
        {
            "id": "gate:G1",
            "kind": "gate",
            "name": "G1",
            "gate_type": "INV",
            "level": 1,
        },
        {"id": "output:Z", "kind": "output", "name": "Z", "level": 2},
    ],
    "nets": [
        {
            "id": "net:A",
            "name": "A",
            "source": "input:A",
            "targets": [{"node": "gate:G1", "pin": 0}],
        },
        {
            "id": "net:Z",
            "name": "Z",
            "source": "gate:G1",
            "targets": [{"node": "output:Z", "pin": None}],
        },
    ],
    "units": {"capacitance": "fF", "time": "ns"},
    "states": {
        "original": {
            "summary": {
                "wns": -0.1,
                "tns": -0.1,
                "circuit_delay": 0.11,
                "area": 1.0,
                "power": 0.1,
                "leakage_power": 0.05,
                "dynamic_power": 0.05,
                "maximum_area": 2.0,
                "maximum_power": 0.2,
                "timing_compliant": False,
                "area_compliant": True,
                "power_compliant": True,
                "all_constraints_compliant": False,
            },
            "gates": {
                "G1": {
                    "cell": "INV_X1",
                    "size": "X1",
                    "load_capacitance": 1.0,
                    "delay_rise": 0.1,
                    "delay_fall": 0.11,
                }
            },
            "critical_paths": [
                {
                    "rank": 1,
                    "slack": -0.1,
                    "input": "A",
                    "output": "Z",
                    "gates": ["G1"],
                    "nets": ["A", "Z"],
                }
            ],
        },
        "optimized": {
            "summary": {
                "wns": 0.01,
                "tns": 0.0,
                "circuit_delay": 0.08,
                "area": 2.0,
                "power": 0.2,
                "leakage_power": 0.1,
                "dynamic_power": 0.1,
                "maximum_area": 2.0,
                "maximum_power": 0.2,
                "timing_compliant": True,
                "area_compliant": True,
                "power_compliant": True,
                "all_constraints_compliant": True,
            },
            "gates": {
                "G1": {
                    "cell": "INV_X2",
                    "size": "X2",
                    "load_capacitance": 1.0,
                    "delay_rise": 0.07,
                    "delay_fall": 0.08,
                }
            },
            "critical_paths": [],
        },
    },
}


def test_load_topology_from_circuit_output_directory(tmp_path: Path) -> None:
    (tmp_path / "circuit_topology.json").write_text(
        json.dumps(SAMPLE_TOPOLOGY),
        encoding="utf-8",
    )

    assert load_topology(tmp_path) == SAMPLE_TOPOLOGY


def test_viewer_serves_app_and_topology_api() -> None:
    server = create_server("127.0.0.1", 0, SAMPLE_TOPOLOGY)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        with urlopen(base_url, timeout=2) as response:  # noqa: S310 - local test server
            html = response.read().decode()
        with urlopen(  # noqa: S310 - local test server
            f"{base_url}/api/topology", timeout=2
        ) as response:
            topology = json.load(response)
        with urlopen(  # noqa: S310 - local test server
            f"{base_url}/gate-symbols/NAND3.svg", timeout=2
        ) as response:
            symbol_type = response.headers.get_content_type()
            symbol = response.read()
        with urlopen(  # noqa: S310 - local test server
            f"{base_url}/vendor/elk.bundled.js", timeout=2
        ) as response:
            elk_type = response.headers.get_content_type()
            elk_bundle = response.read()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert "Interactive circuit topology" in html
    assert 'data-view="schematic"' in html
    assert 'class="topbar"' not in html
    assert 'class="connectivity-row"' in html
    assert 'id="inspector-resizer"' in html
    assert topology == SAMPLE_TOPOLOGY
    assert symbol_type == "image/svg+xml"
    assert symbol.startswith(b"<svg")
    assert elk_type == "text/javascript"
    assert len(elk_bundle) > 1_000_000


def test_viewer_can_start_without_initial_topology() -> None:
    server = create_server("127.0.0.1", 0, None)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        with urlopen(  # noqa: S310 - local test server
            f"{base_url}/api/topology", timeout=2
        ) as response:
            topology = json.load(response)
        with urlopen(base_url, timeout=2) as response:  # noqa: S310
            html = response.read().decode()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert topology is None
    assert 'id="choose-directory"' in html
    assert 'id="directory-picker"' in html


def test_viewer_cli_does_not_require_an_initial_output_directory() -> None:
    arguments = build_argument_parser().parse_args([])

    assert arguments.output_directory is None


def test_every_supported_gate_family_has_a_schematic_symbol() -> None:
    families = {
        "AND2", "AND3", "BUF", "INV", "NAND2", "NAND3",
        "NOR2", "NOR3", "OR2", "OR3", "XNOR2", "XOR2",
    }

    assert {
        path.removeprefix("/gate-symbols/").removesuffix(".svg")
        for path in ASSETS
        if path.startswith("/gate-symbols/")
    } == families
