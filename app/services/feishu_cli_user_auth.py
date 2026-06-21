import base64
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from app.services.feishu.cli_profile import feishu_app_cli_profile


DEFAULT_USER_AUTH_DOMAINS = "approval,attendance,base,calendar,contact,docs,drive,event,im,mail,minutes,okr,task,vc,wiki"


def start_lark_cli_user_authorization(app_config: Any, *, domains: str | None = None) -> dict[str, Any]:
    profile = feishu_app_cli_profile(app_config)
    payload = _run_lark_cli_json(
        [
            *_profile_args(profile),
            "auth",
            "login",
            "--domain",
            _normalized_domains(domains),
            "--no-wait",
            "--json",
        ],
        action="lark-cli auth login",
        timeout=30,
    )
    verification_url = str(
        payload.get("verification_url") or payload.get("verification_uri_complete") or payload.get("verification_uri") or ""
    ).strip()
    device_code = str(payload.get("device_code") or "").strip()
    if not verification_url or not device_code:
        raise HTTPException(status_code=502, detail={"error": "lark_cli_auth_start_missing_fields", "payload": payload})
    return {
        "status": "authorization_started",
        "provider": "feishu_cli",
        "cli_profile": profile,
        "domains": _normalized_domains(domains),
        "verification_url": verification_url,
        "device_code": device_code,
        "qr_code_data_url": _qr_code_data_url(verification_url),
        "instruction": "请用资源所有者本人账号完成授权；完成后回到后台点击“完成 CLI 授权”。",
    }


def complete_lark_cli_user_authorization(
    app_config: Any,
    *,
    device_code: str,
    expected_owner_open_id: str | None = None,
) -> dict[str, Any]:
    code = str(device_code or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="device_code is required")
    profile = feishu_app_cli_profile(app_config)
    payload = _run_lark_cli_json(
        [*_profile_args(profile), "auth", "login", "--device-code", code, "--json"],
        action="lark-cli auth login --device-code",
        timeout=180,
    )
    identity_open_id = _identity_open_id(payload)
    identity_match = _identity_match(identity_open_id, expected_owner_open_id)
    if identity_match is False:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "feishu_cli_user_identity_mismatch",
                "expected_owner_open_id": expected_owner_open_id,
                "authorized_open_id": identity_open_id,
                "message": "CLI 授权账号与当前资源所有者 open_id 不一致，不能标记为已授权。",
            },
        )
    return {
        "status": "authorized",
        "provider": "feishu_cli",
        "cli_profile": profile,
        "identity": payload.get("identity"),
        "identity_open_id": identity_open_id,
        "identity_match": identity_match,
        "message": payload.get("message") or "Feishu CLI user authorization completed.",
    }


def _profile_args(profile: str | None) -> list[str]:
    return ["--profile", profile] if profile else []


def _normalized_domains(domains: str | None) -> str:
    text = str(domains or "").strip()
    return text or DEFAULT_USER_AUTH_DOMAINS


def _run_lark_cli_json(args: list[str], *, action: str, timeout: int) -> dict[str, Any]:
    path = shutil.which("lark-cli")
    if not path:
        raise HTTPException(status_code=500, detail="lark-cli not found in PATH")
    completed = subprocess.run(
        [path, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env=os.environ.copy(),
    )
    if completed.returncode != 0:
        detail = _command_error_detail(action=action, completed=completed)
        raise HTTPException(status_code=502, detail=detail)
    return _json_output(completed.stdout, action=action)


def _qr_code_data_url(verification_url: str) -> str:
    path = shutil.which("lark-cli")
    if not path:
        raise HTTPException(status_code=500, detail="lark-cli not found in PATH")
    tmp_dir = Path("tmp")
    tmp_dir.mkdir(exist_ok=True)
    output_path = tmp_dir / f"user-identity-cli-auth-{int(time.time() * 1000)}.png"
    completed = subprocess.run(
        [path, "auth", "qrcode", verification_url, "--output", str(output_path)],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
        env=os.environ.copy(),
    )
    if completed.returncode != 0:
        detail = _command_error_detail(action="lark-cli auth qrcode", completed=completed)
        raise HTTPException(status_code=502, detail=detail)
    try:
        raw = output_path.read_bytes()
    finally:
        output_path.unlink(missing_ok=True)
    return f"data:image/png;base64,{base64.b64encode(raw).decode()}"


def _json_output(text: str, *, action: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail={"error": f"{action} returned non-json output", "output": text[:1000]}) from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail={"error": f"{action} returned unexpected JSON", "payload": payload})
    return payload


def _identity_open_id(payload: dict[str, Any]) -> str | None:
    candidates: list[Any] = [
        payload.get("open_id"),
        payload.get("user_open_id"),
        payload.get("owner_open_id"),
    ]
    identity = payload.get("identity")
    if isinstance(identity, dict):
        candidates.extend(
            [
                identity.get("open_id"),
                identity.get("user_open_id"),
                identity.get("owner_open_id"),
                identity.get("id"),
            ]
        )
        for key in ("user", "account", "profile"):
            nested = identity.get(key)
            if isinstance(nested, dict):
                candidates.extend([nested.get("open_id"), nested.get("user_open_id"), nested.get("id")])
    for candidate in candidates:
        value = str(candidate or "").strip()
        if value.startswith("ou_"):
            return value
    return None


def _identity_match(identity_open_id: str | None, expected_owner_open_id: str | None) -> bool | None:
    expected = str(expected_owner_open_id or "").strip()
    if not expected or not identity_open_id:
        return None
    return identity_open_id == expected


def _command_error_detail(*, action: str, completed: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    return {
        "error": f"{action} failed",
        "returncode": completed.returncode,
        "stdout": completed.stdout[-1000:],
        "stderr": completed.stderr[-2000:],
    }
