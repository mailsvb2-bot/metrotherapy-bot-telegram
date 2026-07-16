#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
ROOT_SOURCE="${MAX_TRUST_ROOT_FILE:-$REPO_ROOT/deploy/certs/russian_trusted_root_ca.crt}"
SUB_SOURCE="${MAX_TRUST_SUB_FILE:-$REPO_ROOT/deploy/certs/russian_trusted_sub_ca.crt}"
ROOT_FINGERPRINT="${MAX_TRUST_ROOT_FINGERPRINT:-D26D2D0231B7C39F92CC738512BA54103519E4405D68B5BD703E9788CA8ECF31}"
SUB_FINGERPRINT="${MAX_TRUST_SUB_FINGERPRINT:-BBBDE2103E790B999EC62BD03CF625A5A2E7C316E10AFE6A490EEDEAD8B3FD9B}"
OPENSSL_BIN="${OPENSSL_BIN:-$(command -v openssl || true)}"
INSTALL_BIN="${INSTALL_BIN:-$(command -v install || true)}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

for tool_name in OPENSSL_BIN INSTALL_BIN; do
  tool_value="${!tool_name}"
  if [ -z "$tool_value" ] || [ ! -x "$tool_value" ]; then
    printf 'ERROR: required tool is unavailable: %s\n' "$tool_name" >&2
    exit 41
  fi
done

for source_file in "$ROOT_SOURCE" "$SUB_SOURCE"; do
  if [ ! -f "$source_file" ] || [ ! -s "$source_file" ]; then
    printf 'ERROR: vendored certificate is missing or empty: %s\n' "$source_file" >&2
    exit 42
  fi
done

certificate_fingerprint() {
  local source_file="$1"
  "$OPENSSL_BIN" x509 -in "$source_file" -noout -fingerprint -sha256 \
    | awk -F= '{print $2}' \
    | tr -d ':' \
    | tr '[:lower:]' '[:upper:]'
}

verify_certificate() {
  local source_file="$1"
  local expected_fingerprint="$2"
  local expected_subject="$3"
  local actual_fingerprint
  local subject

  "$OPENSSL_BIN" x509 -in "$source_file" -noout >/dev/null
  "$OPENSSL_BIN" x509 -in "$source_file" -checkend 86400 -noout >/dev/null

  actual_fingerprint="$(certificate_fingerprint "$source_file")"
  if [ "$actual_fingerprint" != "$expected_fingerprint" ]; then
    printf 'ERROR: certificate DER fingerprint mismatch: %s\n' "$source_file" >&2
    exit 43
  fi

  subject="$("$OPENSSL_BIN" x509 -in "$source_file" -noout -subject -nameopt RFC2253)"
  case "$subject" in
    *"$expected_subject"*) ;;
    *)
      printf 'ERROR: unexpected certificate subject: %s\n' "$source_file" >&2
      exit 44
      ;;
  esac
}

verify_certificate "$ROOT_SOURCE" "$ROOT_FINGERPRINT" "CN=Russian Trusted Root CA"
verify_certificate "$SUB_SOURCE" "$SUB_FINGERPRINT" "CN=Russian Trusted Sub CA"
"$OPENSSL_BIN" verify -CAfile "$ROOT_SOURCE" "$ROOT_SOURCE" >/dev/null
"$OPENSSL_BIN" verify -CAfile "$ROOT_SOURCE" "$SUB_SOURCE" >/dev/null

if command -v update-ca-certificates >/dev/null 2>&1; then
  TRUST_DIR="${MAX_TRUST_DIR:-/usr/local/share/ca-certificates}"
  mkdir -p "$TRUST_DIR"
  "$INSTALL_BIN" -m 0644 "$ROOT_SOURCE" "$TRUST_DIR/russian_trusted_root_ca.crt"
  "$INSTALL_BIN" -m 0644 "$SUB_SOURCE" "$TRUST_DIR/russian_trusted_sub_ca.crt"
  update-ca-certificates >/dev/null
elif command -v update-ca-trust >/dev/null 2>&1; then
  TRUST_DIR="${MAX_TRUST_DIR:-/etc/pki/ca-trust/source/anchors}"
  mkdir -p "$TRUST_DIR"
  "$INSTALL_BIN" -m 0644 "$ROOT_SOURCE" "$TRUST_DIR/russian_trusted_root_ca.crt"
  "$INSTALL_BIN" -m 0644 "$SUB_SOURCE" "$TRUST_DIR/russian_trusted_sub_ca.crt"
  update-ca-trust extract >/dev/null
else
  printf 'ERROR: neither update-ca-certificates nor update-ca-trust is available\n' >&2
  exit 45
fi

"$PYTHON_BIN" - <<'PY'
import urllib.error
import urllib.request

url = "https://platform-api2.max.ru/me"
try:
    with urllib.request.urlopen(url, timeout=20) as response:
        status = int(response.status)
except urllib.error.HTTPError as exc:
    status = int(exc.code)
except Exception as exc:
    raise SystemExit(f"MAX API2 TLS verification failed: {type(exc).__name__}: {exc}") from exc

if status not in {200, 400, 401, 403, 405}:
    raise SystemExit(f"MAX API2 TLS probe returned unexpected HTTP status: {status}")
PY

printf 'MAX_API2_TRUST_OK root_fingerprint=%s sub_fingerprint=%s\n' \
  "$ROOT_FINGERPRINT" \
  "$SUB_FINGERPRINT"
