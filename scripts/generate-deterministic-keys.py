#!/usr/bin/env python3
"""
Tactical Edge Gateway - Deterministic Cryptographic Identity & SSH Key Generator
================================================================================
Derives reproducible, cryptographically sound Ed25519 SSH host keypairs and service
identities from a pinned master seed and node identity context using HKDF-SHA256.

Architectural Rationale:
In ephemeral tactical edge testbeds and CI/CD pipelines, non-deterministic host keys
force engineers to disable StrictHostKeyChecking (a severe security anti-pattern).
This tool generates identical, cryptographically valid OpenSSH keys across rebuilds
without persisting private keys into source control.
"""

import argparse
import base64
import hashlib
import os
import struct
import sys
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)


def derive_ed25519_seed(master_seed: str, identity: str) -> bytes:
    """
    Derives 32 bytes of deterministic entropy for Ed25519 using HKDF-SHA256.
    """
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"tactical-edge-gateway-identity-v1",
        info=identity.encode("utf-8"),
    )
    return hkdf.derive(master_seed.encode("utf-8"))


def generate_deterministic_ed25519(master_seed: str, identity: str, output_dir: str):
    """
    Generates deterministic Ed25519 private and public keys and saves them in OpenSSH format.
    """
    os.makedirs(output_dir, mode=0o700, exist_ok=True)
    private_raw = derive_ed25519_seed(master_seed, identity)
    private_key = ed25519.Ed25519PrivateKey.from_private_bytes(private_raw)
    public_key = private_key.public_key()

    priv_pem = private_key.private_bytes(
        encoding=Encoding.PEM,
        format=PrivateFormat.OpenSSH,
        encryption_algorithm=NoEncryption(),
    )

    pub_bytes = public_key.public_bytes(
        encoding=Encoding.OpenSSH,
        format=PublicFormat.OpenSSH,
    )

    key_filename = f"id_ed25519_{identity.replace('/', '_').replace(' ', '_')}"
    priv_path = os.path.join(output_dir, key_filename)
    pub_path = f"{priv_path}.pub"

    with open(priv_path, "wb") as f:
        f.write(priv_pem)
    os.chmod(priv_path, 0o600)

    with open(pub_path, "wb") as f:
        f.write(pub_bytes)
        f.write(b"\n")
    os.chmod(pub_path, 0o644)

    pub_str = pub_bytes.decode("utf-8")
    print(f"Generated deterministic Ed25519 key for '{identity}':")
    print(f"  Private Key: {priv_path} (mode 0600)")
    print(f"  Public Key:  {pub_path} (mode 0644)")
    print(f"  Fingerprint: {pub_str}")
    return priv_path, pub_str


def main():
    parser = argparse.ArgumentParser(
        description="Generate deterministic Ed25519 SSH host keys for tactical edge nodes."
    )
    parser.add_argument(
        "--seed",
        default="tactical-edge-gateway-deterministic-seed-2026",
        help="Master seed phrase/secret for derivation",
    )
    parser.add_argument(
        "--identity",
        default="edge-gateway-node01",
        help="Identity string / hostname for derivation context",
    )
    parser.add_argument(
        "--output-dir",
        default=".keys",
        help="Directory to save generated keys",
    )
    args = parser.parse_args()

    generate_deterministic_ed25519(args.seed, args.identity, args.output_dir)


if __name__ == "__main__":
    main()
