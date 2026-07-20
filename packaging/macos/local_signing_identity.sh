#!/bin/bash
set -euo pipefail

IDENTITY_NAME="Photo Curator Local Development"
LOGIN_KEYCHAIN="$HOME/Library/Keychains/login.keychain-db"

identity_exists() {
  /usr/bin/security find-identity -v -p codesigning "$LOGIN_KEYCHAIN" 2>/dev/null \
    | /usr/bin/grep -Fq "\"$IDENTITY_NAME\""
}

certificate_exists() {
  /usr/bin/security find-certificate -c "$IDENTITY_NAME" "$LOGIN_KEYCHAIN" >/dev/null 2>&1
}

if [[ "${1:---print}" == "--print" ]]; then
  if identity_exists; then
    echo "$IDENTITY_NAME"
  else
    echo "-"
  fi
  exit 0
fi

if [[ "$1" != "--create" ]]; then
  echo "Usage: $0 --print|--create" >&2
  exit 2
fi

if identity_exists; then
  echo "Local signing identity already exists: $IDENTITY_NAME"
  exit 0
fi
if [[ ! -f "$LOGIN_KEYCHAIN" ]]; then
  echo "Login keychain not found: $LOGIN_KEYCHAIN" >&2
  exit 1
fi
if ! command -v openssl >/dev/null 2>&1; then
  echo "OpenSSL is required to create the local signing identity" >&2
  exit 1
fi

WORK_DIR="$(mktemp -d)"
trap '/bin/rm -rf "$WORK_DIR"' EXIT

if certificate_exists; then
  /usr/bin/security find-certificate -c "$IDENTITY_NAME" -p "$LOGIN_KEYCHAIN" \
    >"$WORK_DIR/identity.crt"
else
  PASSWORD="$(/usr/bin/uuidgen)$(/usr/bin/uuidgen)"
  /bin/cat >"$WORK_DIR/openssl.cnf" <<EOF
[req]
distinguished_name = subject
x509_extensions = extensions
prompt = no

[subject]
CN = $IDENTITY_NAME
O = Photo Curator

[extensions]
basicConstraints = critical,CA:false
keyUsage = critical,digitalSignature
extendedKeyUsage = critical,codeSigning
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid
EOF

  openssl req -new -newkey rsa:2048 -nodes -x509 -days 3650 -sha256 \
    -config "$WORK_DIR/openssl.cnf" \
    -keyout "$WORK_DIR/identity.key" -out "$WORK_DIR/identity.crt" >/dev/null 2>&1
  openssl pkcs12 -export -legacy -name "$IDENTITY_NAME" \
    -inkey "$WORK_DIR/identity.key" -in "$WORK_DIR/identity.crt" \
    -passout "pass:$PASSWORD" -out "$WORK_DIR/identity.p12" >/dev/null 2>&1

  /usr/bin/security import "$WORK_DIR/identity.p12" -k "$LOGIN_KEYCHAIN" \
    -P "$PASSWORD" -T /usr/bin/codesign -T /usr/bin/security >/dev/null
fi

if ! /usr/bin/security add-trusted-cert -r trustRoot -p codeSign \
  -k "$LOGIN_KEYCHAIN" "$WORK_DIR/identity.crt"; then
  echo "macOS did not authorize trust settings. Re-run 'make local-signing-identity'" >&2
  echo "and approve the Keychain authorization prompt." >&2
  exit 1
fi

if ! identity_exists; then
  echo "Identity was imported but is not valid for code signing" >&2
  exit 1
fi
echo "Created local signing identity: $IDENTITY_NAME"
