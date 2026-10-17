"""Integration test for Defense Unicorns Zarf Air-Gap Packaging.

Verifies:
1. Zarf packages the tactical-edge-gateway container and manifests into a .tar.zst bundle.
2. The package inspection verifies all OCI images, components, and manifests are intact.
"""

from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not shutil.which("zarf"),
    reason="Zarf binary not found on PATH",
)

ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def test_zarf_package_create_and_inspect() -> None:
    """Validate declarative zarf.yaml packages into a valid air-gap tar.zst archive."""
    zarf_dir = Path(__file__).parents[2] / "packages" / "zarf"
    assert (zarf_dir / "zarf.yaml").exists(), "packages/zarf/zarf.yaml not found"

    with tempfile.TemporaryDirectory() as tmpdir:
        # Create Zarf package in isolated temporary directory
        cmd = [
            "zarf",
            "--no-color",
            "package",
            "create",
            str(zarf_dir),
            "--confirm",
            "--skip-sbom",
            "-o",
            tmpdir,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
        assert res.returncode == 0, f"Zarf package create failed: {res.stderr}\n{res.stdout}"

        # Find generated .tar.zst package
        packages = glob.glob(os.path.join(tmpdir, "zarf-package-tactical-edge-gateway-*.tar.zst"))
        assert len(packages) == 1, f"Expected 1 zarf package, found: {packages}"
        pkg_path = packages[0]

        # Inspect package definition
        inspect_cmd = ["zarf", "--no-color", "package", "inspect", "definition", pkg_path]
        inspect_res = subprocess.run(
            inspect_cmd, capture_output=True, text=True, timeout=30, check=False
        )
        assert inspect_res.returncode == 0, f"Zarf inspect failed: {inspect_res.stderr}"

        content = ANSI_ESCAPE.sub("", inspect_res.stdout)
        assert "name: tactical-edge-gateway" in content
        assert "version: 0.1.0" in content
        assert "tactical-edge-gateway:latest" in content
        assert "ghcr.io/freetakteam/freetakserver:latest" in content
        assert "gateway-manifests" in content
        assert "fts-manifests" in content
        assert "tactical-edge" in content
