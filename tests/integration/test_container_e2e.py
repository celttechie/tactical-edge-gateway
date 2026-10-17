"""Integration and E2E validation for the hardened container image.

Verifies:
1. Rootless least-privilege execution (UID 10001).
2. Read-only root filesystem (--read-only) with temporary write support.
3. End-to-end telemetry pipeline (Synthetic -> CoT -> UDP egress) inside container.
4. Container healthcheck instruction.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import time
import uuid

import pytest


def _is_docker_and_image_available() -> bool:
    """Return True if docker is on PATH and tactical-edge-gateway:latest image exists."""
    if not shutil.which("docker"):
        return False
    try:
        res = subprocess.run(
            ["docker", "images", "-q", "tactical-edge-gateway:latest"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return bool(res.stdout.strip())
    except (subprocess.SubprocessError, OSError):
        return False


pytestmark = pytest.mark.skipif(
    not _is_docker_and_image_available(),
    reason="Docker daemon or 'tactical-edge-gateway:latest' image not available",
)


def _get_free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_container_version_read_only() -> None:
    """Validate that the container runs with --read-only rootfs as edgeuser."""
    cmd = [
        "docker",
        "run",
        "--rm",
        "--read-only",
        "--tmpfs",
        "/tmp",
        "tactical-edge-gateway:latest",
        "--version",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)
    assert res.returncode == 0
    assert "tactical-edge-gateway" in res.stdout
    assert "0.1.0" in res.stdout


def test_container_non_root_user_identity() -> None:
    """Validate UID/GID inside container matches DISA STIG rootless specification."""
    cmd = [
        "docker",
        "run",
        "--rm",
        "--entrypoint",
        "id",
        "tactical-edge-gateway:latest",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)
    assert res.returncode == 0
    assert "uid=10001(edgeuser)" in res.stdout
    assert "gid=10001(edgegroup)" in res.stdout


def test_container_e2e_synthetic_udp_stream() -> None:
    """Validate full end-to-end container operation emitting CoT over UDP to host."""
    port = _get_free_udp_port()
    container_name = f"teg-test-{uuid.uuid4().hex[:8]}"

    # Set up host UDP receiver
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver.settimeout(1.0)
    receiver.bind(("127.0.0.1", port))

    # Run container attached to host network for direct loopback delivery
    cmd = [
        "docker",
        "run",
        "--rm",
        "--name",
        container_name,
        "--network",
        "host",
        "--read-only",
        "--tmpfs",
        "/tmp",
        "-e",
        "TEG_SYNTHETIC__UID=synth-docker-01",
        "-e",
        "TEG_SYNTHETIC__CALLSIGN=DOCKER-VIPER",
        "-e",
        "TEG_SYNTHETIC__RATE_HZ=10.0",
        "-e",
        f'TEG_UDP_EGRESS=[{{"host": "127.0.0.1", "port": {port}}}]',
        "tactical-edge-gateway:latest",
    ]

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    received_packets: list[bytes] = []
    start_time = time.monotonic()
    try:
        while time.monotonic() - start_time < 5.0 and len(received_packets) < 2:
            try:
                data, _ = receiver.recvfrom(65535)
                received_packets.append(data)
            except socket.timeout:
                continue
    finally:
        # Gracefully stop the container
        subprocess.run(
            ["docker", "stop", "-t", "2", container_name], capture_output=True, check=False
        )
        proc.wait(timeout=5)
        receiver.close()

    assert len(received_packets) >= 1, "Container failed to emit CoT UDP packets to host"
    payload = received_packets[0].decode("utf-8")
    assert "<event" in payload
    assert 'version="2.0"' in payload
    assert "DOCKER-VIPER" in payload


def test_container_healthcheck_configuration() -> None:
    """Validate that the container specifies a valid HEALTHCHECK probe."""
    cmd = [
        "docker",
        "inspect",
        "--format={{json .Config.Healthcheck}}",
        "tactical-edge-gateway:latest",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=5, check=False)
    assert res.returncode == 0
    assert "tactical_edge_gateway" in res.stdout
    assert "healthy" in res.stdout
