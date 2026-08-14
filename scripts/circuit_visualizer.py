"""Serve the interactive topology viewer with optional initial circuit data."""

from __future__ import annotations

import argparse
import json
import logging
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


logger = logging.getLogger(__name__)

TOPOLOGY_FILENAME = "circuit_topology.json"
ASSET_DIRECTORY = Path(__file__).resolve().parent / "circuit_viewer"
ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/vendor/elk.bundled.js": (
        "vendor/elk.bundled.js",
        "text/javascript; charset=utf-8",
    ),
}
for symbol_path in (ASSET_DIRECTORY / "gate_symbols").glob("*.svg"):
    ASSETS[f"/gate-symbols/{symbol_path.name}"] = (
        f"gate_symbols/{symbol_path.name}",
        "image/svg+xml",
    )


class ViewerError(RuntimeError):
    """Raised when the viewer cannot load or serve topology data."""


def load_topology(output_directory: str | Path) -> dict[str, Any]:
    """Load and minimally validate a generated topology artifact."""

    path = Path(output_directory) / TOPOLOGY_FILENAME
    try:
        with path.open(encoding="utf-8") as topology_file:
            data: object = json.load(topology_file)
    except (OSError, json.JSONDecodeError) as error:
        raise ViewerError(f"cannot load topology artifact {path}: {error}") from error
    if not isinstance(data, dict):
        raise ViewerError(f"topology artifact {path} must contain a JSON object")
    if data.get("schema_version") != 2:
        raise ViewerError(f"topology artifact {path} has an unsupported schema")
    if not isinstance(data.get("nodes"), list) or not isinstance(
        data.get("nets"), list
    ):
        raise ViewerError(f"topology artifact {path} is missing nodes or nets")
    if not isinstance(data.get("states"), dict):
        raise ViewerError(f"topology artifact {path} is missing analysis states")
    return data


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Open an interactive circuit topology viewer."
    )
    parser.add_argument(
        "output_directory",
        nargs="?",
        type=Path,
        help=(
            f"optional circuit output directory containing {TOPOLOGY_FILENAME}; "
            "omit it to choose a directory in the browser"
        ),
    )
    parser.add_argument("--host", default="127.0.0.1", help="server host")
    parser.add_argument("--port", type=int, default=8765, help="server port")
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="do not open the viewer in the default browser",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    arguments = build_argument_parser().parse_args(argv)
    try:
        topology = (
            load_topology(arguments.output_directory)
            if arguments.output_directory is not None
            else None
        )
        server = create_server(arguments.host, arguments.port, topology)
    except (OSError, ViewerError) as error:
        raise SystemExit(f"error: {error}") from error

    browser_host = "127.0.0.1" if arguments.host == "0.0.0.0" else arguments.host
    url = f"http://{browser_host}:{server.server_port}"
    print(f"Circuit topology viewer: {url}")
    print("Press Ctrl+C to stop.")
    if not arguments.no_browser:
        threading.Timer(0.25, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nViewer stopped.")
    finally:
        server.server_close()


def create_server(
    host: str,
    port: int,
    topology: dict[str, Any] | None,
) -> ThreadingHTTPServer:
    """Create a local viewer server; port zero selects an available port."""

    class ViewerRequestHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            path = urlsplit(self.path).path
            if path == "/api/topology":
                self._send_json(topology)
                return
            asset = ASSETS.get(path)
            if asset is None:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            filename, content_type = asset
            self._send_asset(filename, content_type)

        def _send_json(self, payload: object) -> None:
            body = json.dumps(payload, separators=(",", ":")).encode()
            self._send(body, "application/json; charset=utf-8", "no-store")

        def _send_asset(self, filename: str, content_type: str) -> None:
            try:
                body = (ASSET_DIRECTORY / filename).read_bytes()
            except OSError:
                self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            self._send(body, content_type, "public, max-age=300")

        def _send(self, body: bytes, content_type: str, cache: str) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            logger.debug(format, *args)

    return ThreadingHTTPServer((host, port), ViewerRequestHandler)


if __name__ == "__main__":
    main()
