#!/usr/bin/env bash
# Create a private key + certificate signing request (CSR) for the Paperwork host.
#
# Use this when the InfoOrigin CA team signs your request instead of handing you
# a ready-made PFX. The private key is generated here and never leaves the server;
# you send them only the .csr file.
#
#   sudo ./make-csr.sh [hostname] [output-dir]
#
# Defaults: hostname = info.pdf.infoorigin.com, output-dir = /root/tls
set -euo pipefail

HOST="${1:-info.pdf.infoorigin.com}"
OUT_DIR="${2:-/root/tls}"
KEY="$OUT_DIR/$HOST.key"
CSR="$OUT_DIR/$HOST.csr"
EXT="$OUT_DIR/$HOST.ext.cnf"

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this with sudo." >&2
    exit 1
fi

install -d -m 700 "$OUT_DIR"

if [ -s "$KEY" ]; then
    echo "Reusing existing key: $KEY"
else
    openssl genrsa -out "$KEY" 2048 2>/dev/null
    chmod 600 "$KEY"
    echo "Created private key: $KEY"
fi

# Extensions the signed certificate must carry. SAN is mandatory: browsers
# ignore CN today, so a certificate without subjectAltName is rejected.
cat > "$EXT" <<EOF
basicConstraints = CA:FALSE
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = DNS:$HOST
EOF

rm -f "$CSR"
openssl req -new -key "$KEY" -sha256 -out "$CSR" \
    -subj "/CN=$HOST/O=InfoOrigin/OU=IT/C=IN" \
    -addext "subjectAltName=DNS:$HOST" 2>/dev/null

echo
echo "=== CSR to send to the InfoOrigin CA team ==="
echo "File: $CSR"
echo "Subject: CN=$HOST, O=InfoOrigin, OU=IT, C=IN"
echo "SAN    : DNS:$HOST"
echo
cat "$CSR"
echo
cat <<EOF
=== What to ask for ===
A server certificate for $HOST, issued by the InfoOrigin root CA, in PEM
format. If they need the signing command themselves:

  openssl x509 -req -in $CSR -CA root-ca.crt -CAkey root-ca.key \\
    -CAcreateserial -days 825 -sha256 \\
    -extfile $EXT -out $HOST.crt

Then install it here with:

  sudo ./install-cert.sh $HOST.crt $KEY
EOF
