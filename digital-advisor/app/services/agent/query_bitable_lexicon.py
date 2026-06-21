from __future__ import annotations

import re

TABLE_QUERY_TERMS = (
    "查",
    "查询",
    "查看",
    "看看",
    "筛选",
    "列出",
    "列举",
    "找",
    "搜",
    "搜索",
    "看下",
    "看一看",
    "同步",
    "抽取",
)

TABLE_ACTION_TERMS = (
    "建",
    "创建",
    "新建",
    "建表",
    "建立",
    "起",
    "写入",
    "放入",
    "同步",
    "导出",
    "导入",
)

TABLE_TARGET_TERMS = (
    "表",
    "表格",
    "多维表格",
    "bitable",
    "base",
)

TABLE_QUERY_DOMAIN_TERMS = (
    "待办",
    "任务",
    "事项",
    "审批",
    "待审",
    "待我",
    "待处理",
    "邮件",
    "邮件箱",
    "收件箱",
    "付款",
    "报销",
    "用章",
    "合同",
    "组织",
    "群",
    "项目",
    "清单",
    "列表",
    "个人",
    "待办事项",
    "待办任务",
)

TABLE_QUERY_RELATED_TERMS = (
    "同步",
    "导出",
    "写入",
    "放入",
    "建表",
    "创建",
    "新建",
    "表",
    "表格",
)

_DECISION_GUARD_TERMS = (
    "该不该",
    "应该",
    "可否",
    "是否",
    "能不能",
    "行不行",
    "帮我判断",
    "建议",
    "对比",
    "比较后",
)

_ANALYSIS_GUARD_TERMS = (
    "分析",
    "趋势",
    "原因",
    "影响",
    "评估",
    "风险",
    "异常",
    "统计",
    "总结",
    "汇总",
    "隐患",
    "复盘",
    "回顾",
    "原因",
)

QUERY_TERM_PATTERN = re.compile(
    r"查|查询|查看|看看|筛选|列出|列举|找|搜|搜索|看下|看一看|看下|同步|抽取"
)
TABLE_TARGET_TERM_PATTERN = re.compile(
    r"表|表格|多维表格|bitable|base|电子表格|sheet|工作表"
)
CREATE_TERM_PATTERN = re.compile(
    r"创建|建|新建|建表|建一|建张|建个|新建一个|新建一张|起一|起张|起个|整一|整张|弄一|弄个|做一|做张|做个|起一张|建张"
)
IMPORT_TERM_PATTERN = re.compile(
    r"放入|写入|导入|填入|装入|放到|塞进|放进|放进去|同步|更新|写入到|更新到"
)
EXPORT_TERM_PATTERN = re.compile(r"导出|导成|导到|导出到")
QUERY_TEXT_ANALYTIC_TERMS = (
    "分析",
    "趋势",
    "原因",
    "影响",
    "评估",
    "风险",
    "异常",
    "统计",
    "总结",
    "汇总",
    "隐患",
    "复盘",
    "回顾",
    "为什么",
    "现状",
    "进展",
)
QUERY_TEXT_WRITE_PROMOTION_TERMS = (
    "导出",
    "导入",
    "写入",
    "放入",
    "填入",
    "装入",
    "放到",
    "塞进",
    "放进",
    "放进去",
    "更新",
    "更新到",
    "同步",
)


def contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def is_strong_bitable_query(text: str) -> bool:
    if QUERY_TERM_PATTERN.search(text) is None:
        return False
    if not (_contains(text, TABLE_ACTION_TERMS) and _contains(text, TABLE_TARGET_TERMS)):
        return False
    if not _contains(text, TABLE_QUERY_DOMAIN_TERMS):
        return False
    if _contains(text, QUERY_TEXT_ANALYTIC_TERMS) and not _contains(
        text,
        QUERY_TEXT_WRITE_PROMOTION_TERMS,
    ):
        return False
    if _contains(text, _ANALYSIS_GUARD_TERMS) and _contains(text, _DECISION_GUARD_TERMS):
        return False
    return True


def is_ambiguous_bitable_query(text: str) -> bool:
    if QUERY_TERM_PATTERN.search(text) is None:
        return False
    if _contains(text, _ANALYSIS_GUARD_TERMS):
        return True
    return not _contains(text, TABLE_QUERY_DOMAIN_TERMS)


def _contains(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)
