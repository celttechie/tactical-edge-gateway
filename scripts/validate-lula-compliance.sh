#!/usr/bin/env bash
# ==============================================================================
# Tactical Edge Gateway - Lula OSCAL Compliance Validation Script
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
OSCAL_FILE="${REPO_ROOT}/compliance/lula/oscal-component.yaml"
MANIFESTS_DIR="${REPO_ROOT}/packages/zarf/manifests"
TMP_DIR="$(mktemp -d /tmp/teg-lula-verify-XXXXXX)"

trap 'rm -rf "${TMP_DIR}"' EXIT

echo "==> Verifying Defense Unicorns Lula CLI..."
if ! command -v lula &>/dev/null; then
    echo "ERROR: 'lula' binary not found on PATH." >&2
    echo "Install Lula from https://github.com/defenseunicorns/lula" >&2
    exit 1
fi
echo "Lula version: $(lula version)"

echo "==> Linting OSCAL Component Definition against OSCAL v1.1.2 schema..."
lula tools lint -f "${OSCAL_FILE}"
echo "OSCAL schema linting PASSED."

echo "==> Assembling mock Kubernetes resources from declarative manifests..."
python3 -c "
import json, yaml, sys
from pathlib import Path

manifests_dir = Path('${MANIFESTS_DIR}')
deployments = []
services = []

for f in manifests_dir.glob('*.yaml'):
    with open(f) as yf:
        docs = yaml.safe_load_all(yf)
        for doc in docs:
            if not doc:
                continue
            kind = doc.get('kind')
            if kind == 'Deployment':
                deployments.append(doc)
            elif kind == 'Service':
                services.append(doc)

res = {
    'livedeployments': deployments,
    'liveservices': services
}
with open('${TMP_DIR}/resources.json', 'w') as out:
    json.dump(res, out, indent=2)
"

echo "==> Extracting and evaluating embedded Lula validations..."
python3 -c "
import yaml
from pathlib import Path

with open('${OSCAL_FILE}') as f:
    oscal = yaml.safe_load(f)

resources = oscal.get('component-definition', {}).get('back-matter', {}).get('resources', [])
tmp_dir = Path('${TMP_DIR}')

for r in resources:
    uuid = r['uuid']
    title = r.get('title', uuid)
    desc = r.get('description', '')
    val_file = tmp_dir / f'val-{uuid}.yaml'
    with open(val_file, 'w') as out:
        out.write(desc)
    print(f'Extracted {title} ({uuid})')
"

for val_file in "${TMP_DIR}"/val-*.yaml; do
    echo "--> Running offline validation: $(basename "${val_file}")"
    lula dev validate -f "${val_file}" -r "${TMP_DIR}/resources.json"
done

echo "==> All offline policy validations PASSED (AC-3, AC-6, SC-8, SI-4)."

# Check if a live Kubernetes cluster has tactical-edge deployed
if command -v kubectl &>/dev/null && kubectl get namespace tactical-edge &>/dev/null; then
    echo "==> Live Kubernetes deployment detected in namespace 'tactical-edge'. Running live assessment..."
    SAR_OUTPUT="${TMP_DIR}/assessment-results.yaml"
    lula validate -f "${OSCAL_FILE}" -o "${SAR_OUTPUT}"
    echo "Live assessment completed. Findings summary:"
    grep -E "control-id:|state:" "${SAR_OUTPUT}" || true
else
    echo "==> Note: No live 'tactical-edge' namespace found. Live cluster assessment skipped."
fi

echo "==> Lula compliance validation completed successfully!"
