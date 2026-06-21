from types import SimpleNamespace

from app.services.ai.advisor import (
    AdvisorContext,
    _extract_keywords,
    _is_contact_question,
    _should_use_deepseek_for_bot_analysis,
    _source_filter,
    answer_advisor_question,
)
from app.services.ai.business_extraction import enrich_extracted_item, extract_business_items
from app.services.ai.openai_service import fallback_daily_report, heuristic_extract_items


def test_extract_keywords_keeps_business_terms() -> None:
    keywords = _extract_keywords("戴总最近关注哪些销售订单风险？")

    assert "戴总" in keywords
    assert "销售订单" in keywords
    assert "风险" in keywords


def test_source_filter_routes_mail_questions() -> None:
    assert _source_filter("最近邮件有什么重点") == {"feishu", "imap", "gmail", "graph"}


def test_source_filter_routes_feishu_questions() -> None:
    assert _source_filter("最近飞书聊了什么") == {"feishu"}


def test_contact_questions_are_detected_for_context_isolation() -> None:
    assert _is_contact_question("帮我把组织架构以XMind形式输出")
    assert _is_contact_question("飞书通讯录里有哪些部门")


def test_member_sensitive_question_is_limited_before_search() -> None:
    answer = answer_advisor_question(
        db=None,
        company_id="company-1",
        question="最近邮件有什么重点",
        scope="chat",
        chat_id="oc_chat",
        actor_role="member",
        actor_access_scope="chat",
    )

    assert "不能直接展开" in answer
    assert "member/chat" in answer


def test_domain_question_outside_allowed_domains_is_limited() -> None:
    answer = answer_advisor_question(
        db=None,
        company_id="company-1",
        question="最近财务付款有什么风险",
        scope="domain",
        chat_id="oc_chat",
        actor_role="manager",
        actor_access_scope="domain",
        actor_domains=["sales"],
    )

    assert "不能直接展开" in answer
    assert "manager/domain" in answer


def test_deepseek_bot_analysis_only_for_company_analysis(monkeypatch) -> None:
    monkeypatch.setattr("app.services.ai.advisor.settings.deepseek_use_for_bot_analysis", True)
    context = AdvisorContext(
        scope="company",
        keywords=[],
        events=[],
        items=[],
        reports=[],
        memory_facts=[],
        actor_role="owner",
        actor_access_scope="company",
    )
    service = SimpleNamespace(deepseek_client=object())

    assert _should_use_deepseek_for_bot_analysis("今天经营风险给我分析一下", context, service=service)
    assert not _should_use_deepseek_for_bot_analysis("最近一封邮件是什么", context, service=service)

    context.scope = "chat"
    assert not _should_use_deepseek_for_bot_analysis("帮我分析一下", context, service=service)


def test_heuristic_extraction_does_not_mark_normal_salary_sheet_as_risk() -> None:
    items = heuristic_extract_items("附件为2026年3月固势工资明细汇总表，请核对，有问题随时沟通。")

    assert [item["item_type"] for item in items] == []


def test_heuristic_extraction_keeps_salary_exception_as_risk() -> None:
    items = heuristic_extract_items("2026年3月工资明细存在差异和问题，请尽快确认风险。")

    assert [item["item_type"] for item in items] == ["risk"]


def test_heuristic_extraction_filters_low_signal_admin_notice() -> None:
    items = heuristic_extract_items("关于邮件系统迁移后统一邮件签名规范的提醒，如有问题请联系市场部。")

    assert items == []


def test_heuristic_extraction_does_not_make_offer_notice_a_decision() -> None:
    items = heuristic_extract_items("录用通知书已发送，请确认邮件成功传达并及时回复。")

    assert items == []


def test_heuristic_extraction_filters_welcome_tutorial_task() -> None:
    items = heuristic_extract_items("欢迎使用飞书邮箱，请查看使用指南并稍后处理。")

    assert items == []


def test_heuristic_extraction_ignores_markdown_report_headings() -> None:
    items = heuristic_extract_items("### 风险与阻塞\n### 已形成决策")

    assert items == []


def test_business_extraction_structures_compensation_decision() -> None:
    items = extract_business_items(
        "固势2025年度继续服务奖金，发放时间：2026/4/20，发放规则：发放前离职不予发放，"
        "根据公司现金流可适度顺延发放，请进行报税并操作。"
    )

    assert len(items) == 1
    item = items[0]
    assert item["item_type"] == "decision"
    assert item["business_object"] == "compensation"
    assert item["priority"] == "high"
    assert item["business_date"] == "2026-04-20"
    assert {"发放时间", "发放规则", "现金流顺延安排", "税务处理"} <= set(item["decision_points"])


def test_business_enrichment_does_not_upgrade_normal_salary_sheet() -> None:
    raw = {
        "item_type": "risk",
        "title": "2026年3月固势工资明细汇总表",
        "description": "附件为固势3月的薪资汇总，请核对审批，有问题随时沟通。",
        "priority": "medium",
    }

    item = enrich_extracted_item(raw, "")

    assert "business_object" not in item
    assert item["priority"] == "medium"


def test_business_enrichment_adds_payment_context() -> None:
    raw = {
        "item_type": "decision",
        "title": "付款申请",
        "description": "申请金额：50000，项目编号 PN2504001，等待审批。",
        "priority": "medium",
    }

    item = enrich_extracted_item(raw, "")

    assert item["business_object"] == "payment"
    assert item["priority"] == "high"
    assert item["amount"] == 50000
    assert item["suggested_action"] == "核对金额、申请人、项目归属和付款依据后再审批。"


def test_fallback_daily_report_filters_normal_salary_sheet_from_risk_section() -> None:
    report = fallback_daily_report(
        [
            {
                "id": "1",
                "source": "feishu",
                "title": "2026年3月固势工资明细汇总表",
                "content_text": "附件为工资总表，请核对。",
            }
        ]
    )

    risk_section = report.split("### 风险与阻塞", 1)[1].split("### 已形成决策", 1)[0]
    assert "工资明细汇总表" not in risk_section
