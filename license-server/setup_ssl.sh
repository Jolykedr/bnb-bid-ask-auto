#!/bin/bash
# Generate self-signed SSL certificate for license server.
# Run this ONCE on the server.
#
# The generated server.crt must be embedded into the desktop client
# for certificate pinning (copy its content to license_checker.py).

set -e

CERT_DIR="$(dirname "$0")/certs"
mkdir -p "$CERT_DIR"

CERT_FILE="$CERT_DIR/server.crt"
KEY_FILE="$CERT_DIR/server.key"

if [ -f "$CERT_FILE" ] && [ -f "$KEY_FILE" ]; then
    echo "Certificates already exist in $CERT_DIR"
    echo "Delete them first if you want to regenerate."
    echo ""
    echo "Current certificate fingerprint:"
    openssl x509 -in "$CERT_FILE" -noout -fingerprint -sha256
    exit 0
fi

echo "Generating self-signed SSL certificate..."

# Generate key + cert (valid for 10 years)
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 \
    -keyout "$KEY_FILE" \
    -out "$CERT_FILE" \
    -days 3650 \
    -nodes \
    -subj "/CN=license-server" \
    -addext "subjectAltName=IP:79.137.198.213"

chmod 600 "$KEY_FILE"
chmod 644 "$CERT_FILE"

echo ""
echo "=== SSL Certificate Generated ==="
echo "  Cert: $CERT_FILE"
echo "  Key:  $KEY_FILE"
echo ""
echo "=== Certificate Fingerprint (SHA-256) ==="
openssl x509 -in "$CERT_FILE" -noout -fingerprint -sha256
echo ""
echo "=== IMPORTANT ==="
echo "Copy the content of $CERT_FILE into the desktop client's"
echo "license_checker.py as PINNED_CERT for certificate pinning."
echo ""
echo "To view cert content:"
echo "  cat $CERT_FILE"
echo "================================="
