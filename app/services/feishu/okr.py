from typing import Any
from urllib.parse import quote

from app.models.entities import FeishuAppConfig
from app.services.feishu.client import FeishuClient


class FeishuOkrService:
    """Native Feishu OKR read capability for V5 management resources."""

    def __init__(self, app_config: FeishuAppConfig, *, client: FeishuClient | None = None) -> None:
        self.app_config = app_config
        self.client = client or FeishuClient(app_config)

    async def list_cycles(
        self,
        *,
        user_id: str,
        user_id_type: str = "open_id",
        page_size: int = 100,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "user_id": user_id,
            "user_id_type": user_id_type or "open_id",
            "page_size": min(max(page_size, 1), 100),
        }
        if page_token:
            params["page_token"] = page_token
        return await self.client.api_get("/open-apis/okr/v2/cycles", params=params)

    async def list_objectives(
        self,
        *,
        cycle_id: str,
        user_id_type: str = "open_id",
        department_id_type: str = "open_department_id",
        page_size: int = 100,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "user_id_type": user_id_type or "open_id",
            "department_id_type": department_id_type or "open_department_id",
            "page_size": min(max(page_size, 1), 100),
        }
        if page_token:
            params["page_token"] = page_token
        return await self.client.api_get(f"/open-apis/okr/v2/cycles/{quote(cycle_id, safe='')}/objectives", params=params)
