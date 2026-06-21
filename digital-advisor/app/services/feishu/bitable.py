from typing import Any
from urllib.parse import quote

from app.models.entities import FeishuAppConfig
from app.services.feishu.client import FeishuClient


class FeishuBitableService:
    """Native Feishu Bitable capability for V5 resource indexing."""

    def __init__(self, app_config: FeishuAppConfig, *, client: FeishuClient | None = None) -> None:
        self.app_config = app_config
        self.client = client or FeishuClient(app_config)

    async def list_tables(
        self,
        *,
        app_token: str,
        page_size: int = 100,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"page_size": min(max(page_size, 1), 100)}
        if page_token:
            params["page_token"] = page_token
        return await self.client.api_get(
            f"/open-apis/bitable/v1/apps/{quote(app_token, safe='')}/tables",
            params=params,
        )

    async def list_records(
        self,
        *,
        app_token: str,
        table_id: str,
        page_size: int = 100,
        page_token: str | None = None,
        view_id: str | None = None,
        field_names: list[str] | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"page_size": min(max(page_size, 1), 500)}
        if page_token:
            params["page_token"] = page_token
        if view_id:
            params["view_id"] = view_id
        if field_names:
            params["field_names"] = ",".join(field_names)
        return await self.client.api_get(
            f"/open-apis/bitable/v1/apps/{quote(app_token, safe='')}/tables/{quote(table_id, safe='')}/records",
            params=params,
        )

    async def list_fields(
        self,
        *,
        app_token: str,
        table_id: str,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        params = {
            "limit": min(max(limit, 1), 200),
            "offset": max(offset, 0),
        }
        return await self.client.api_get(
            f"/open-apis/base/v3/bases/{quote(app_token, safe='')}/tables/{quote(table_id, safe='')}/fields",
            params=params,
        )

    async def create_table(
        self,
        *,
        app_token: str,
        name: str,
        fields: list[dict[str, Any]] | None = None,
        view: dict[str, Any] | list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"name": name}
        if fields:
            body["fields"] = fields
        if view:
            body["view"] = view
        return await self.client.api_post(
            f"/open-apis/base/v3/bases/{quote(app_token, safe='')}/tables",
            body,
        )

    async def update_table(
        self,
        *,
        app_token: str,
        table_id: str,
        name: str,
    ) -> dict[str, Any]:
        return await self.client.api_patch(
            f"/open-apis/base/v3/bases/{quote(app_token, safe='')}/tables/{quote(table_id, safe='')}",
            {"name": name},
        )

    async def delete_table(
        self,
        *,
        app_token: str,
        table_id: str,
    ) -> dict[str, Any]:
        return await self.client.api_delete(
            f"/open-apis/base/v3/bases/{quote(app_token, safe='')}/tables/{quote(table_id, safe='')}",
        )

    async def create_field(
        self,
        *,
        app_token: str,
        table_id: str,
        field: dict[str, Any],
    ) -> dict[str, Any]:
        return await self.client.api_post(
            f"/open-apis/base/v3/bases/{quote(app_token, safe='')}/tables/{quote(table_id, safe='')}/fields",
            field,
        )

    async def delete_field(
        self,
        *,
        app_token: str,
        table_id: str,
        field_id: str,
    ) -> dict[str, Any]:
        return await self.client.api_delete(
            f"/open-apis/base/v3/bases/{quote(app_token, safe='')}/tables/{quote(table_id, safe='')}/fields/{quote(field_id, safe='')}",
        )

    async def update_field(
        self,
        *,
        app_token: str,
        table_id: str,
        field_id: str,
        field: dict[str, Any],
    ) -> dict[str, Any]:
        return await self.client.api_put(
            f"/open-apis/base/v3/bases/{quote(app_token, safe='')}/tables/{quote(table_id, safe='')}/fields/{quote(field_id, safe='')}",
            field,
        )

    async def create_view(
        self,
        *,
        app_token: str,
        table_id: str,
        view: dict[str, Any],
    ) -> dict[str, Any]:
        return await self.client.api_post(
            f"/open-apis/base/v3/bases/{quote(app_token, safe='')}/tables/{quote(table_id, safe='')}/views",
            view,
        )

    async def delete_view(
        self,
        *,
        app_token: str,
        table_id: str,
        view_id: str,
    ) -> dict[str, Any]:
        return await self.client.api_delete(
            f"/open-apis/base/v3/bases/{quote(app_token, safe='')}/tables/{quote(table_id, safe='')}/views/{quote(view_id, safe='')}",
        )

    async def rename_view(
        self,
        *,
        app_token: str,
        table_id: str,
        view_id: str,
        name: str,
    ) -> dict[str, Any]:
        return await self.client.api_patch(
            f"/open-apis/base/v3/bases/{quote(app_token, safe='')}/tables/{quote(table_id, safe='')}/views/{quote(view_id, safe='')}",
            {"name": name},
        )

    async def set_view_filter(
        self,
        *,
        app_token: str,
        table_id: str,
        view_id: str,
        filter_config: dict[str, Any],
    ) -> dict[str, Any]:
        return await self.client.api_put(
            f"/open-apis/base/v3/bases/{quote(app_token, safe='')}/tables/{quote(table_id, safe='')}/views/{quote(view_id, safe='')}/filter",
            filter_config,
        )

    async def set_view_sort(
        self,
        *,
        app_token: str,
        table_id: str,
        view_id: str,
        sort_config: dict[str, Any],
    ) -> dict[str, Any]:
        return await self.client.api_put(
            f"/open-apis/base/v3/bases/{quote(app_token, safe='')}/tables/{quote(table_id, safe='')}/views/{quote(view_id, safe='')}/sort",
            sort_config,
        )

    async def set_view_group(
        self,
        *,
        app_token: str,
        table_id: str,
        view_id: str,
        group_config: dict[str, Any],
    ) -> dict[str, Any]:
        return await self.client.api_put(
            f"/open-apis/base/v3/bases/{quote(app_token, safe='')}/tables/{quote(table_id, safe='')}/views/{quote(view_id, safe='')}/group",
            group_config,
        )

    async def get_view_visible_fields(
        self,
        *,
        app_token: str,
        table_id: str,
        view_id: str,
    ) -> dict[str, Any]:
        return await self.client.api_get(
            f"/open-apis/base/v3/bases/{quote(app_token, safe='')}/tables/{quote(table_id, safe='')}/views/{quote(view_id, safe='')}/visible_fields",
        )

    async def set_view_visible_fields(
        self,
        *,
        app_token: str,
        table_id: str,
        view_id: str,
        visible_fields: list[str],
    ) -> dict[str, Any]:
        return await self.client.api_put(
            f"/open-apis/base/v3/bases/{quote(app_token, safe='')}/tables/{quote(table_id, safe='')}/views/{quote(view_id, safe='')}/visible_fields",
            {"visible_fields": visible_fields},
        )

    async def get_view_card(
        self,
        *,
        app_token: str,
        table_id: str,
        view_id: str,
    ) -> dict[str, Any]:
        return await self.client.api_get(
            f"/open-apis/base/v3/bases/{quote(app_token, safe='')}/tables/{quote(table_id, safe='')}/views/{quote(view_id, safe='')}/card",
        )

    async def set_view_card(
        self,
        *,
        app_token: str,
        table_id: str,
        view_id: str,
        card: dict[str, Any],
    ) -> dict[str, Any]:
        return await self.client.api_put(
            f"/open-apis/base/v3/bases/{quote(app_token, safe='')}/tables/{quote(table_id, safe='')}/views/{quote(view_id, safe='')}/card",
            card,
        )

    async def get_view_timebar(
        self,
        *,
        app_token: str,
        table_id: str,
        view_id: str,
    ) -> dict[str, Any]:
        return await self.client.api_get(
            f"/open-apis/base/v3/bases/{quote(app_token, safe='')}/tables/{quote(table_id, safe='')}/views/{quote(view_id, safe='')}/timebar",
        )

    async def set_view_timebar(
        self,
        *,
        app_token: str,
        table_id: str,
        view_id: str,
        timebar: dict[str, str],
    ) -> dict[str, Any]:
        return await self.client.api_put(
            f"/open-apis/base/v3/bases/{quote(app_token, safe='')}/tables/{quote(table_id, safe='')}/views/{quote(view_id, safe='')}/timebar",
            timebar,
        )

    async def create_record(
        self,
        *,
        app_token: str,
        table_id: str,
        fields: dict[str, Any],
        user_id_type: str = "open_id",
        client_token: str | None = None,
        ignore_consistency_check: bool | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"user_id_type": user_id_type or "open_id"}
        if client_token:
            params["client_token"] = client_token
        if ignore_consistency_check is not None:
            params["ignore_consistency_check"] = ignore_consistency_check
        query = "&".join(f"{quote(str(key), safe='')}={quote(str(value).lower() if isinstance(value, bool) else str(value), safe='')}" for key, value in params.items())
        return await self.client.api_post(
            f"/open-apis/bitable/v1/apps/{quote(app_token, safe='')}/tables/{quote(table_id, safe='')}/records?{query}",
            {"fields": fields},
        )

    async def batch_create_records(
        self,
        *,
        app_token: str,
        table_id: str,
        fields: list[str],
        rows: list[list[Any]],
    ) -> dict[str, Any]:
        return await self.client.api_post(
            f"/open-apis/base/v3/bases/{quote(app_token, safe='')}/tables/{quote(table_id, safe='')}/records/batch_create",
            {"fields": fields, "rows": rows},
        )

    async def append_record_attachments(
        self,
        *,
        app_token: str,
        table_id: str,
        record_id: str,
        field_id: str,
        file_tokens: list[str],
    ) -> dict[str, Any]:
        return await self.client.api_post(
            f"/open-apis/base/v3/bases/{quote(app_token, safe='')}/tables/{quote(table_id, safe='')}/append_attachments",
            {
                "attachments": {
                    record_id: {
                        field_id: [{"file_token": token} for token in file_tokens],
                    }
                }
            },
        )

    async def remove_record_attachments(
        self,
        *,
        app_token: str,
        table_id: str,
        record_id: str,
        field_id: str,
        file_tokens: list[str],
    ) -> dict[str, Any]:
        return await self.client.api_post(
            f"/open-apis/base/v3/bases/{quote(app_token, safe='')}/tables/{quote(table_id, safe='')}/remove_attachments",
            {
                "attachments": {
                    record_id: {
                        field_id: [{"file_token": token} for token in file_tokens],
                    }
                }
            },
        )

    async def batch_update_records(
        self,
        *,
        app_token: str,
        table_id: str,
        record_id_list: list[str],
        patch: dict[str, Any],
    ) -> dict[str, Any]:
        return await self.client.api_post(
            f"/open-apis/base/v3/bases/{quote(app_token, safe='')}/tables/{quote(table_id, safe='')}/records/batch_update",
            {"record_id_list": record_id_list, "patch": patch},
        )

    async def batch_delete_records(
        self,
        *,
        app_token: str,
        table_id: str,
        record_id_list: list[str],
    ) -> dict[str, Any]:
        return await self.client.api_post(
            f"/open-apis/base/v3/bases/{quote(app_token, safe='')}/tables/{quote(table_id, safe='')}/records/batch_delete",
            {"record_id_list": record_id_list},
        )

    async def upsert_record(
        self,
        *,
        app_token: str,
        table_id: str,
        fields: dict[str, Any],
        record_id: str | None = None,
    ) -> dict[str, Any]:
        path = f"/open-apis/base/v3/bases/{quote(app_token, safe='')}/tables/{quote(table_id, safe='')}/records"
        if record_id:
            return await self.client.api_patch(
                f"{path}/{quote(record_id, safe='')}",
                fields,
            )
        return await self.client.api_post(path, fields)

    async def update_record(
        self,
        *,
        app_token: str,
        table_id: str,
        record_id: str,
        fields: dict[str, Any],
        user_id_type: str = "open_id",
        ignore_consistency_check: bool | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"user_id_type": user_id_type or "open_id"}
        if ignore_consistency_check is not None:
            params["ignore_consistency_check"] = ignore_consistency_check
        query = "&".join(
            f"{quote(str(key), safe='')}={quote(str(value).lower() if isinstance(value, bool) else str(value), safe='')}"
            for key, value in params.items()
        )
        return await self.client.api_put(
            f"/open-apis/bitable/v1/apps/{quote(app_token, safe='')}/tables/{quote(table_id, safe='')}/records/{quote(record_id, safe='')}?{query}",
            {"fields": fields},
        )

    async def delete_record(
        self,
        *,
        app_token: str,
        table_id: str,
        record_id: str,
        user_id_type: str = "open_id",
    ) -> dict[str, Any]:
        query = f"user_id_type={quote(str(user_id_type or 'open_id'), safe='')}"
        return await self.client.api_delete(
            f"/open-apis/bitable/v1/apps/{quote(app_token, safe='')}/tables/{quote(table_id, safe='')}/records/{quote(record_id, safe='')}?{query}",
        )
