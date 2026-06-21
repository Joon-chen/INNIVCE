from app.services.llm.approval_advisor import parse_approval_llm_advice


def test_parse_approval_llm_advice_accepts_json_object() -> None:
    advice = parse_approval_llm_advice(
        """
        ```json
        {
          "conclusion": "谨慎通过",
          "concise_reason": "附件和表单基本一致，需确认费用归属。",
          "detailed_reason": "主要依据是金额和附件摘要一致；风险点是项目归属需要确认；二次确认问题是票据抬头是否对应公司。"
        }
        ```
        """
    )

    assert advice == {
        "conclusion": "谨慎通过",
        "concise_reason": "附件和表单基本一致，需确认费用归属。",
        "detailed_reason": "主要依据是金额和附件摘要一致；风险点是项目归属需要确认；二次确认问题是票据抬头是否对应公司。",
        "source": "llm",
    }


def test_parse_approval_llm_advice_rejects_invalid_conclusion() -> None:
    advice = parse_approval_llm_advice(
        '{"conclusion":"建议通过","concise_reason":"x","detailed_reason":"y"}'
    )

    assert advice is None
