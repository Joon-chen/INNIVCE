from datetime import UTC, datetime, timedelta
from typing import Any

from app.models.entities import FeishuAppConfig
from app.services.feishu.client import FeishuClient


class FeishuMeetingService:
    """Native Feishu VC meeting capability for V5 schedule resources."""

    def __init__(self, app_config: FeishuAppConfig, *, client: FeishuClient | None = None) -> None:
        self.app_config = app_config
        self.client = client or FeishuClient(app_config)

    async def list_meetings(
        self,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        meeting_status: int = 2,
        page_size: int = 20,
        page_token: str | None = None,
        user_id_type: str = "open_id",
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        start = start_time or (now - timedelta(days=7))
        end = end_time or now
        params: dict[str, Any] = {
            "page_size": min(max(page_size, 1), 100),
            "start_time": str(_to_epoch_seconds(start)),
            "end_time": str(_to_epoch_seconds(end)),
            "meeting_status": meeting_status,
            "user_id_type": user_id_type,
        }
        if page_token:
            params["page_token"] = page_token
        return await self.client.api_get("/open-apis/vc/v1/meeting_list", params=params)


def _to_epoch_seconds(value: datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return int(value.timestamp())
