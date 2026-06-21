from app.services.feishu.authorization_card_entrypoint import authorization_actions_from_trace
from app.services.feishu.authorization_cards import build_user_identity_authorization_card


def test_build_user_identity_authorization_card_uses_action_urls() -> None:
    card = build_user_identity_authorization_card(
        answer="范围：本人相关｜邮件问答\n这个能力需要本人授权。\n授权入口：\n- 不应重复到卡片正文",
        actions=[
            {
                "resource_type": "user_identity_bundle",
                "label": "授权个人能力包",
                "channel": "feishu_oauth",
                "url": "http://127.0.0.1:8000/api/user-identity/oauth/feishu/start?company_id=c1&open_id=ou_1",
                "covered_resources": ["personal_feishu", "external_mail", "personal_dingtalk", "personal_wechat"],
            },
        ],
        actor_open_id="ou_1",
    )

    assert card["header"]["title"]["content"] == "需要本人授权"
    assert "不应重复到卡片正文" not in card["elements"][0]["text"]["content"]
    actions = card["elements"][1]["actions"]
    assert actions[0]["url"].endswith("/api/user-identity/oauth/feishu/start?company_id=c1&open_id=ou_1")
    assert actions[0]["value"]["kind"] == "user_identity_authorization"
    assert actions[0]["value"]["actor_open_id"] == "ou_1"
    assert actions[0]["value"]["resource_type"] == "user_identity_bundle"


def test_build_user_identity_authorization_card_warns_for_local_only_url() -> None:
    card = build_user_identity_authorization_card(
        answer="这个能力需要本人授权。",
        actions=[
            {
                "resource_type": "user_identity_bundle",
                "label": "授权个人能力包",
                "channel": "feishu_oauth",
                "url": "http://127.0.0.1:8000/api/user-identity/oauth/feishu/start?company_id=c1&open_id=ou_1",
                "local_only": True,
            },
        ],
        actor_open_id="ou_1",
    )

    note = card["elements"][-1]["elements"][0]["content"]
    assert "本地地址" in note
    assert "公网 HTTPS API_BASE_URL" in note
    assert "CLI 二维码仅作为本地调试备用" in note


def test_authorization_actions_from_trace_reads_tool_structured_result() -> None:
    trace = {
        "steps": [
            {"kind": "semantic", "metadata": {}},
            {
                "kind": "tool",
                "metadata": {
                    "structured_result": {
                        "authorization_actions": [
                            {
                                "resource_type": "user_identity_bundle",
                                "label": "授权个人能力包",
                                "url": "http://127.0.0.1:8000/api/user-identity/oauth/feishu/start",
                            }
                        ]
                    }
                },
            },
        ]
    }

    assert authorization_actions_from_trace(trace)[0]["resource_type"] == "user_identity_bundle"
