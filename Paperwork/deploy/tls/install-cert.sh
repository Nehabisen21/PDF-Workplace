#!/usr/bin/env bash
# Install a PEM server certificate + private key for Paperwork and reload nginx.
#
#   sudo ./install-cert.sh server.crt server.key [chain.crt] [root-ca.crt]
#
#   chain.crt   optional intermediate/CA chain, appended to the certificate
#   root-ca.crt optional InfoOrigin root CA, added to the server trust store so
#               local curl checks against the site start passing
#
# Refuses to install a CA certificate, and refuses a key that does not match
# the certificate, so a wrong file can never take the site down.
set -euo pipefail

HOST="info.pdf.infoorigin.com"
CERT="${1:?usage: install-cert.sh server.crt server.key [chain.crt] [root-ca.crt]}"
KEY="${2:?usage: install-cert.sh server.crt server.key [chain.crt] [root-ca.crt]}"
CHAIN="${3:-}"
ROOT_CA="${4:-}"

DEST_CERT="/etc/ssl/certs/$HOST.fullchain.crt"
DEST_KEY="/etc/ssl/private/$HOST.key"
NGINX_SITE="/etc/nginx/sites-available/paperwork"

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this with sudo." >&2
    exit 1
fi
for f in "$CERT" "$KEY" ${CHAIN:+"$CHAIN"}; do
    [ -f "$f" ] || { echo "Missing file: $f" >&2; exit 1; }
done

if ! openssl x509 -in "$CERT" -noout >/dev/null 2>&1; then
    echo "ERROR: $CERT is not a PEM certificate." >&2
    exit 2
fi

# A CA certificate (basicConstraints CA:TRUE) cannot be used to serve TLS.
if openssl x509 -in "$CERT" -noout -text | grep -q "CA:TRUE"; then
    echo "ERROR: $CERT is a CA certificate, not a server certificate." >&2
    echo "       Ask the CA team for a certificate for $HOST (subjectAltName DNS:$HOST)." >&2
    exit 3
fi

cert_pub="$(openssl x509 -in "$CERT" -noout -pubkey | openssl pkey -pubin -outform DER 2>/dev/null | openssl dgst -sha256)"
key_pub="$(openssl pkey -in "$KEY" -pubout -outform DER 2>/dev/null | openssl dgst -sha256)"
if [ -z "$cert_pub" ] || [ "$cert_pub" != "$key_pub" ]; then
    echo "ERROR: the certificate and the private key do not belong together." >&2
    exit 4
fi

if ! openssl x509 -in "$CERT" -noout -text | grep -A1 "Subject Alternative Name" | grep -q "$HOST"; then
    echo "WARNING: $HOST is not in the certificate subjectAltName." >&2
    echo "         Browsers will still warn. Continue only if this is intentional." >&2
fi

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
cat "$CERT" > "$TMP"
if [ -n "$CHAIN" ]; then
    cat "$CHAIN" >> "$TMP"
fi
install -m 644 "$TMP" "$DEST_CERT"
install -m 600 "$KEY" "$DEST_KEY"

echo "Installed certificate: $DEST_CERT"
echo "Installed private key: $DEST_KEY"
openssl x509 -in "$DEST_CERT" -noout -subject -issuer -dates

if [ -n "$ROOT_CA" ]; then
    install -m 644 "$ROOT_CA" "/usr/local/share/ca-certificates/infoorigin-root-ca.crt"
    update-ca-certificates >/dev/null
    echo "Added $ROOT_CA to the server trust store."
fi

if [ -f "$NGINX_SITE" ]; then
    cp "$NGINX_SITE" "$NGINX_SITE.bak.$(date +%s)"
    sed -i -E "s|^([[:space:]]*ssl_certificate[[:space:]]+).*;|\1$DEST_CERT;|" "$NGINX_SITE"
    sed -i -E "s|^([[:space:]]*ssl_certificate_key[[:space:]]+).*;|\1$DEST_KEY;|" "$NGINX_SITE"
    echo "Updated $NGINX_SITE (backup kept)."
else
    echo "WARNING: $NGINX_SITE not found; set the paths in nginx manually." >&2
fi

nginx -t
systemctl reload nginx

echo
echo "=== Verification ==="
curl -sS -o /dev/null -w "healthz: HTTP %{http_code}, tls verify: %{ssl_verify_result}\n" "https://$HOST/healthz" \
    || echo "curl could not verify the chain; check that '${ROOT_CA:-the root CA}' is trusted by this client."
