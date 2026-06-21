from app.services.feishu import approval_cards


def test_approval_card_item_title_includes_amount_applicant_and_reason() -> None:
    item = {
        "approval_name": "Payment Request",
        "initiator_names": ["余莲莲"],
        "instance_detail": {"form": '[{"name":"付款金额","value":3724},{"name":"付款事由","value":"端午节礼品"}]'},
    }

    title = approval_cards.approval_card_item_title(item, 1)

    assert title == "**1. 付款审批**｜3724元｜余莲莲｜端午节礼品"


def test_approval_card_suggestion_returns_conclusion_and_reason() -> None:
    item = {
        "approval_name": "报销审批",
        "instance_detail": {"form": '[{"name":"费用汇总","value":1309.2},{"name":"报销事由","value":"客户现场住宿"}]'},
    }

    suggestion = approval_cards.approval_card_suggestion(item)

    assert suggestion.startswith("**建议：")
    assert "\n理由：" in suggestion


def test_build_approval_action_card_with_default_renderers_can_expand_detail() -> None:
    item = {
        "approval_name": "报销审批",
        "initiator_names": ["王东升"],
        "instance_detail": {"form": '[{"name":"费用汇总","value":1309.2},{"name":"报销事由","value":"客户现场住宿"}]'},
    }

    card = approval_cards.build_approval_action_card(
        [item],
        chat_id="oc_1",
        receive_id_type="chat_id",
        receive_id="oc_1",
        actor_open_id="ou_owner",
        expanded_index=1,
        renderers=approval_cards.approval_action_card_renderers(),
    )

    content = card["elements"][1]["text"]["content"]
    assert "这笔审批的关键信息" in content
    assert card["elements"][2]["actions"][0]["value"]["action"] == "detail"
