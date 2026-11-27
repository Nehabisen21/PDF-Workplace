#!/usr/bin/env bash
# Install a PKCS#12 bundle (.pfx / .p12) exported by the InfoOrigin CA team.
#
#   sudo ./install-pfx.sh bundle.pfx [password] [root-ca.crt]
#
# Extracts the certificate, its chain and the private key, then hands them to
# install-cert.sh which validates everything and reloads nginx.
set -euo pipefail

PFX="${1:?usage: install-pfx.sh bundle.pfx [password] [root-ca.crt]}"
PASS="${2:-}"
ROOT_CA="${3:-}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this with sudo." >&2
    exit 1
fi
[ -f "$PFX" ] || { echo "Missing file: $PFX" >&2; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

if [ -n "$PASS" ]; then
    PASSIN=(-passin "pass:$PASS")
else
    PASSIN=(-passin pass:)
fi

# A wrong password must reach the friendly error below, not abort the script.
# Leaf certificate (the one whose key is in this bundle).
openssl pkcs12 -in "$PFX" "${PASSIN[@]}" -nokeys -clcerts -out "$WORK/server.crt" 2>/dev/null || true
# Private key, unencrypted for nginx but stored 600 by install-cert.sh.
openssl pkcs12 -in "$PFX" "${PASSIN[@]}" -nocerts -nodes -out "$WORK/server.key" 2>/dev/null || true
# Any CA certificates bundled with it become the chain (may legitimately be empty).
openssl pkcs12 -in "$PFX" "${PASSIN[@]}" -nokeys -cacerts -out "$WORK/chain.crt" 2>/dev/null || true

if [ ! -s "$WORK/server.crt" ] || [ ! -s "$WORK/server.key" ]; then
    echo "ERROR: could not read the certificate and key from $PFX." >&2
    echo "       Check the password. An export that contains only a CA cannot be used." >&2
    exit 2
fi

ARGS=("$WORK/server.crt" "$WORK/server.key")
if [ -s "$WORK/chain.crt" ]; then
    ARGS+=("$WORK/chain.crt")
fi
if [ -n "$ROOT_CA" ]; then
    ARGS+=("$ROOT_CA")
fi

exec "$HERE/install-cert.sh" "${ARGS[@]}"
