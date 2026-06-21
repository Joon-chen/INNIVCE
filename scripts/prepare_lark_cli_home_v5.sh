#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

ACTION="${1:-check}"
LARK_CLI_HOME="${LARK_CLI_HOME:-$PWD/.local/lark-cli-v5}"
LARK_CLI_DATA_HOME="${LARK_CLI_DATA_HOME:-$LARK_CLI_HOME/share}"
LARK_BRAND="${LARK_BRAND:-feishu}"
LARK_PROFILE="${LARK_PROFILE:-v5-local-prod}"
LARK_AUTH_DOMAINS="${LARK_AUTH_DOMAINS:-approval,attendance,base,calendar,contact,docs,drive,event,im,mail,minutes,okr,task,vc,wiki}"
CLI_IMAGE="${CLI_IMAGE:-digital-advisor-api:latest}"
PYTHON_BIN="${PYTHON:-python3}"
tmp_home=""

cleanup() {
  if [[ -n "$tmp_home" ]]; then
    rm -rf "$tmp_home"
  fi
}
trap cleanup EXIT

usage() {
  cat <<'USAGE'
Usage:
  scripts/prepare_lark_cli_home_v5.sh check
  LARK_APP_ID=cli_xxx scripts/prepare_lark_cli_home_v5.sh init-bot < app_secret.txt
  LARK_APP_ID=cli_xxx scripts/prepare_lark_cli_home_v5.sh init-bot-container < app_secret.txt
  scripts/prepare_lark_cli_home_v5.sh auth-user-start
  LARK_DEVICE_CODE=xxx scripts/prepare_lark_cli_home_v5.sh auth-user-complete

Environment:
  LARK_CLI_HOME       Dedicated CLI config directory mounted into containers.
  LARK_CLI_DATA_HOME  Dedicated CLI encrypted secret store. Defaults to LARK_CLI_HOME/share.
  LARK_APP_ID         Feishu/Lark app id for init-bot.
  LARK_BRAND          feishu or lark. Defaults to feishu.
  LARK_PROFILE        CLI profile name. Defaults to v5-local-prod.
  LARK_AUTH_DOMAINS   Comma-separated domains for user auth.
  CLI_IMAGE           Docker image used for container-side doctor check.

Security:
  init-bot reads appSecret from stdin so it does not appear in shell history or ps output.
  init-bot-container stores the appSecret in LARK_CLI_HOME using CLI_IMAGE, so the same
  mounted directory can be read by local-prod containers without macOS Keychain access.
USAGE
}

ensure_tools() {
  if ! command -v lark-cli >/dev/null 2>&1; then
    echo "ERROR: lark-cli not found in PATH." >&2
    exit 1
  fi
  if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "ERROR: python3 not found in PATH." >&2
    exit 1
  fi
}

if [[ "$ACTION" == "-h" || "$ACTION" == "--help" || "$ACTION" == "help" ]]; then
  usage
  exit 0
fi

prepare_home() {
  mkdir -p "$LARK_CLI_HOME"
  mkdir -p "$LARK_CLI_DATA_HOME"
  chmod 700 "$LARK_CLI_HOME"
  chmod 700 "$LARK_CLI_DATA_HOME"
  tmp_home="$(mktemp -d)"
  ln -s "$LARK_CLI_HOME" "$tmp_home/.lark-cli"
  mkdir -p "$tmp_home/.local/share"
  ln -s "$LARK_CLI_DATA_HOME" "$tmp_home/.local/share/lark-cli"
}

run_lark() {
  HOME="$tmp_home" lark-cli "$@"
}

doctor_check() {
  local host_status=0
  run_lark config show || host_status=$?
  run_lark doctor --offline || host_status=$?
  if command -v docker >/dev/null 2>&1 && docker image inspect "$CLI_IMAGE" >/dev/null 2>&1; then
    docker run --rm \
      -v "$LARK_CLI_HOME:/root/.lark-cli:ro" \
      -v "$LARK_CLI_DATA_HOME:/root/.local/share/lark-cli:ro" \
      "$CLI_IMAGE" lark-cli doctor --offline
  else
    echo "WARN: Docker image $CLI_IMAGE not found; skipped container-side doctor check." >&2
    return "$host_status"
  fi
}

auth_user_start() {
  mkdir -p tmp
  local output
  output="$(run_lark auth login --domain "$LARK_AUTH_DOMAINS" --no-wait --json)"
  local parsed url device_code
  parsed="$("$PYTHON_BIN" -c '
import json
import sys

payload = json.loads(sys.stdin.read())
url = payload.get("verification_url") or payload.get("verification_uri_complete") or payload.get("verification_uri")
device_code = payload.get("device_code")
if not url or not device_code:
    raise SystemExit(f"ERROR: unable to parse auth output: {payload}")
print(url)
print(device_code)
' <<<"$output")"
  url="$(printf '%s\n' "$parsed" | sed -n '1p')"
  device_code="$(printf '%s\n' "$parsed" | sed -n '2p')"
  lark-cli auth qrcode "$url" --output tmp/v5-lark-auth-qrcode.png >/dev/null
  echo "Open this URL and complete authorization:"
  echo "$url"
  echo "QR code: tmp/v5-lark-auth-qrcode.png"
  echo "Then run:"
  echo "LARK_DEVICE_CODE=$device_code scripts/prepare_lark_cli_home_v5.sh auth-user-complete"
}

ensure_tools
prepare_home

case "$ACTION" in
  check)
    doctor_check
    ;;
  init-bot)
    if [[ -z "${LARK_APP_ID:-}" ]]; then
      echo "ERROR: LARK_APP_ID is required for init-bot." >&2
      exit 1
    fi
    run_lark config init --app-id "$LARK_APP_ID" --app-secret-stdin --brand "$LARK_BRAND" --name "$LARK_PROFILE"
    doctor_check
    ;;
  init-bot-container)
    if [[ -z "${LARK_APP_ID:-}" ]]; then
      echo "ERROR: LARK_APP_ID is required for init-bot-container." >&2
      exit 1
    fi
    if ! command -v docker >/dev/null 2>&1; then
      echo "ERROR: docker not found in PATH." >&2
      exit 1
    fi
    if ! docker image inspect "$CLI_IMAGE" >/dev/null 2>&1; then
      echo "ERROR: Docker image $CLI_IMAGE not found. Build local-prod image before init-bot-container." >&2
      exit 1
    fi
    docker run --rm -i \
      -v "$LARK_CLI_HOME:/root/.lark-cli" \
      -v "$LARK_CLI_DATA_HOME:/root/.local/share/lark-cli" \
      "$CLI_IMAGE" \
      lark-cli config init --app-id "$LARK_APP_ID" --app-secret-stdin --brand "$LARK_BRAND" --name "$LARK_PROFILE"
    doctor_check
    ;;
  auth-user-start)
    auth_user_start
    ;;
  auth-user-complete)
    if [[ -z "${LARK_DEVICE_CODE:-}" ]]; then
      echo "ERROR: LARK_DEVICE_CODE is required for auth-user-complete." >&2
      exit 1
    fi
    run_lark auth login --device-code "$LARK_DEVICE_CODE"
    doctor_check
    ;;
  *)
    usage >&2
    exit 1
    ;;
esac
