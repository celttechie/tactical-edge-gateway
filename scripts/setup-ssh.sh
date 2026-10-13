#!/usr/bin/env bash
# ==============================================================================
# Tactical Edge Gateway - Automated Lab SSH Onboarding & Setup Script
# ==============================================================================
# Configures dedicated SSH keypairs, updates ~/.ssh/config with StrictHostKeyChecking
# accept-new, seeds known_hosts, and isolates the tactical edge environment.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

CURRENT_USER="$(id -un)"
DEFAULT_KEY="$HOME/.ssh/id_tactical_edge_ed25519"
SSH_KEY="${SSH_KEY:-$DEFAULT_KEY}"

echo -e "${BLUE}======================================================================${NC}"
echo -e "${BLUE}  Tactical Edge Gateway - SSH Environment & Identity Setup            ${NC}"
echo -e "${BLUE}======================================================================${NC}"

SSH_KEY="${SSH_KEY/#\~/$HOME}"
SSH_PUB_KEY="${SSH_KEY}.pub"
SSH_DIR="$HOME/.ssh"
SSH_CONFIG="$SSH_DIR/config"

mkdir -p "$SSH_DIR"
chmod 700 "$SSH_DIR"

if [ ! -f "$SSH_KEY" ]; then
    echo -e "\n==> Generating dedicated Ed25519 client keypair: ${SSH_KEY}..."
    ssh-keygen -t ed25519 -f "$SSH_KEY" -C "tactical-edge-${CURRENT_USER}" -N ""
    chmod 600 "$SSH_KEY"
    chmod 644 "$SSH_PUB_KEY"
    echo -e "${GREEN}==> Client keypair created successfully.${NC}"
else
    echo -e "==> Using existing SSH keypair: ${SSH_KEY}"
fi

touch "$SSH_CONFIG"
chmod 600 "$SSH_CONFIG"

echo -e "\n==> Verifying ~/.ssh/config host configuration..."
if ! grep -q "Host tactical-edge-*" "$SSH_CONFIG" 2>/dev/null; then
    cat << EOF >> "$SSH_CONFIG"

# ==============================================================================
# Tactical Edge Gateway - Lab Host Definitions
# ==============================================================================
Host tactical-edge-gateway
    HostName 127.0.0.1
    Port 2222
    User edgeuser
    IdentityFile ${SSH_KEY}
    StrictHostKeyChecking accept-new
    UserKnownHostsFile ~/.ssh/known_hosts_tactical_edge
EOF
    echo -e "${GREEN}==> Added tactical-edge-gateway block to ${SSH_CONFIG}.${NC}"
else
    echo -e "==> Host entry already present in ${SSH_CONFIG}."
fi

echo -e "\n${GREEN}==> SSH environment initialization complete.${NC}"
