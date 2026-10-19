"""Tactical Edge Gateway - Common Scripting Utilities & Testbed Primitives.

Provides shared network discovery, terminal formatting, ATAK package generation,
and mock TAK server capabilities across demonstration scripts and integration tests.
"""

from __future__ import annotations

import asyncio
import http.server
import shutil
import socket
import socketserver
import subprocess  # nosec B404
import zipfile
from collections.abc import Callable
from pathlib import Path

# ANSI Terminal Color & Style Formatting
C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_GREEN = "\033[32m"
C_RED = "\033[31m"
C_YELLOW = "\033[33m"
C_CYAN = "\033[36m"
C_MAGENTA = "\033[35m"

# Default Network & TAK Service Ports
DEFAULT_TAK_PORT = 8087
DEFAULT_TAK_TLS_PORT = 8089
DEFAULT_TAK_REST_PORT = 19023
DEFAULT_HTTP_SERVE_PORT = 8000

__all__ = [
    "C_BOLD",
    "C_CYAN",
    "C_GREEN",
    "C_MAGENTA",
    "C_RED",
    "C_RESET",
    "C_YELLOW",
    "DEFAULT_HTTP_SERVE_PORT",
    "DEFAULT_TAK_PORT",
    "DEFAULT_TAK_REST_PORT",
    "DEFAULT_TAK_TLS_PORT",
    "MockTakServer",
    "build_package",
    "detect_host_ip",
    "discover_network_interfaces",
    "find_free_port",
    "format_interface_label",
    "generate_manifest_xml",
    "generate_pref_xml",
    "get_repo_root",
    "interactive_select_host",
    "serve_package",
]


def get_repo_root() -> Path:
    """Return absolute path to repository root directory."""
    return Path(__file__).resolve().parents[1]


def detect_host_ip() -> str:
    """Detect primary routable host LAN IP via outbound socket check."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def find_free_port(is_udp: bool = False) -> int:
    """Find an available unused local port for testing or socket services."""
    sock_type = socket.SOCK_DGRAM if is_udp else socket.SOCK_STREAM
    with socket.socket(socket.AF_INET, sock_type) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def discover_network_interfaces() -> list[tuple[str, str]]:
    """Discover available local network interfaces and IPv4 addresses."""
    interfaces: list[tuple[str, str]] = []
    if shutil.which("ip"):
        try:
            res = subprocess.run(
                ["ip", "-br", "-4", "addr", "show"],  # nosec B603 B607
                capture_output=True,
                text=True,
                check=True,
            )
            for line in res.stdout.strip().splitlines():
                parts = line.split()
                if len(parts) >= 3 and parts[1] != "DOWN":
                    name = parts[0]
                    ip = parts[2].split("/")[0]
                    interfaces.append((name, ip))
        except Exception:  # nosec B110
            pass

    if not interfaces:
        primary = detect_host_ip()
        interfaces.append(("default", primary))
        if primary != "127.0.0.1":
            interfaces.append(("lo", "127.0.0.1"))

    return interfaces


def format_interface_label(iface_name: str) -> str:
    """Return friendly network domain descriptor for common interface names."""
    if iface_name.startswith(("wl", "wifi")):
        return " (Wi-Fi)"
    if iface_name.startswith(("en", "eth")):
        return " (Ethernet)"
    if "tailscale" in iface_name:
        return " (Tailscale VPN)"
    if iface_name == "lo":
        return " (Loopback)"
    if iface_name.startswith(("docker", "br-", "veth")):
        return " (Bridge/Container)"
    return ""


def interactive_select_host(
    interfaces: list[tuple[str, str]],
    prompt_fn: Callable[[str], str] = input,
) -> str:
    """Prompt operator to select from detected interfaces or enter custom target IP."""
    print("\nAvailable Network Interfaces:")
    for idx, (iface, ip) in enumerate(interfaces, 1):
        label = format_interface_label(iface)
        print(f"  [{idx}] {iface}{label:18s} : {ip}")

    custom_idx = len(interfaces) + 1
    print(f"  [{custom_idx}] Custom IP / Hostname")

    while True:
        try:
            raw_choice = prompt_fn(f"\nSelect network interface [1-{custom_idx}] (default 1): ")
            choice = raw_choice.strip()
            if not choice:
                return interfaces[0][1]

            num = int(choice)
            if 1 <= num <= len(interfaces):
                return interfaces[num - 1][1]
            if num == custom_idx:
                custom_val = prompt_fn("Enter target IP or hostname: ").strip()
                if custom_val:
                    return custom_val
                print("Invalid input. Please enter a valid IP or hostname.")
            else:
                print(f"Please enter a number between 1 and {custom_idx}.")
        except (ValueError, EOFError):
            print("Invalid selection. Please choose a valid index.")


def generate_pref_xml(
    host: str,
    port: int = DEFAULT_TAK_PORT,
    description: str = "Tactical Edge Gateway - FreeTAKServer",
) -> str:
    """Generate ATAK preferences XML."""
    return f"""<?xml version="1.0" encoding="utf-8" standalone="yes"?>
<preferences>
  <preference version="1" name="cot_streams">
    <entry key="count" class="java.lang.Integer">1</entry>
    <entry key="description0" class="java.lang.String">{description}</entry>
    <entry key="enabled0" class="java.lang.Boolean">true</entry>
    <entry key="connectString0" class="java.lang.String">{host}:{port}:tcp</entry>
  </preference>
  <preference version="1" name="com.atakmap.app_preferences">
    <entry key="displayServerConnectionWidget" class="java.lang.Boolean">true</entry>
  </preference>
</preferences>
"""


def generate_manifest_xml(
    pkg_name: str = "Tactical Edge Gateway FreeTAKServer Connection",
    uid: str = "teg-freetakserver-connection-pkg",
) -> str:
    """Generate ATAK Mission Package Manifest XML."""
    return f"""<MissionPackageManifest version="2">
  <Configuration>
    <Parameter name="name" value="{pkg_name}"/>
    <Parameter name="uid" value="{uid}"/>
    <Parameter name="onReceiveDelete" value="false"/>
  </Configuration>
  <Contents>
    <Content ignore="false" zipEntry="teg-freetakserver.pref"/>
  </Contents>
</MissionPackageManifest>
"""


def build_package(
    host: str,
    port: int = DEFAULT_TAK_PORT,
    output_dir: Path | None = None,
) -> tuple[Path, Path]:
    """Generate ATAK pref file and mission package zip archive."""
    if output_dir is None:
        atak_dir = get_repo_root() / "packages" / "atak"
    else:
        atak_dir = output_dir

    pkg_dir = atak_dir / "mission-package"

    atak_dir.mkdir(parents=True, exist_ok=True)
    pkg_dir.mkdir(parents=True, exist_ok=True)

    pref_file = atak_dir / "teg-freetakserver.pref"
    zip_file = pkg_dir / "teg-mission-package.zip"

    pref_xml = generate_pref_xml(host, port)
    manifest_xml = generate_manifest_xml()

    pref_file.write_text(pref_xml, encoding="utf-8")

    with zipfile.ZipFile(zip_file, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("MANIFEST/manifest.xml", manifest_xml)
        zf.writestr("teg-freetakserver.pref", pref_xml)

    return pref_file, zip_file


def serve_package(
    zip_file: Path,
    host: str,
    http_port: int = DEFAULT_HTTP_SERVE_PORT,
    server_cls: type[socketserver.TCPServer] = socketserver.TCPServer,
) -> None:
    """Serve the mission package via a lightweight HTTP server for mobile download."""
    directory = zip_file.parent

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(directory), **kwargs)

    print("\nServing ATAK Mission Package at:")
    print(f"  --> http://{host}:{http_port}/{zip_file.name}")
    print("\nOpen this URL in your mobile phone browser to download and tap to open in ATAK.")
    print("Press Ctrl+C to stop the server.\n")

    with server_cls(("0.0.0.0", http_port), Handler) as httpd:  # nosec B104
        httpd.serve_forever()


class MockTakServer:
    """Mock TAK server that can dynamically sever and restore listener sockets."""

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.server: asyncio.Server | None = None
        self.is_accepting = True
        self.received_messages: list[bytes] = []
        self._writers: list[asyncio.StreamWriter] = []

    async def start(self) -> None:
        """Starts listener socket accepting client connections."""
        self.is_accepting = True
        self.server = await asyncio.start_server(self._handle_client, self.host, self.port)

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Internal client stream reader loop."""
        self._writers.append(writer)
        while self.is_accepting:
            try:
                data = await reader.read(4096)
                if not data:
                    break
                self.received_messages.append(data.strip())
            except Exception:
                break
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:  # nosec B110
            pass

    async def sever_link(self) -> None:
        """Simulates radio loss by terminating active connections and closing socket."""
        self.is_accepting = False
        for w in self._writers:
            try:
                w.close()
            except Exception:  # nosec B110
                pass
        self._writers.clear()
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.server = None

    async def restore_link(self) -> None:
        """Restores radio link by reopening the listener socket."""
        await self.start()

    async def stop(self) -> None:
        """Stops mock TAK server and cleans up active connections."""
        await self.sever_link()
