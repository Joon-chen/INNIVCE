from types import SimpleNamespace

from app.services.feishu import command_parser


def test_extract_command_text_from_json_message_content() -> None:
    payload = {"event": {"message": {"content": '{"text":"今日日报"}'}}}

    assert command_parser.extract_command_text(payload) == "今日日报"


def test_normalize_command_aliases_and_intents() -> None:
    assert command_parser.normalize_command("/日报") == "今日日报"
    assert command_parser.normalize_command("帮我查下未审批的单子") == "最近审批"
    assert command_parser.normalize_command("你建议我同意还是拒绝") == "审批建议"
    assert command_parser.normalize_command("帮我审批通过") == "审批通过请求"
    assert command_parser.normalize_command("固势的总经理是谁") == "管理人员查询"
    assert command_parser.normalize_command("以后回答简洁点") == "以后回答简洁点"


def test_normalize_command_uses_supplied_approval_context_state() -> None:
    assert (
        command_parser.normalize_command_with_context(
            "展开一下",
            "展开一下",
            has_approval_context=True,
        )
        == "审批详情请求"
    )
    assert (
        command_parser.normalize_command_with_context(
            "展开一下",
            "展开一下",
            has_approval_context=False,
        )
        == "展开一下"
    )


def test_group_message_requires_bot_mention_to_reply() -> None:
    app_config = SimpleNamespace(app_id="cli_bot", name="大飞哥")
    group_payload = {"event": {"message": {"chat_id": "oc_group", "chat_type": "group"}}}
    mentioned_payload = {
        "event": {
            "message": {
                "chat_id": "oc_group",
                "chat_type": "group",
                "mentions": [{"name": "大飞哥"}],
            }
        }
    }
    private_payload = {"event": {"message": {"chat_id": "ou_direct", "chat_type": "p2p"}}}

    assert command_parser.should_reply_to_message(app_config, group_payload) is False
    assert command_parser.should_reply_to_message(app_config, mentioned_payload) is True
    assert command_parser.should_reply_to_message(app_config, private_payload) is True


def test_get_sender_open_id_falls_back_to_approval_actor_value() -> None:
    payload = {
        "event": {
            "action": {
                "value": {
                    "kind": "approval_action",
                    "action": "detail",
                    "index": 1,
                    "actor_open_id": "ou_owner",
                }
            }
        }
    }

    assert command_parser.get_sender_open_id(payload) == "ou_owner"
