#!/usr/bin/env python3
"""Tactical Edge Gateway - Interactive ATAK Mission Package Builder & Distribution CLI.

Generates an ATAK Data Package (.zip) and server preference file (.pref)
configured with the target host IP for single-tap import into ATAK-CIV or WinTAK.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Support running as a standalone script or module
try:
    from scripts.common import (
        DEFAULT_HTTP_SERVE_PORT,
        DEFAULT_TAK_PORT,
        build_package,
        detect_host_ip,
        discover_network_interfaces,
        format_interface_label,
        generate_manifest_xml,
        generate_pref_xml,
        interactive_select_host,
        serve_package,
    )
except ImportError:
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from scripts.common import (
        DEFAULT_HTTP_SERVE_PORT,
        DEFAULT_TAK_PORT,
        build_package,
        detect_host_ip,
        discover_network_interfaces,
        format_interface_label,
        generate_manifest_xml,
        generate_pref_xml,
        interactive_select_host,
        serve_package,
    )

__all__ = [
    "DEFAULT_HTTP_SERVE_PORT",
    "DEFAULT_TAK_PORT",
    "build_package",
    "detect_host_ip",
    "discover_network_interfaces",
    "format_interface_label",
    "generate_manifest_xml",
    "generate_pref_xml",
    "interactive_select_host",
    "main",
    "parse_args",
    "serve_package",
]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI options for package builder."""
    parser = argparse.ArgumentParser(
        description="Build and serve ATAK Mission Package for TEG / FreeTAKServer",
    )
    detected_ip = detect_host_ip()
    parser.add_argument(
        "--host",
        default=None,
        help=f"Target FreeTAKServer IP / hostname (detected LAN IP: {detected_ip})",
    )
    parser.add_argument(
        "-i",
        "--interactive",
        action="store_true",
        help="Interactively select network interface or enter custom target IP",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_TAK_PORT,
        help=f"FreeTAKServer CoT TCP streaming port (default: {DEFAULT_TAK_PORT})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory for generated preferences and mission package",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Start temporary HTTP server to download package directly on phone",
    )
    parser.add_argument(
        "--http-port",
        type=int,
        default=DEFAULT_HTTP_SERVE_PORT,
        help=f"Port for the HTTP server when --serve is used (default: {DEFAULT_HTTP_SERVE_PORT})",
    )

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """CLI Entrypoint for building and serving ATAK Mission Package."""
    args = parse_args(argv)

    if args.host:
        target_host = args.host
    elif args.interactive:
        interfaces = discover_network_interfaces()
        target_host = interactive_select_host(interfaces)
    else:
        target_host = detect_host_ip()

    pref_file, zip_file = build_package(target_host, args.port, args.output_dir)
    print(f"[+] Generated ATAK Server Prefs : {pref_file} ({target_host}:{args.port}:tcp)")
    print(f"[+] Generated Mission Package   : {zip_file}")

    if args.serve:
        try:
            serve_package(zip_file, target_host, args.http_port)
        except KeyboardInterrupt:
            print("\nHTTP server stopped.")
            sys.exit(0)


if __name__ == "__main__":
    main()
