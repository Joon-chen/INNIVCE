from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

from app.models.entities import FeishuAppConfig
from app.services.feishu.client import FeishuClient


class FeishuCalendarService:
    """Native Feishu Calendar capability for V5 schedule resources."""

    def __init__(self, app_config: FeishuAppConfig, *, client: FeishuClient | None = None) -> None:
        self.app_config = app_config
        self.client = client or FeishuClient(app_config)

    async def list_primary_events(
        self,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        page_size: int = 50,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        start = start_time or now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = end_time or (now + timedelta(days=7))
        params: dict[str, Any] = {
            "page_size": max(50, min(page_size, 100)),
            "start_time": str(_to_epoch_seconds(start)),
            "end_time": str(_to_epoch_seconds(end)),
        }
        if page_token:
            params["page_token"] = page_token
        return await self.client.api_get("/open-apis/calendar/v4/calendars/primary/events", params=params)

    async def create_event(
        self,
        *,
        calendar_id: str,
        summary: str,
        start_time: dict[str, Any],
        end_time: dict[str, Any],
        description: str | None = None,
        recurrence: str | None = None,
        attendee_ids: list[str] | None = None,
        attendees: list[dict[str, Any]] | None = None,
        user_id_type: str = "open_id",
        need_notification: bool = True,
        user_access_token: str | None = None,
    ) -> dict[str, Any]:
        calendar = quote(calendar_id or "primary", safe="")
        payload: dict[str, Any] = {
            "attendee_ability": "can_modify_event",
            "description": description or "",
            "end_time": end_time,
            "free_busy_status": "busy",
            "reminders": [{"minutes": 5}],
            "start_time": start_time,
            "summary": summary,
            "vchat": {"vc_type": "vc"},
        }
        if recurrence:
            payload["recurrence"] = recurrence
        path = f"/open-apis/calendar/v4/calendars/{calendar}/events"
        if user_access_token:
            created = await self.client.api_post_user(path, user_access_token=user_access_token, payload=payload)
        else:
            created = await self.client.api_post(path, payload)
        invitees = _attendees(attendee_ids, attendees)
        if not invitees:
            return created

        event_id = _created_event_id(created)
        if not event_id:
            raise RuntimeError("Feishu calendar event create response missing event_id; cannot add attendees.")
        event = quote(event_id, safe="")
        attendee_path = f"/open-apis/calendar/v4/calendars/{calendar}/events/{event}/attendees?user_id_type={user_id_type or 'open_id'}"
        attendee_payload = {"attendees": invitees, "need_notification": need_notification}
        try:
            if user_access_token:
                await self.client.api_post_user(attendee_path, user_access_token=user_access_token, payload=attendee_payload)
            else:
                await self.client.api_post(attendee_path, attendee_payload)
        except Exception:
            try:
                await self.client.api_delete(f"/open-apis/calendar/v4/calendars/{calendar}/events/{event}")
            except Exception:
                pass
            raise
        return created


def _to_epoch_seconds(value: datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return int(value.timestamp())


def _attendees(attendee_ids: list[str] | None, attendees: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    items = [dict(item) for item in attendees or [] if isinstance(item, dict)]
    for attendee_id in attendee_ids or []:
        if attendee_id.startswith("ou_"):
            items.append({"type": "user", "user_id": attendee_id})
        elif attendee_id.startswith("oc_"):
            items.append({"type": "chat", "chat_id": attendee_id})
        elif attendee_id.startswith("omm_"):
            items.append({"type": "resource", "room_id": attendee_id})
    return items


def _created_event_id(payload: dict[str, Any]) -> str | None:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return None
    event = data.get("event")
    if isinstance(event, dict) and event.get("event_id"):
        return str(event["event_id"])
    if data.get("event_id"):
        return str(data["event_id"])
    return None
