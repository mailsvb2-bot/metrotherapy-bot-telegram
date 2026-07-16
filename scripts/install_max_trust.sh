#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_URL="${MAX_TRUST_ROOT_URL:-https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt}"
SUB_URL="${MAX_TRUST_SUB_URL:-https://gu-st.ru/content/lending/russian_trusted_sub_ca_pem.crt}"
ROOT_SHA256="${MAX_TRUST_ROOT_SHA256:-936a43fea6e8e525bcc0f81acd9c3d21b4fc4b9b68acea7906d698005afc6504}"
SUB_SHA256="${MAX_TRUST_SUB_SHA256:-f0ae589f36774f29ef3648f7984b08d42fcce6f1ffeeb6236d773daeb2744ea6}"
CURL_BIN="${CURL_BIN:-$(command -v curl || true)}"
OPENSSL_BIN="${OPENSSL_BIN:-$(command -v openssl || true)}"
SHA256SUM_BIN="${SHA256SUM_BIN:-$(command -v sha256sum || true)}"
INSTALL_BIN="${INSTALL_BIN:-$(command -v install || true)}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

for tool_name in CURL_BIN OPENSSL_BIN SHA256SUM_BIN INSTALL_BIN; do
  tool_value="${!tool_name}"
  if [ -z "$tool_value" ] || [ ! -x "$tool_value" ]; then
    printf 'ERROR: required tool is unavailable: %s\n' "$tool_name" >&2
    exit 41
  fi
done

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT INT TERM HUP
ROOT_TMP="$TMP_DIR/russian_trusted_root_ca.crt"
SUB_TMP="$TMP_DIR/russian_trusted_sub_ca.crt"

fetch_and_verify() {
  local url="$1"
  local destination="$2"
  local expected_sha="$3"
  local expected_subject="$4"
  local actual_sha
  local subject

  "$CURL_BIN" \
    --fail \
    --location \
    --silent \
    --show-error \
    --proto '=https' \
    --tlsv1.2 \
    --connect-timeout 15 \
    --max-time 60 \
    --output "$destination" \
    "$url"

  actual_sha="$("$SHA256SUM_BIN" "$destination" | awk '{print $1}')"
  if [ "$actual_sha" != "$expected_sha" ]; then
    printf 'ERROR: certificate checksum mismatch for %s\n' "$url" >&2
    exit 42
  fi

  "$OPENSSL_BIN" x509 -in "$destination" -noout >/dev/null
  "$OPENSSL_BIN" x509 -in "$destination" -checkend 86400 -noout >/dev/null
  subject="$("$OPENSSL_BIN" x509 -in "$destination" -noout -subject -nameopt RFC2253)"
  case "$subject" in
    *"$expected_subject"*) ;;
    *)
      printf 'ERROR: unexpected certificate subject for %s: %s\n' "$url" "$subject" >&2
      exit 43
      ;;
  esac
}

fetch_and_verify "$ROOT_URL" "$ROOT_TMP" "$ROOT_SHA256" "CN=Russian Trusted Root CA"
fetch_and_verify "$SUB_URL" "$SUB_TMP" "$SUB_SHA256" "CN=Russian Trusted Sub CA"
"$OPENSSL_BIN" verify -CAfile "$ROOT_TMP" "$ROOT_TMP" >/dev/null
"$OPENSSL_BIN" verify -CAfile "$ROOT_TMP" "$SUB_TMP" >/dev/null

if command -v update-ca-certificates >/dev/null 2>&1; then
  TRUST_DIR="${MAX_TRUST_DIR:-/usr/local/share/ca-certificates}"
  mkdir -p "$TRUST_DIR"
  "$INSTALL_BIN" -m 0644 "$ROOT_TMP" "$TRUST_DIR/russian_trusted_root_ca.crt"
  "$INSTALL_BIN" -m 0644 "$SUB_TMP" "$TRUST_DIR/russian_trusted_sub_ca.crt"
  update-ca-certificates >/dev/null
elif command -v update-ca-trust >/dev/null 2>&1; then
  TRUST_DIR="${MAX_TRUST_DIR:-/etc/pki/ca-trust/source/anchors}"
  mkdir -p "$TRUST_DIR"
  "$INSTALL_BIN" -m 0644 "$ROOT_TMP" "$TRUST_DIR/russian_trusted_root_ca.crt"
  "$INSTALL_BIN" -m 0644 "$SUB_TMP" "$TRUST_DIR/russian_trusted_sub_ca.crt"
  update-ca-trust extract >/dev/null
else
  printf 'ERROR: neither update-ca-certificates nor update-ca-trust is available\n' >&2
  exit 44
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

printf 'MAX_API2_TRUST_OK root_sha256=%s sub_sha256=%s\n' "$ROOT_SHA256" "$SUB_SHA256"
