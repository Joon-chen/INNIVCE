import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from urllib.parse import quote, urlencode
from uuid import UUID

import httpx
import redis
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.entities import FeishuAppConfig
from app.schemas.common import WorkEventCreate
from app.services.work_events import upsert_work_event


class FeishuClientMode(StrEnum):
    SDK = "sdk"
    RAW_HTTP = "raw_http"


@dataclass(frozen=True)
class FeishuRouteRule:
    key: str
    mode: FeishuClientMode
    reason: str
    sdk_module: str | None = None
    path_prefixes: tuple[str, ...] = ()


FEISHU_ROUTE_RULES: tuple[FeishuRouteRule, ...] = (
    FeishuRouteRule(
        key="im",
        mode=FeishuClientMode.SDK,
        sdk_module="im.v1",
        path_prefixes=("/open-apis/im/",),
        reason="消息、群组、群成员等 IM 基础能力稳定成熟，优先使用 SDK。",
    ),
    FeishuRouteRule(
        key="contact",
        mode=FeishuClientMode.SDK,
        sdk_module="contact.v3",
        path_prefixes=("/open-apis/contact/",),
        reason="通讯录、部门、用户读取属于稳定基础能力，优先使用 SDK。",
    ),
    FeishuRouteRule(
        key="calendar",
        mode=FeishuClientMode.SDK,
        sdk_module="calendar.v4",
        path_prefixes=("/open-apis/calendar/",),
        reason="日历和日程 API 结构稳定，优先使用 SDK。",
    ),
    FeishuRouteRule(
        key="vc",
        mode=FeishuClientMode.SDK,
        sdk_module="vc.v1",
        path_prefixes=("/open-apis/vc/",),
        reason="视频会议、会议记录等稳定能力优先使用 SDK。",
    ),
    FeishuRouteRule(
        key="auth",
        mode=FeishuClientMode.SDK,
        sdk_module="auth/authen",
        path_prefixes=("/open-apis/auth/", "/open-apis/authen/"),
        reason="SDK 调用会托管 tenant_access_token 生命周期；显式 token 调试接口仍保留 raw 缓存。",
    ),
    FeishuRouteRule(
        key="wiki",
        mode=FeishuClientMode.RAW_HTTP,
        sdk_module="wiki.v2",
        path_prefixes=("/open-apis/wiki/",),
        reason="知识库/Wiki 场景经常涉及资源发现、节点转换和新接口，保留 Raw HTTP 优先。",
    ),
    FeishuRouteRule(
        key="bitable",
        mode=FeishuClientMode.RAW_HTTP,
        sdk_module="bitable.v1",
        path_prefixes=("/open-apis/bitable/",),
        reason="多维表格高级能力字段复杂、接口迭代快，保留 Raw HTTP 优先。",
    ),
    FeishuRouteRule(
        key="approval",
        mode=FeishuClientMode.RAW_HTTP,
        sdk_module="approval.v4",
        path_prefixes=("/open-apis/approval/",),
        reason="审批高级能力、任务查询和实例详情差异较多，保留 Raw HTTP 优先。",
    ),
    FeishuRouteRule(
        key="drive_docs",
        mode=FeishuClientMode.RAW_HTTP,
        sdk_module="drive/docs/docx/sheets",
        path_prefixes=("/open-apis/drive/", "/open-apis/docx/", "/open-apis/sheets/"),
        reason="云文档、文件、表格资源发现和下载上传链路复杂，Raw HTTP 更便于快速覆盖。",
    ),
    FeishuRouteRule(
        key="mail",
        mode=FeishuClientMode.RAW_HTTP,
        sdk_module="mail",
        path_prefixes=("/open-apis/mail/",),
        reason="飞书邮箱有租户/用户 token 差异和历史兼容问题，当前保留 Raw HTTP。",
    ),
)


def route_for_feishu_api(path_or_key: str) -> FeishuRouteRule:
    value = path_or_key.strip()
    lowered = value.lower()
    for rule in FEISHU_ROUTE_RULES:
        if lowered == rule.key or any(value.startswith(prefix) for prefix in rule.path_prefixes):
            return rule
    return FeishuRouteRule(
        key="future_or_unknown",
        mode=FeishuClientMode.RAW_HTTP,
        path_prefixes=(value,),
        reason="未知或未来新接口默认走 Raw HTTP，便于快速接入和调试。",
    )


def route_rule_to_dict(rule: FeishuRouteRule) -> dict[str, Any]:
    return {
        "key": rule.key,
        "mode": rule.mode.value,
        "sdk_module": rule.sdk_module,
        "path_prefixes": list(rule.path_prefixes),
        "reason": rule.reason,
    }


class FeishuSdkLayer:
    def __init__(self, app_config: FeishuAppConfig) -> None:
        self.app_config = app_config
        self._lark = None
        self._client = None
        try:
            import lark_oapi as lark
        except ImportError:
            self.available = False
            self.import_error = "lark-oapi is not installed"
            return
        self._lark = lark
        self.available = True
        self.import_error = None

    @property
    def client(self):
        if not self.available:
            return None
        if self._client is None:
            builder = (
                self._lark.Client.builder()
                .app_id(self.app_config.app_id)
                .app_secret(self.app_config.app_secret)
                .timeout(settings.request_timeout_seconds)
            )
            self._client = builder.build()
        return self._client

    async def send_message(
        self,
        *,
        receive_id_type: str,
        receive_id: str,
        msg_type: str,
        content: dict[str, Any] | str,
    ) -> dict[str, Any]:
        if not self.available or not self.client:
            raise RuntimeError(self.import_error or "Feishu SDK unavailable")

        from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

        body = (
            CreateMessageRequestBody.builder()
            .receive_id(receive_id)
            .msg_type(msg_type)
            .content(content if isinstance(content, str) else json.dumps(content, ensure_ascii=False))
            .build()
        )
        request = (
            CreateMessageRequest.builder()
            .receive_id_type(receive_id_type)
            .request_body(body)
            .build()
        )
        response = await self.client.im.v1.message.acreate(request)
        return self._response_to_dict(response, action="Feishu SDK send message")

    def _response_to_dict(self, response: Any, *, action: str) -> dict[str, Any]:
        try:
            marshalled = self._lark.JSON.marshal(response)
            body = json.loads(marshalled) if isinstance(marshalled, str) else marshalled
        except Exception:
            body = {"raw": str(response)}

        success = getattr(response, "success", None)
        if callable(success) and not success():
            raise HTTPException(status_code=502, detail={"message": f"{action} failed", "body": body})
        code = body.get("code") if isinstance(body, dict) else None
        if code not in (None, 0):
            raise HTTPException(status_code=502, detail={"message": f"{action} failed", "body": body})
        return body if isinstance(body, dict) else {"raw": body}


class FeishuRawHttpLayer:
    def __init__(self, app_config: FeishuAppConfig, redis_client: redis.Redis) -> None:
        self.app_config = app_config
        self.base_url = settings.feishu_base_url.rstrip("/")
        self.redis = redis_client

    @property
    def _cache_key(self) -> str:
        return f"feishu:tenant_access_token:{self.app_config.app_id}"

    async def get_tenant_access_token(self, force_refresh: bool = False) -> str:
        if not force_refresh:
            cached = self.redis.get(self._cache_key)
            if cached:
                return cached

        body = await self.request(
            "POST",
            "/open-apis/auth/v3/tenant_access_token/internal",
            payload={"app_id": self.app_config.app_id, "app_secret": self.app_config.app_secret},
            auth=False,
            error_label="Feishu token",
        )
        token = body["tenant_access_token"]
        expire = max(int(body.get("expire", 7200)) - 300, 60)
        self.redis.setex(self._cache_key, expire, token)
        return token

    async def request(
        self,
        method: Literal["GET", "POST", "PATCH", "DELETE"],
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        auth: bool = True,
        error_label: str = "Feishu API",
    ) -> dict[str, Any]:
        headers = {}
        if auth:
            token = await self.get_tenant_access_token()
            headers["Authorization"] = f"Bearer {token}"
        url = f"{self.base_url}{path}"
        async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
            response = await client.request(
                method,
                url,
                params=params or {},
                json=payload if method in {"POST", "PATCH", "DELETE"} else None,
                headers=headers,
            )
        return self._handle_response(response, path=path, error_label=error_label)

    def _handle_response(self, response: httpx.Response, *, path: str, error_label: str) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError:
            body = {"raw": response.text}
        if response.status_code >= 400:
            raise HTTPException(
                status_code=502,
                detail={
                    "message": f"{error_label} HTTP error",
                    "path": path,
                    "status_code": response.status_code,
                    "body": body,
                },
            )
        if body.get("code") != 0:
            raise HTTPException(
                status_code=502,
                detail={"message": f"{error_label} failed", "path": path, "body": body},
            )
        return body


class FeishuClient:
    def __init__(self, app_config: FeishuAppConfig) -> None:
        self.app_config = app_config
        self.base_url = settings.feishu_base_url.rstrip("/")
        self.redis = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        self.sdk = FeishuSdkLayer(app_config)
        self.raw = FeishuRawHttpLayer(app_config, self.redis)

    @property
    def _cache_key(self) -> str:
        return self.raw._cache_key

    async def get_tenant_access_token(self, force_refresh: bool = False) -> str:
        return await self.raw.get_tenant_access_token(force_refresh=force_refresh)

    def route_for(self, path_or_key: str) -> FeishuRouteRule:
        return route_for_feishu_api(path_or_key)

    def routing_overview(self) -> dict[str, Any]:
        return {
            "sdk_available": self.sdk.available,
            "sdk_import_error": self.sdk.import_error,
            "rules": [route_rule_to_dict(rule) for rule in FEISHU_ROUTE_RULES],
        }

    async def send_message(
        self,
        *,
        receive_id_type: str,
        receive_id: str,
        msg_type: str,
        content: dict[str, Any],
    ) -> dict[str, Any]:
        if self.sdk.available:
            return await self.sdk.send_message(
                receive_id_type=receive_id_type,
                receive_id=receive_id,
                msg_type=msg_type,
                content=content,
            )
        return await self.api_post(
            f"/open-apis/im/v1/messages?receive_id_type={receive_id_type}",
            {
                "receive_id": receive_id,
                "msg_type": msg_type,
                "content": content if isinstance(content, str) else json.dumps(content, ensure_ascii=False),
            },
        )

    async def api_get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self.raw.request("GET", path, params=params, error_label="Feishu API")

    async def api_get_user(
        self,
        path: str,
        *,
        user_access_token: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        headers = {"Authorization": f"Bearer {user_access_token}"}
        async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
            response = await client.get(url, params=params or {}, headers=headers)
        try:
            body = response.json()
        except ValueError:
            body = {"raw": response.text}
        if response.status_code >= 400:
            raise HTTPException(
                status_code=502,
                detail={
                    "message": "Feishu user API HTTP error",
                    "path": path,
                    "status_code": response.status_code,
                    "body": body,
                },
            )
        if body.get("code") != 0:
            raise HTTPException(status_code=502, detail={"message": "Feishu user API failed", "path": path, "body": body})
        return body

    async def api_post_user(
        self,
        path: str,
        *,
        user_access_token: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        headers = {"Authorization": f"Bearer {user_access_token}"}
        async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
            response = await client.post(url, json=payload or {}, headers=headers)
        try:
            body = response.json()
        except ValueError:
            body = {"raw": response.text}
        if response.status_code >= 400:
            raise HTTPException(
                status_code=502,
                detail={
                    "message": "Feishu user API HTTP error",
                    "path": path,
                    "status_code": response.status_code,
                    "body": body,
                },
            )
        if body.get("code") != 0:
            raise HTTPException(status_code=502, detail={"message": "Feishu user API failed", "path": path, "body": body})
        return body

    async def api_post(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self.raw.request("POST", path, payload=payload or {}, error_label="Feishu API")

    async def api_put(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self.raw.request("PUT", path, payload=payload or {}, error_label="Feishu API")

    async def api_patch(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self.raw.request("PATCH", path, payload=payload or {}, error_label="Feishu API")

    async def update_message_content(self, *, message_id: str, content: dict[str, Any] | str) -> dict[str, Any]:
        message = quote(message_id, safe="")
        return await self.api_patch(
            f"/open-apis/im/v1/messages/{message}",
            {"content": content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)},
        )

    async def api_delete(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self.raw.request("DELETE", path, payload=payload or {}, error_label="Feishu API")

    async def download_binary(self, path: str, params: dict[str, Any] | None = None) -> tuple[bytes, str | None]:
        token = await self.get_tenant_access_token()
        url = f"{self.base_url}{path}"
        headers = {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
            response = await client.get(url, params=params or {}, headers=headers, follow_redirects=True)
        if response.status_code >= 400:
            try:
                body: Any = response.json()
            except ValueError:
                body = {"raw": response.text[:500]}
            raise HTTPException(
                status_code=502,
                detail={
                    "message": "Feishu download HTTP error",
                    "path": path,
                    "status_code": response.status_code,
                    "body": body,
                },
            )
        content_type = response.headers.get("content-type")
        return response.content, content_type

    async def list_messages(
        self,
        *,
        container_id_type: str,
        container_id: str,
        start_time: int,
        end_time: int,
        page_size: int = 20,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        token = await self.get_tenant_access_token()
        url = f"{self.base_url}/open-apis/im/v1/messages"
        params: dict[str, Any] = {
            "container_id_type": container_id_type,
            "container_id": container_id,
            "start_time": str(start_time),
            "end_time": str(end_time),
            "page_size": str(page_size),
        }
        if page_token:
            params["page_token"] = page_token
        headers = {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
            response = await client.get(url, params=params, headers=headers)
        try:
            body = response.json()
        except ValueError:
            body = {"raw": response.text}
        if response.status_code >= 400:
            raise HTTPException(
                status_code=502,
                detail={
                    "message": "Feishu list messages HTTP error",
                    "status_code": response.status_code,
                    "body": body,
                },
            )
        if body.get("code") != 0:
            raise HTTPException(
                status_code=502,
                detail={"message": "Feishu list messages failed", "body": body},
            )
        return body

    def build_user_oauth_url(self, *, state: str = "local") -> str:
        params = urlencode(
            {
                "app_id": self.app_config.app_id,
                "redirect_uri": settings.feishu_oauth_redirect_uri,
                "state": state,
            }
        )
        return f"{self.base_url}/open-apis/authen/v1/index?{params}"

    async def exchange_user_access_token(self, *, code: str) -> dict[str, Any]:
        token = await self.get_tenant_access_token()
        headers = {"Authorization": f"Bearer {token}"}
        payload = {"grant_type": "authorization_code", "code": code}
        url = f"{self.base_url}/open-apis/authen/v1/access_token"
        async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
            response = await client.post(url, json=payload, headers=headers)
        try:
            body = response.json()
        except ValueError:
            body = {"raw": response.text}
        if response.status_code >= 400:
            raise HTTPException(
                status_code=502,
                detail={
                    "message": "Feishu OAuth HTTP error",
                    "status_code": response.status_code,
                    "body": body,
                },
            )
        if body.get("code") != 0:
            raise HTTPException(status_code=502, detail={"message": "Feishu OAuth failed", "body": body})
        return body

    async def refresh_user_access_token(self, *, refresh_token: str) -> dict[str, Any]:
        token = await self.get_tenant_access_token()
        headers = {"Authorization": f"Bearer {token}"}
        payload = {"grant_type": "refresh_token", "refresh_token": refresh_token}
        url = f"{self.base_url}/open-apis/authen/v1/refresh_access_token"
        async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
            response = await client.post(url, json=payload, headers=headers)
        try:
            body = response.json()
        except ValueError:
            body = {"raw": response.text}
        if response.status_code >= 400:
            raise HTTPException(
                status_code=502,
                detail={
                    "message": "Feishu OAuth refresh HTTP error",
                    "status_code": response.status_code,
                    "body": body,
                },
            )
        if body.get("code") != 0:
            raise HTTPException(status_code=502, detail={"message": "Feishu OAuth refresh failed", "body": body})
        return body

    async def get_user_info(self, *, user_access_token: str) -> dict[str, Any]:
        url = f"{self.base_url}/open-apis/authen/v1/user_info"
        headers = {"Authorization": f"Bearer {user_access_token}"}
        async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
            response = await client.get(url, headers=headers)
        try:
            body = response.json()
        except ValueError:
            body = {"raw": response.text}
        if response.status_code >= 400:
            raise HTTPException(
                status_code=502,
                detail={
                    "message": "Feishu user info HTTP error",
                    "status_code": response.status_code,
                    "body": body,
                },
            )
        if body.get("code") != 0:
            raise HTTPException(status_code=502, detail={"message": "Feishu user info failed", "body": body})
        return body


def normalize_feishu_event(app_config: FeishuAppConfig, payload: dict[str, Any]) -> WorkEventCreate:
    header = payload.get("header") or {}
    event = payload.get("event") or payload
    event_type = header.get("event_type") or payload.get("type") or "feishu.event"
    event_id = header.get("event_id") or event.get("message_id") or event.get("event_id")
    occurred_at = _parse_feishu_time(header.get("create_time") or event.get("create_time"))
    sender = event.get("sender") or event.get("operator") or {}
    message = event.get("message") or {}
    content_text = _extract_message_text(message) or event.get("text") or str(event)[:3000]
    title = message.get("chat_id") or event_type

    return WorkEventCreate(
        company_id=app_config.company_id,
        source="feishu",
        event_type=event_type,
        external_id=event_id,
        thread_id=message.get("chat_id") or event.get("chat_id"),
        title=title,
        content_text=content_text,
        occurred_at=occurred_at,
        actors=[sender] if sender else [],
        labels=["feishu", event_type],
        payload=payload,
    )


def normalize_feishu_message(
    app_config: FeishuAppConfig,
    message: dict[str, Any],
    *,
    event_type: str = "im.message.history",
) -> WorkEventCreate:
    message_id = message.get("message_id")
    chat_id = message.get("chat_id")
    sender = message.get("sender") or {}
    content_text = _extract_message_text(message) or str(message.get("content") or "")[:3000]
    message_type = message.get("message_type") or message.get("msg_type") or "message"
    title = f"{message_type} {chat_id}".strip()

    return WorkEventCreate(
        company_id=app_config.company_id,
        source="feishu",
        event_type=event_type,
        external_id=message_id,
        thread_id=chat_id,
        title=title,
        content_text=content_text,
        occurred_at=_parse_feishu_time(message.get("create_time") or message.get("update_time")),
        actors=[sender] if sender else [],
        labels=["feishu", event_type, message_type],
        payload={"message": message},
    )


def ingest_feishu_event(db: Session, app_config: FeishuAppConfig, payload: dict[str, Any]):
    event = upsert_work_event(db, normalize_feishu_event(app_config, payload))
    db.commit()
    db.refresh(event)
    return event


def ingest_feishu_message(db: Session, app_config: FeishuAppConfig, message: dict[str, Any]):
    event = upsert_work_event(db, normalize_feishu_message(app_config, message))
    db.flush()
    return event


def verify_feishu_token(app_config: FeishuAppConfig, payload: dict[str, Any]) -> None:
    token = payload.get("token") or (payload.get("header") or {}).get("token")
    expected = app_config.verification_token or settings.feishu_event_token
    if expected and token != expected:
        raise HTTPException(status_code=403, detail="Invalid Feishu verification token")


def _parse_feishu_time(value: Any) -> datetime:
    if value is None:
        return datetime.now(UTC)
    try:
        timestamp = int(value)
        if timestamp > 10_000_000_000:
            timestamp = timestamp / 1000
        return datetime.fromtimestamp(timestamp, tz=UTC)
    except (TypeError, ValueError):
        return datetime.now(UTC)


def _extract_message_text(message: dict[str, Any]) -> str | None:
    content = message.get("content")
    if content is None and isinstance(message.get("body"), dict):
        content = message["body"].get("content")
    if isinstance(content, str):
        stripped = content.strip()
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return stripped
        if isinstance(parsed, dict):
            text = parsed.get("text")
            if isinstance(text, str):
                return text
        return stripped
    if isinstance(content, dict):
        return content.get("text") or str(content)
    return None


def get_feishu_app_or_404(db: Session, app_config_id: UUID) -> FeishuAppConfig:
    app_config = db.get(FeishuAppConfig, app_config_id)
    if not app_config or not app_config.is_active:
        raise HTTPException(status_code=404, detail="Feishu app config not found")
    return app_config
