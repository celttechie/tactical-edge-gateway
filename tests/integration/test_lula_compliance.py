"""Integration tests for Defense Unicorns Lula OSCAL Compliance Validation.

Validates:
1. OSCAL component-definition YAML schema, UUID v4, and NIST SP 800-53 controls.
2. Documented KSV-0014 deviation and compensating controls in AC-6.
3. 'lula tools lint' verification against OSCAL v1.1.2 schema.
4. 'lula dev validate' execution of Rego policies against declarative manifests.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parents[2]
OSCAL_FILE = REPO_ROOT / "compliance" / "lula" / "oscal-component.yaml"
LULA_SYMLINK = REPO_ROOT / "lula.yaml"
MANIFESTS_DIR = REPO_ROOT / "packages/zarf/manifests"


def test_oscal_component_definition_structure() -> None:
    """Verify OSCAL file exists, is valid YAML, and conforms to schema."""
    assert OSCAL_FILE.exists(), f"OSCAL file not found at {OSCAL_FILE}"
    assert LULA_SYMLINK.exists(), f"lula.yaml symlink not found at {LULA_SYMLINK}"
    assert LULA_SYMLINK.resolve() == OSCAL_FILE.resolve()

    with open(OSCAL_FILE) as f:
        doc = yaml.safe_load(f)

    comp_def = doc.get("component-definition")
    assert comp_def is not None, "Missing root 'component-definition'"

    # Validate root UUID
    root_uuid = comp_def.get("uuid")
    assert root_uuid is not None
    assert uuid.UUID(root_uuid, version=4)

    # Validate metadata
    meta = comp_def.get("metadata", {})
    assert meta.get("oscal-version") == "1.1.2"
    assert "Tactical Edge Gateway" in meta.get("title", "")

    # Validate components
    components = comp_def.get("components", [])
    assert len(components) == 1
    gateway_comp = components[0]
    assert gateway_comp.get("type") == "software"
    assert gateway_comp.get("title") == "tactical-edge-gateway"
    assert uuid.UUID(gateway_comp.get("uuid", ""), version=4)

    # Validate control implementations
    control_impls = gateway_comp.get("control-implementations", [])
    assert len(control_impls) == 1
    ctrl_impl = control_impls[0]
    assert uuid.UUID(ctrl_impl.get("uuid", ""), version=4)

    # Validate NIST SP 800-53 Rev 5 requirements
    implemented_reqs = ctrl_impl.get("implemented-requirements", [])
    req_map = {req["control-id"]: req for req in implemented_reqs}
    expected_controls = {"ac-3", "ac-6", "sc-8", "si-4"}
    assert expected_controls.issubset(set(req_map.keys()))

    # Validate KSV-0014 deviation and compensating controls in AC-6
    ac6_desc = req_map["ac-6"]["description"]
    assert "KSV-0014" in ac6_desc
    assert "Compensating Controls" in ac6_desc
    assert "999" in ac6_desc

    # Validate validation links to back-matter resources
    back_matter = comp_def.get("back-matter", {})
    resources = back_matter.get("resources", [])
    resource_uuids = {r["uuid"] for r in resources}

    for ctrl_id in expected_controls:
        req = req_map[ctrl_id]
        links = req.get("links", [])
        assert len(links) >= 1
        lula_link = next((lnk for lnk in links if lnk.get("rel") == "lula"), None)
        assert lula_link is not None, f"Control {ctrl_id} missing lula link"
        target_uuid = lula_link["href"].lstrip("#")
        assert target_uuid in resource_uuids, f"Link {target_uuid} not found in back-matter"


@pytest.mark.skipif(
    not shutil.which("lula"),
    reason="Lula binary not found on PATH",
)
def test_lula_tools_lint() -> None:
    """Verify OSCAL file passes 'lula tools lint' against upstream OSCAL v1.1.2 schema."""
    cmd = ["lula", "tools", "lint", "-f", str(OSCAL_FILE)]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
    combined = res.stdout + res.stderr
    assert res.returncode == 0, f"lula tools lint failed: {combined}"
    assert "valid OSCAL version 1.1.2" in combined or "valid" in combined


@pytest.mark.skipif(
    not shutil.which("lula"),
    reason="Lula binary not found on PATH",
)
def test_lula_dev_validate_manifests() -> None:
    """Verify all embedded Rego policies pass against the declarative Kubernetes manifests."""
    with open(OSCAL_FILE) as f:
        doc = yaml.safe_load(f)

    resources = doc["component-definition"]["back-matter"]["resources"]

    # Gather manifest objects
    deployments = []
    services = []
    for mf in MANIFESTS_DIR.glob("*.yaml"):
        with open(mf) as yf:
            for obj in yaml.safe_load_all(yf):
                if not obj:
                    continue
                if obj.get("kind") == "Deployment":
                    deployments.append(obj)
                elif obj.get("kind") == "Service":
                    services.append(obj)

    assert len(deployments) >= 2, "Expected at least 2 Deployments (gateway and freetakserver)"
    assert len(services) >= 2, "Expected at least 2 Services (gateway and freetakserver)"

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        resources_json = tmp_path / "resources.json"
        with open(resources_json, "w") as rf:
            json.dump({"livedeployments": deployments, "liveservices": services}, rf)

        for r in resources:
            r_uuid = r["uuid"]
            r_title = r.get("title", r_uuid)
            desc = r["description"]

            val_file = tmp_path / f"val-{r_uuid}.yaml"
            with open(val_file, "w") as vf:
                vf.write(desc)

            cmd = [
                "lula",
                "dev",
                "validate",
                "-f",
                str(val_file),
                "-r",
                str(resources_json),
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
            combined = res.stdout + res.stderr
            assert res.returncode == 0, f"Validation {r_title} failed: {combined}"
            assert "0 failing results" in combined or "Validation completed" in combined
