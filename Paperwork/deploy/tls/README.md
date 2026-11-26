# TLS certificate for `info.pdf.infoorigin.com`

The browser warning ("Not secure") comes from the certificate nginx serves.
`paperwork.crt` on the server is self-signed, so no client trusts it.

`InfoOrigin Root CA.cer` in the Downloads folder is a **CA certificate**. It is
used to *verify* certificates; it cannot be served by nginx. What the server
needs is a **server certificate for this hostname plus its private key**, issued
by that CA.

## Fastest check first

The VM may already hold a usable certificate from another site:

```bash
sudo grep -rhE "ssl_certificate(_key)?[[:space:]]" /etc/nginx/sites-enabled/ | sort -u
```

If one of those points at a `*.infoorigin.com` wildcard, reuse it:

```bash
cd /opt/PDF-Workplace/Paperwork/deploy/tls
sudo ./install-cert.sh /path/to/wildcard.fullchain.crt /path/to/wildcard.key
```

## Option 1 — certificate issued by the InfoOrigin CA (preferred)

Generate the key and CSR on the server, send only the CSR to IT:

```bash
cd /opt/PDF-Workplace/Paperwork/deploy/tls
sudo ./make-csr.sh
```

IT returns a PEM certificate signed by the InfoOrigin root CA. Install it:

```bash
sudo ./install-cert.sh info.pdf.infoorigin.com.crt /root/tls/info.pdf.infoorigin.com.key
```

If they also give you the root CA file, pass it last so the server itself can
verify the site:

```bash
sudo ./install-cert.sh info.pdf.infoorigin.com.crt /root/tls/info.pdf.infoorigin.com.key "" "InfoOrigin Root CA.cer"
```

## Option 2 — PFX/P12 export from IT

```bash
cd /opt/PDF-Workplace/Paperwork/deploy/tls
sudo ./install-pfx.sh /path/to/infoorigin.pfx 'the-password'
```

The script extracts the leaf certificate, its chain and the key, checks that
they match, refuses CA certificates, updates
`/etc/nginx/sites-available/paperwork`, runs `nginx -t` and reloads.

## Verify

```bash
curl -sSI https://info.pdf.infoorigin.com | head -1
openssl s_client -connect info.pdf.infoorigin.com:443 -servername info.pdf.infoorigin.com </dev/null \
  | openssl x509 -noout -subject -issuer -dates
```

From a domain-joined PC the padlock should appear with no warning, because the
InfoOrigin root CA is already trusted there.

## About DNS

DNS is not the problem: `info.pdf.infoorigin.com` resolves to `172.30.30.253`,
which is correct for an on-prem host. Only the certificate needs replacing. A
publicly trusted certificate (Let's Encrypt DNS-01 via Route 53) is possible if
the site must be reachable outside the LAN, but it is not required for internal
users.
