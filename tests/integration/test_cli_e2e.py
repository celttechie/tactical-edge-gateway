"""Black-box system end-to-end test for Tactical Edge Gateway.

Tests the full compiled process execution as a standalone OS subprocess:
- Configuration injected purely via 12-factor environment variables (TEG_*).
- Gateway process bootstraps without CLI arguments.
- Synthetic kinematics engine emits live CoT XML over local UDP socket.
- Real network listener captures and parses the Cursor on Target XML datagrams.
- Graceful termination via SIGTERM ensures zero orphan processes or socket leaks.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

import pytest


def _find_free_udp_port() -> int:
    """Finds an unused local UDP port."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def test_cli_full_process_e2e_with_env_vars():
    """Verify standalone process bootstraps via TEG_* env vars and emits valid CoT XML."""
    udp_port = _find_free_udp_port()

    # 1. Start live UDP listener socket to capture C2 CoT stream
    listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    listener.bind(("127.0.0.1", udp_port))
    listener.settimeout(4.0)

    # 2. Configure gateway entirely via 12-factor container environment variables
    env = os.environ.copy()
    env["TEG_SYNTHETIC__UID"] = "alpha-uav-01"
    env["TEG_SYNTHETIC__CALLSIGN"] = "ALPHA-ONE"
    env["TEG_SYNTHETIC__ALTITUDE_MSL_M"] = "250.0"
    env["TEG_SYNTHETIC__SPEED_MPS"] = "18.0"
    env["TEG_SYNTHETIC__RATE_HZ"] = "10.0"
    env["TEG_UDP_EGRESS"] = f'[{{"host": "127.0.0.1", "port": {udp_port}}}]'

    # 3. Launch gateway as a real OS subprocess
    proc = subprocess.Popen(
        [sys.executable, "-m", "tactical_edge_gateway.main"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    received_events: list[ET.Element] = []

    try:
        # 4. Receive and parse CoT datagrams from the live gateway process
        start_time = time.time()
        while len(received_events) < 3 and (time.time() - start_time) < 6.0:
            try:
                data, _ = listener.recvfrom(4096)
                root = ET.fromstring(data.decode("utf-8"))
                if root.tag == "event":
                    received_events.append(root)
            except socket.timeout:
                break

        # 5. Assert live telemetry CoT correctness
        assert len(received_events) >= 1, "Failed to receive any CoT datagrams from gateway process"

        evt = received_events[0]
        assert evt.attrib["uid"] == "alpha-uav-01"
        assert evt.attrib["type"] == "a-f-A-M-F"  # Air domain MIL-STD-2525
        assert evt.attrib["version"] == "2.0"
        assert "time" in evt.attrib
        assert "stale" in evt.attrib

        point = evt.find("point")
        assert point is not None
        # Geoid undulation model calculates HAE as MSL - 30.0 (250.0 - 30.0 = 220.0)
        assert float(point.attrib["hae"]) == pytest.approx(220.0, abs=1.0)

        detail = evt.find("detail")
        assert detail is not None
        contact = detail.find("contact")
        assert contact is not None
        assert contact.attrib["callsign"] == "ALPHA-ONE"

        track = detail.find("track")
        assert track is not None
        assert float(track.attrib["speed"]) == pytest.approx(18.0, abs=0.5)

    finally:
        # 6. Graceful process shutdown via SIGTERM
        proc.send_signal(signal.SIGTERM)
        try:
            stdout, stderr = proc.communicate(timeout=3.0)
            assert proc.returncode == 0
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            pytest.fail(f"Gateway process failed to stop cleanly on SIGTERM. Stderr: {stderr}")
        finally:
            listener.close()
