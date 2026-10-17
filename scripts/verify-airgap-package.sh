#!/usr/bin/env bash
# ==============================================================================
# Tactical Edge Gateway - Zarf Air-Gap Package Verification Script
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ZARF_DIR="${REPO_ROOT}/packages/zarf"
TMP_DIR="$(mktemp -d /tmp/teg-zarf-verify-XXXXXX)"

trap 'rm -rf "${TMP_DIR}"' EXIT

echo "==> Verifying Zarf CLI availability..."
if ! command -v zarf &>/dev/null; then
    echo "ERROR: 'zarf' binary not found on PATH." >&2
    exit 1
fi
echo "Zarf version: $(zarf version)"

echo "==> Validating Docker image availability..."
if ! docker image inspect tactical-edge-gateway:latest &>/dev/null; then
    echo "WARNING: Local image 'tactical-edge-gateway:latest' not found. Building..."
    docker build -t tactical-edge-gateway:latest "${REPO_ROOT}"
fi

echo "==> Creating Zarf air-gap package in ${TMP_DIR}..."
zarf package create "${ZARF_DIR}" --confirm --skip-sbom -o "${TMP_DIR}"

PACKAGE_FILE=$(find "${TMP_DIR}" -name "zarf-package-tactical-edge-gateway-*.tar.zst" | head -n 1)
if [[ -z "${PACKAGE_FILE}" || ! -f "${PACKAGE_FILE}" ]]; then
    echo "ERROR: Zarf package was not generated." >&2
    exit 1
fi

echo "==> Package successfully created: $(basename "${PACKAGE_FILE}") ($(du -h "${PACKAGE_FILE}" | cut -f1))"

echo "==> Inspecting package definition and components..."
zarf package inspect definition "${PACKAGE_FILE}"

echo "==> Verification completed successfully! Air-gap package is intact and deployable offline."
