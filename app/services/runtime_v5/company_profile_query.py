from __future__ import annotations

import re


COMPANY_PROFILE_FIELD_MARKERS = (
    "公司介绍",
    "公司情况",
    "主营业务",
    "主要业务",
    "公司主营",
    "业务范围",
    "公司业务",
    "做什么",
    "干什么",
    "是什么公司",
    "产品",
    "产品线",
    "客户",
    "目标客户",
    "服务谁",
    "面向谁",
    "优势",
    "特点",
    "能力",
    "业务模式",
    "应用场景",
    "使用场景",
    "解决方案",
    "不明确",
    "不清楚",
    "不确定",
    "信息缺口",
    "资料缺口",
    "哪些信息",
    "联系方式",
    "联系电话",
    "联系邮箱",
    "联系地址",
    "地址",
)


def looks_like_company_profile_query(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or "").lower())
    if not compact:
        return False
    if any(token in compact for token in ("我们公司", "咱们公司", "本公司", "当前公司", "这家公司", "公司")):
        return any(marker in compact for marker in COMPANY_PROFILE_FIELD_MARKERS)
    return any(marker in compact for marker in ("主营业务", "主要业务", "公司介绍", "公司是做什么", "是什么公司", "资料里哪些信息", "哪些信息不明确", "信息缺口"))
