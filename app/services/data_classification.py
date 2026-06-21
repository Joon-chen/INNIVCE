from dataclasses import dataclass
from typing import Any


COMPANY = "company"
PERSONAL = "personal"

DOMAIN_KEYWORDS: dict[str, tuple[str, ...]] = {
    "finance": ("付款", "回款", "发票", "报销", "工资", "薪资", "借款", "预算", "银行", "payment", "invoice", "reimbursement"),
    "sales": ("客户", "销售", "订单", "报价", "合同", "商机", "回款", "customer", "sales", "order", "quotation", "contract"),
    "project": ("项目", "交付", "进度", "验收", "里程碑", "project", "delivery", "milestone"),
    "rd": ("研发", "技术", "测试", "设备", "软件", "硬件", "故障", "验证", "technology", "test", "device"),
    "hr": ("招聘", "入职", "离职", "请假", "考勤", "人事", "recruit", "onboarding", "leave"),
    "admin": ("行政", "采购", "用章", "办公", "快递", "物业", "purchase", "office"),
}

PERSONAL_KEYWORDS = (
    "私人",
    "个人事项",
    "家庭",
    "家人",
    "医疗",
    "体检",
    "保险理赔",
    "信用卡",
    "个人账单",
    "购物",
    "个人消费",
    "private",
    "personal",
    "family",
)

PERSONAL_FOLDERS = {"personal", "private", "个人", "私人"}


@dataclass(frozen=True)
class Classification:
    data_classification: str
    business_domain: str
    visibility_scope: str
    reason: str


def classify_mail_event(
    *,
    account_settings: dict[str, Any] | None,
    subject: str | None,
    text_body: str | None,
    headers: dict[str, str | None],
    labels: list[str],
) -> Classification:
    settings = account_settings or {}
    combined = _combined_text(subject, text_body, headers)
    lowered = combined.lower()
    label_set = {str(label).strip().lower() for label in labels}

    if label_set & {item.lower() for item in PERSONAL_FOLDERS}:
        return Classification(PERSONAL, "personal", _personal_visibility(settings), "personal_folder")
    if _contains_any(combined, settings.get("personal_keywords") or PERSONAL_KEYWORDS):
        return Classification(PERSONAL, "personal", _personal_visibility(settings), "personal_keyword")
    if _matches_sender(headers.get("from"), settings.get("personal_senders") or []):
        return Classification(PERSONAL, "personal", _personal_visibility(settings), "personal_sender")
    if _matches_sender(headers.get("from"), settings.get("personal_domains") or []):
        return Classification(PERSONAL, "personal", _personal_visibility(settings), "personal_domain")

    domain = "general"
    for candidate, keywords in DOMAIN_KEYWORDS.items():
        if any(keyword.lower() in lowered for keyword in keywords):
            domain = candidate
            break
    return Classification(PERSONAL, domain, _personal_visibility(settings), f"mail_business_domain:{domain}")


def _combined_text(subject: str | None, text_body: str | None, headers: dict[str, str | None]) -> str:
    return "\n".join(
        str(item or "")
        for item in (
            subject,
            text_body[:2000] if text_body else "",
            headers.get("from"),
            headers.get("to"),
            headers.get("cc"),
        )
    )


def _contains_any(text: str, keywords: list[str] | tuple[str, ...]) -> bool:
    return any(str(keyword).strip() and str(keyword).lower() in text.lower() for keyword in keywords)


def _matches_sender(sender: str | None, patterns: list[str]) -> bool:
    value = str(sender or "").lower()
    return any(str(pattern).strip().lower() in value for pattern in patterns)


def _company_visibility(settings: dict[str, Any]) -> str:
    return str(settings.get("company_visibility_scope") or settings.get("visibility_scope") or "company_management")


def _personal_visibility(settings: dict[str, Any]) -> str:
    return str(settings.get("personal_visibility_scope") or "owner")
