from types import SimpleNamespace
from uuid import uuid4

from app.api.routes.portal_request_models import PortalApprovalDetailRequest
from app.services import portal_runtime


def test_portal_approval_detail_payload_merges_live_structured_detail(monkeypatch) -> None:
    app_config_id = uuid4()
    company_id = uuid4()

    monkeypatch.setattr(portal_runtime, "_require_portal_session", lambda **kwargs: None)
    monkeypatch.setattr(
        portal_runtime,
        "get_feishu_app_or_404",
        lambda db, app_config_id: SimpleNamespace(company_id=company_id),
    )

    class _FakeApprovalService:
        def __init__(self, app_config):
            self.app_config = app_config

        async def get_instance(self, *, instance_code: str, user_id_type: str):
            return {
                "data": {
                    "approval_name": "费用报销",
                    "serial_number": "202606150009",
                    "applicant": {"name": "任佳乐"},
                    "form": '[{"name":"费用汇总","value":8902.33},{"name":"报销事由","value":"出差打车"}]',
                }
            }

    monkeypatch.setattr(portal_runtime, "FeishuApprovalService", _FakeApprovalService)

    payload = portal_runtime.portal_approval_detail_payload(
        SimpleNamespace(),
        PortalApprovalDetailRequest(
            app_config_id=app_config_id,
            open_id="ou_1",
            chat_id="oc_1",
            instance_code="instance-1",
            task_id="task-1",
        ),
        None,
    )

    assert payload["available"] is True
    assert payload["item"]["title"] == "费用报销"
    assert payload["item"]["serial_number"] == "202606150009"
    assert payload["item"]["applicant_name"] == "任佳乐"
    assert payload["item"]["amount"] == 8902.33
    assert payload["item"]["instance_code"] == "instance-1"
    assert payload["metadata"]["source"] == "feishu_live_instance_detail"
