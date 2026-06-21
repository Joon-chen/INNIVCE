#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

COMPOSE_PROJECT="${COMPOSE_PROJECT:-v5launchcheck}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.local-prod.yml}"
API_URL="${API_URL:-http://127.0.0.1:8000}"
KEEP_LOCAL_PROD="${KEEP_LOCAL_PROD:-0}"
ALLOW_DEGRADED_FEISHU_CLI="${ALLOW_DEGRADED_FEISHU_CLI:-0}"
PYTHON_BIN="${PYTHON:-python3}"
overview_file=""
doctor_file=""
console_file=""
console_assets_file=""

cleanup() {
  if [[ -n "$overview_file" ]]; then
    rm -f "$overview_file"
  fi
  if [[ -n "$doctor_file" ]]; then
    rm -f "$doctor_file"
  fi
  if [[ -n "$console_file" ]]; then
    rm -f "$console_file"
  fi
  if [[ -n "$console_assets_file" ]]; then
    rm -f "$console_assets_file"
  fi
  if [[ "$KEEP_LOCAL_PROD" != "1" ]]; then
    docker compose -p "$COMPOSE_PROJECT" -f "$COMPOSE_FILE" down -v >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker not found in PATH." >&2
  exit 1
fi

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "ERROR: python3 not found in PATH." >&2
  exit 1
fi

if [[ ! -d "${LARK_CLI_HOME:-${HOME}/.lark-cli}" ]]; then
  echo "WARN: LARK_CLI_HOME not found; container Feishu CLI identity will likely be unavailable." >&2
fi

docker compose -p "$COMPOSE_PROJECT" -f "$COMPOSE_FILE" config --quiet
docker compose -p "$COMPOSE_PROJECT" -f "$COMPOSE_FILE" up -d --build

for _ in {1..60}; do
  if curl -fsS "$API_URL/health" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

curl -fsS "$API_URL/health" >/dev/null
docker compose -p "$COMPOSE_PROJECT" -f "$COMPOSE_FILE" exec -T api alembic current
docker compose -p "$COMPOSE_PROJECT" -f "$COMPOSE_FILE" exec -T api lark-cli --version

if [[ -n "${V5_FEISHU_APP_SECRET:-}" ]]; then
  docker compose -p "$COMPOSE_PROJECT" -f "$COMPOSE_FILE" exec -T \
    -e V5_COMPANY_NAME="${V5_COMPANY_NAME:-固势}" \
    -e V5_COMPANY_CODE="${V5_COMPANY_CODE:-gaustek}" \
    -e V5_FEISHU_APP_NAME="${V5_FEISHU_APP_NAME:-固势大飞哥}" \
    -e V5_FEISHU_APP_ID \
    -e V5_FEISHU_APP_SECRET \
    -e V5_FEISHU_APP_CONFIG_ID="${V5_FEISHU_APP_CONFIG_ID:-}" \
    -e V5_FEISHU_CLI_PROFILE="${V5_FEISHU_CLI_PROFILE:-v5-local-prod}" \
    -e V5_BOT_ADMIN_OPEN_ID="${V5_BOT_ADMIN_OPEN_ID:-}" \
    -e V5_BOT_ADMIN_NAME="${V5_BOT_ADMIN_NAME:-}" \
    api python scripts/bootstrap_feishu_app_config_v5.py >/dev/null
fi

overview_file="$(mktemp)"
doctor_file="$(mktemp)"
console_file="$(mktemp)"
console_assets_file="$(mktemp)"
curl -fsS "$API_URL/api/v5/os/overview" >"$overview_file"
curl -fsS "$API_URL/console" >"$console_file"
doctor_status=0
if ! docker compose -p "$COMPOSE_PROJECT" -f "$COMPOSE_FILE" exec -T api lark-cli doctor --offline >"$doctor_file"; then
  doctor_status=$?
fi

"$PYTHON_BIN" - "$overview_file" "$doctor_file" "$console_file" "$console_assets_file" "$doctor_status" "$ALLOW_DEGRADED_FEISHU_CLI" "$API_URL" <<'PY'
import json
import re
import sys
from urllib.parse import urljoin
from urllib.request import urlopen
from pathlib import Path

overview = json.loads(Path(sys.argv[1]).read_text())
doctor = json.loads(Path(sys.argv[2]).read_text())
console_html = Path(sys.argv[3]).read_text()
asset_manifest = Path(sys.argv[4])
doctor_status = int(sys.argv[5])
allow_degraded = sys.argv[6] == "1"
api_url = sys.argv[7].rstrip("/") + "/"

readiness = overview.get("release_readiness") or {}
status = readiness.get("status")
blockers = readiness.get("blockers") or []
checks = {item.get("key"): item for item in readiness.get("checks") or [] if isinstance(item, dict)}
cli_check = checks.get("feishu_cli") or {}

if overview.get("status", {}).get("database") != "ok":
    raise SystemExit("ERROR: database is not ok in /api/v5/os/overview")
if "feishu_boundary" not in checks:
    raise SystemExit("ERROR: feishu_boundary readiness check is missing")
if "feishu_cli" not in checks:
    raise SystemExit("ERROR: feishu_cli readiness check is missing")

required_html = ("数字参谋驾驶舱", "老司机", "企业数字参谋", "/console/assets/styles.css")
missing_html = [item for item in required_html if item not in console_html]
if missing_html:
    raise SystemExit(f"ERROR: /console missing required markers: {missing_html}")

script_match = re.search(r'<script src="([^"]*app\.js[^"]*)"', console_html)
style_match = re.search(r'<link rel="stylesheet" href="([^"]*styles\.css[^"]*)"', console_html)
if not script_match or not style_match:
    raise SystemExit("ERROR: /console missing app.js or styles.css asset links")

asset_manifest.write_text(
    json.dumps({"script": script_match.group(1), "style": style_match.group(1)}, ensure_ascii=False),
    encoding="utf-8",
)

for asset_path, markers in (
    (script_match.group(1), ("const state =", "loadOperatingCenter", "release_readiness")),
    (style_match.group(1), (".operating-center", ".topbar", "overflow-wrap")),
):
    asset_url = urljoin(api_url, asset_path.lstrip("/"))
    with urlopen(asset_url, timeout=10) as response:
        body = response.read().decode("utf-8")
    missing = [marker for marker in markers if marker not in body]
    if missing:
        raise SystemExit(f"ERROR: console asset {asset_path} missing markers: {missing}")

doctor_ok = bool(doctor.get("ok")) and doctor_status == 0
if doctor_ok and status != "ready":
    if "feishu_app_config_missing" in blockers:
        raise SystemExit(
            "ERROR: lark-cli doctor is ok but FeishuAppConfig is missing. "
            "Set V5_FEISHU_APP_SECRET before running local-prod check; "
            "V5_FEISHU_APP_ID can be inferred from LARK_CLI_HOME/config.json when there is one app profile."
        )
    raise SystemExit(f"ERROR: lark-cli doctor is ok but release readiness is {status}: {blockers}")

if not doctor_ok:
    expected = "feishu_cli_identity_unavailable"
    if expected not in blockers or cli_check.get("blocker") != expected:
        raise SystemExit(
            "ERROR: container lark-cli doctor failed, but release readiness did not expose "
            f"{expected}: blockers={blockers}, cli_check={cli_check}"
        )
    message = "WARN: container Feishu CLI identity is unavailable; local-prod is not launch-ready."
    if allow_degraded:
        print(message)
        print("WARN: ALLOW_DEGRADED_FEISHU_CLI=1 set, treating this as an expected rehearsal blocker.")
        raise SystemExit(0)
    raise SystemExit(message)

print("OK: local-prod Docker rehearsal is launch-ready.")
PY
