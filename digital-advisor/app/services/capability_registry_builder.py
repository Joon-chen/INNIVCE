from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.services.runtime_v5.capabilities import (
    RUNTIME_CAPABILITIES,
    SKILL_ATOMIC_CAPABILITIES,
    RuntimeCapability,
    SkillAtomicCapability,
)


def _capability(
    domain_id: str,
    capability_id: str,
    label: str,
    description: str,
    *,
    status: str = "available",
    user_visible: bool = True,
) -> dict[str, Any]:
    return {
        "domain_id": domain_id,
        "capability_id": capability_id,
        "label": label,
        "description": description,
        "status": status,
        "user_visible": user_visible,
        "supported_surfaces": ("bot", "detail", "portal"),
        "business_value": description,
    }


FROZEN_DOMAINS: tuple[dict[str, str], ...] = (
    {"domain_id": "people", "label": "People", "description": "组织与人员"},
    {"domain_id": "communication", "label": "Communication", "description": "沟通协作"},
    {"domain_id": "workspace", "label": "Workspace", "description": "任务、日程与执行"},
    {"domain_id": "process", "label": "Process", "description": "流程与审批"},
    {"domain_id": "knowledge", "label": "Knowledge", "description": "知识与文档"},
    {"domain_id": "business", "label": "Business", "description": "业务运营"},
    {"domain_id": "intelligence", "label": "Intelligence", "description": "智能分析"},
)


CAPABILITY_DEFINITIONS: tuple[dict[str, Any], ...] = (
    _capability("people", "organization_lookup", "查询组织架构", "读取组织结构、部门和人员关系。"),
    _capability("people", "contact_lookup", "查询通讯录", "查询人员、联系方式和基础身份。"),
    _capability("people", "employee_profile_read", "查看员工档案", "查看员工档案和上下文。", status="planned"),
    _capability("people", "attendance_query", "查询考勤", "查询个人或团队考勤记录。"),
    _capability("people", "performance_context", "查看绩效上下文", "查看绩效相关上下文。", status="planned"),
    _capability("people", "recruitment_context", "查看招聘上下文", "查看招聘进展和候选人上下文。", status="planned"),
    _capability("people", "payroll_context", "查看薪资上下文", "查看薪资相关上下文。", status="planned"),
    _capability("communication", "message_send", "发送消息", "向个人或群聊发送消息。"),
    _capability("communication", "chat_search", "搜索群聊", "查找群聊和消息上下文。"),
    _capability("communication", "mail_search", "搜索邮件", "检索邮件和会话。"),
    _capability("communication", "mail_summary", "总结邮件", "总结邮件内容并提取重点。"),
    _capability("communication", "mail_reply_draft", "生成邮件回复建议", "生成邮件草稿或回复建议。"),
    _capability("communication", "announcement_read", "查看公告", "读取企业公告。", status="planned"),
    _capability("communication", "bot_interaction", "机器人交互", "通过机器人进行工作交互。"),
    _capability("workspace", "task_query", "查询任务", "查询待办、任务和执行事项。"),
    _capability("workspace", "task_create", "创建任务", "创建新的任务或待办。"),
    _capability("workspace", "task_update", "更新任务", "完成、更新或调整任务。"),
    _capability("workspace", "task_follow_up", "跟进任务", "评论、提醒和跟进任务。"),
    _capability("workspace", "task_delay_risk", "识别任务延期风险", "识别任务延期或阻塞风险。", status="planned"),
    _capability("workspace", "okr_query", "查询 OKR", "查询目标和关键结果。"),
    _capability("workspace", "project_status", "查看项目状态", "查看项目进展和风险。", status="planned"),
    _capability("workspace", "calendar_query", "查询日程", "查询日历和会议安排。"),
    _capability("workspace", "meeting_schedule", "安排会议", "创建或调整会议日程。"),
    _capability("workspace", "meeting_summary", "总结会议", "总结会议和纪要内容。"),
    _capability("workspace", "meeting_action_items", "提取会议行动项", "从会议内容中提取待办。", status="planned"),
    _capability("process", "approval_query", "查询审批", "查询待处理或已发起审批。"),
    _capability("process", "approval_detail", "查看审批详情", "查看审批单详情。"),
    _capability("process", "approval_approve", "审批通过", "通过审批动作。"),
    _capability("process", "approval_reject", "审批拒绝", "拒绝审批动作。"),
    _capability("process", "approval_evidence_review", "查看审批判断依据", "查看审批 AI 判断依据。"),
    _capability("process", "reimbursement_analysis", "分析报销", "分析报销合理性和风险。"),
    _capability("process", "procurement_review", "采购审核", "审核采购流程和风险。", status="planned"),
    _capability("process", "payment_review", "付款审核", "审核付款流程和风险。", status="planned"),
    _capability("process", "leave_review", "请假审核", "审核请假流程。", status="planned"),
    _capability("process", "travel_review", "出差审核", "审核出差流程。", status="planned"),
    _capability("knowledge", "knowledge_search", "搜索知识", "搜索知识库和知识资源。"),
    _capability("knowledge", "document_read", "阅读文档", "读取文档、文件和知识内容。"),
    _capability("knowledge", "document_create", "创建文档", "创建新的文档。", status="planned"),
    _capability("knowledge", "document_summary", "总结文档", "总结文档内容。"),
    _capability("knowledge", "file_lookup", "查找文件", "查找云盘和附件文件。"),
    _capability("knowledge", "sheet_read", "读取表格", "读取电子表格。"),
    _capability("knowledge", "sheet_update", "更新表格", "写入或更新电子表格。"),
    _capability("knowledge", "minutes_read", "阅读会议纪要", "读取会议纪要、妙记和相关内容。"),
    _capability("business", "customer_profile", "查看客户画像", "查看客户资料和画像。", status="planned"),
    _capability("business", "opportunity_tracking", "跟进商机", "跟进商机进展。", status="planned"),
    _capability("business", "order_query", "查询订单", "查询订单信息。", status="planned"),
    _capability("business", "contract_review", "合同审核", "审核合同内容和风险。", status="planned"),
    _capability("business", "supplier_review", "供应商审核", "审核供应商风险。", status="planned"),
    _capability("business", "product_lookup", "查询产品", "查询产品信息。", status="planned"),
    _capability("business", "ticket_tracking", "跟进工单", "跟进工单处理。", status="planned"),
    _capability("business", "inventory_query", "查询库存", "查询库存信息。", status="planned"),
    _capability("business", "project_business_context", "查看业务项目上下文", "查看业务项目上下文。", status="planned"),
    _capability("intelligence", "daily_report_generate", "生成日报", "生成日报。", status="planned"),
    _capability("intelligence", "weekly_report_generate", "生成周报", "生成周报。", status="planned"),
    _capability("intelligence", "risk_detect", "识别风险", "识别企业运营风险。"),
    _capability("intelligence", "business_analysis", "经营分析", "分析经营数据和趋势。"),
    _capability("intelligence", "decision_recommendation", "决策建议", "给出决策建议。"),
    _capability("intelligence", "management_insight", "管理洞察", "生成管理洞察。"),
    _capability("intelligence", "cognitive_snapshot_read", "读取认知快照", "读取当前认知快照。"),
    _capability("intelligence", "insight_generate", "生成 Insight", "生成建议和洞察。", status="planned"),
)


SOURCE_DOMAIN = {
    "approval": "process",
    "people": "people",
    "attendance": "people",
    "calendar": "workspace",
    "task": "workspace",
    "okr": "workspace",
    "vc": "workspace",
    "vc_agent": "workspace",
    "minutes": "workspace",
    "note": "workspace",
    "mail": "communication",
    "im": "communication",
    "docs": "knowledge",
    "wiki": "knowledge",
    "drive": "knowledge",
    "sheets": "knowledge",
    "markdown": "knowledge",
    "slides": "knowledge",
    "whiteboard": "knowledge",
    "base": "business",
    "apps": "business",
    "openapi": "intelligence",
    "company_profile": "intelligence",
    "knowledge": "intelligence",
    "workevent": "intelligence",
    "memory": "intelligence",
    "web": "intelligence",
}


PROVIDER_NAMES = {
    "approval": "Feishu Approval",
    "people": "Feishu Contact",
    "attendance": "Feishu Attendance",
    "calendar": "Feishu Calendar",
    "task": "Feishu Task",
    "okr": "Feishu OKR",
    "vc": "Feishu VC",
    "vc_agent": "Feishu VC Agent",
    "minutes": "Feishu Minutes",
    "note": "Feishu Note",
    "mail": "Feishu Mail",
    "im": "Feishu IM",
    "docs": "Feishu Doc",
    "wiki": "Feishu Wiki",
    "drive": "Feishu Drive",
    "sheets": "Feishu Sheets",
    "markdown": "Feishu Markdown",
    "slides": "Feishu Slides",
    "whiteboard": "Feishu Whiteboard",
    "base": "Feishu Base",
    "apps": "Feishu Apps",
    "openapi": "Feishu OpenAPI",
    "company_profile": "Company Profile",
    "knowledge": "Knowledge",
    "workevent": "WorkEvent",
    "memory": "Memory",
    "web": "Web",
}


STRATEGY_CAPABILITY = {
    "approval_query": "approval_query",
    "approval_initiated": "approval_query",
    "approval_detail": "approval_detail",
    "approval_approve": "approval_approve",
    "approval_reject": "approval_reject",
    "approval_transfer": "approval_detail",
    "approval_add_sign": "approval_detail",
    "approval_rollback": "approval_detail",
    "approval_remind": "approval_detail",
    "approval_cancel": "approval_detail",
    "approval_cc": "approval_detail",
    "people_lookup": "contact_lookup",
    "department_members": "organization_lookup",
    "organization_snapshot": "organization_lookup",
    "organization_export": "organization_lookup",
    "task_query": "task_query",
    "task_search": "task_query",
    "task_create": "task_create",
    "task_complete": "task_update",
    "task_update": "task_update",
    "task_reopen": "task_update",
    "task_delete": "task_update",
    "task_subtask_create": "task_create",
    "task_comment": "task_follow_up",
    "task_assign_members": "task_update",
    "task_update_followers": "task_follow_up",
    "task_update_reminders": "task_follow_up",
    "task_upload_attachment": "task_update",
    "task_add_to_tasklist": "task_update",
    "task_set_ancestor": "task_update",
    "task_clear_ancestor": "task_update",
    "tasklist_create": "task_create",
    "tasklist_update": "task_update",
    "tasklist_delete": "task_update",
    "tasklist_update_members": "task_update",
    "tasklist_set_members": "task_update",
    "task_section_create": "task_create",
    "task_section_update": "task_update",
    "task_section_delete": "task_update",
    "calendar_query": "calendar_query",
    "calendar_create": "meeting_schedule",
    "mail_query": "mail_search",
    "mail_search": "mail_search",
    "mail_get_message": "mail_search",
    "mail_draft_create": "mail_reply_draft",
    "message_send": "message_send",
    "chat_search": "chat_search",
    "message_query": "chat_search",
    "chat_create": "chat_search",
    "chat_auto_join_public": "chat_search",
    "docs_read": "document_read",
    "docs_edit": "document_create",
    "wiki_search": "knowledge_search",
    "drive_list": "file_lookup",
    "drive_upload": "file_lookup",
    "base_query": "customer_profile",
    "base_create": "customer_profile",
    "sheets_read": "sheet_read",
    "sheets_create": "sheet_update",
    "sheets_write": "sheet_update",
    "vc_meeting_search": "meeting_summary",
    "minutes_read": "minutes_read",
    "note_read": "minutes_read",
    "markdown_read": "document_read",
    "markdown_write": "document_create",
    "apps_deploy": "project_business_context",
    "openapi_explore": "business_analysis",
    "attendance_query": "attendance_query",
    "okr_query": "okr_query",
    "okr_update": "okr_query",
    "slides_read": "document_read",
    "slides_write": "document_create",
    "whiteboard_read": "document_read",
    "whiteboard_write": "document_create",
    "vc_agent_read": "meeting_summary",
    "vc_agent_join": "meeting_schedule",
    "company_intro": "business_analysis",
    "risk_analysis": "risk_detect",
    "general_analysis": "business_analysis",
    "decision_advice": "decision_recommendation",
    "general_query": "knowledge_search",
}


@dataclass(frozen=True)
class CapabilityRegistryBuilder:
    company_id: str
    runtime_capabilities: tuple[RuntimeCapability, ...] = RUNTIME_CAPABILITIES
    skill_atomic_capabilities: tuple[SkillAtomicCapability, ...] = SKILL_ATOMIC_CAPABILITIES
    tool_configs: tuple[Any, ...] = ()
    provider_health: tuple[dict[str, Any], ...] = ()
    governance_findings: tuple[dict[str, Any], ...] = ()
    generated_at: str | None = None

    def build(self) -> dict[str, Any]:
        generated_at = self.generated_at or datetime.now(UTC).isoformat()
        catalog_payload = self.catalog_payload()
        skill_registry_payload = self.skill_registry_payload()
        governance_payload = self.governance_payload()
        diagnostics_payload = self.diagnostics_payload()
        registry_health = self.registry_health(
            catalog_payload=catalog_payload,
            skill_registry_payload=skill_registry_payload,
            governance_payload=governance_payload,
            diagnostics_payload=diagnostics_payload,
        )
        return {
            "version": "capability_registry_payload_v1",
            "registry_version": "capability_registry_v1",
            "generated_at": generated_at,
            "company_id": self.company_id,
            "registry_health": registry_health,
            "catalog_payload": catalog_payload,
            "skill_registry_payload": skill_registry_payload,
            "governance_payload": governance_payload,
            "diagnostics_payload": diagnostics_payload,
        }

    def catalog_payload(self) -> dict[str, Any]:
        capability_by_domain: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for definition in CAPABILITY_DEFINITIONS:
            capability_by_domain[definition["domain_id"]].append(
                {
                    "capability_id": definition["capability_id"],
                    "label": definition["label"],
                    "description": definition["description"],
                    "status": definition["status"],
                    "user_visible": definition["user_visible"],
                    "supported_surfaces": list(definition["supported_surfaces"]),
                    "business_value": definition["business_value"],
                }
            )

        domains = []
        for domain in FROZEN_DOMAINS:
            capabilities = sorted(capability_by_domain[domain["domain_id"]], key=lambda item: item["capability_id"])
            domains.append({**domain, "capabilities": capabilities})

        capability_count = sum(len(domain["capabilities"]) for domain in domains)
        visible_count = sum(
            1
            for domain in domains
            for capability in domain["capabilities"]
            if capability["user_visible"]
        )
        return {
            "summary": {
                "domain_count": len(domains),
                "capability_count": capability_count,
                "visible_capability_count": visible_count,
            },
            "domains": domains,
        }

    def skill_registry_payload(self) -> dict[str, Any]:
        skills_by_capability: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for skill in self.skill_atomic_capabilities:
            domain_id = SOURCE_DOMAIN.get(skill.source, "intelligence")
            capability_id = _capability_id_for_source_operation(skill.source, skill.operation)
            provider_binding_count = 1 if skill.source else 0
            skills_by_capability[capability_id].append(
                {
                    "skill_id": _skill_id(skill),
                    "label": skill.label or skill.operation,
                    "skill_type": "provider_skill",
                    "risk_level": skill.risk_level,
                    "status": "enabled" if skill.exposed else "pending",
                    "requires_confirmation": bool(skill.requires_confirmation),
                    "runtime_supported": _runtime_supported(skill),
                    "receipt_supported": _receipt_supported(skill),
                    "provider_binding_count": provider_binding_count,
                    "source": skill.source,
                    "operation": skill.operation,
                    "provider_bindings": [_provider_binding(skill.source, skill.operation, _skill_id(skill))],
                    "reason": skill.reason,
                    "domain_id": domain_id,
                    "capability_id": capability_id,
                }
            )

        capabilities = []
        capability_defs = {item["capability_id"]: item for item in CAPABILITY_DEFINITIONS}
        for capability_id in sorted(skills_by_capability):
            definition = capability_defs.get(capability_id)
            first_skill = skills_by_capability[capability_id][0]
            capabilities.append(
                {
                    "domain_id": definition["domain_id"] if definition else first_skill["domain_id"],
                    "capability_id": capability_id,
                    "label": definition["label"] if definition else capability_id,
                    "skills": sorted(skills_by_capability[capability_id], key=lambda item: item["skill_id"]),
                }
            )

        all_skills = [skill for capability in capabilities for skill in capability["skills"]]
        return {
            "summary": {
                "skill_count": len(all_skills),
                "enabled_count": len([item for item in all_skills if item["status"] == "enabled"]),
                "pending_count": len([item for item in all_skills if item["status"] != "enabled"]),
                "high_risk_count": len([item for item in all_skills if item["risk_level"] == "high"]),
                "missing_provider_count": len([item for item in all_skills if item["provider_binding_count"] == 0]),
            },
            "capabilities": capabilities,
        }

    def governance_payload(self) -> dict[str, Any]:
        findings = [_normalize_governance_finding(item) for item in self.governance_findings]
        findings.extend(self._generated_governance_findings())
        findings = sorted(_dedupe_findings(findings), key=lambda item: (item["severity"], item["finding_id"]))
        return {
            "summary": {
                "finding_count": len(findings),
                "p0_count": len([item for item in findings if item["severity"] == "P0"]),
                "p1_count": len([item for item in findings if item["severity"] == "P1"]),
                "p2_count": len([item for item in findings if item["severity"] == "P2"]),
                "p3_count": len([item for item in findings if item["severity"] == "P3"]),
            },
            "findings": findings,
        }

    def diagnostics_payload(self) -> dict[str, Any]:
        provider_items = [_provider_health_payload(item) for item in self.provider_health]
        failed_provider_count = len([item for item in provider_items if item["status"] not in {"healthy", "ok"}])
        failed_action_count = len(
            [
                item
                for item in self.governance_findings
                if str(item.get("scope") or "") == "runtime" and str(item.get("status") or "") == "failed"
            ]
        )
        provider_status = "healthy" if failed_provider_count == 0 else "degraded"
        action_status = "healthy" if failed_action_count == 0 else "degraded"
        overall_status = "healthy" if provider_status == "healthy" and action_status == "healthy" else "degraded"
        return {
            "summary": {
                "status": overall_status,
                "runtime_status": "healthy",
                "provider_status": provider_status,
                "permission_status": "healthy",
                "result_context_status": "healthy",
                "response_experience_status": "healthy",
                "action_state_status": action_status,
            },
            "runtime": {"status": "healthy", "last_error": None, "latency_ms": None},
            "providers": provider_items,
            "permission": {"status": "healthy", "missing_scope_count": 0},
            "result_context": {"status": "healthy", "missing_company_id_count": 0},
            "response_experience": {"status": "healthy", "slow_response_count": 0},
            "follow_up": {"status": "healthy", "pending_follow_up_count": 0},
            "action_state": {
                "status": action_status,
                "failed_action_count": failed_action_count,
                "waiting_confirmation_count": 0,
            },
        }

    def consistency_report(self) -> dict[str, Any]:
        guard = self.lifecycle_guard_report()
        missing_capabilities = guard["MissingCapabilityForSkill"]
        missing_skills = guard["MissingSkillForRuntime"]
        missing_providers = guard["MissingProviderForSkill"]
        orphan_skills = guard["OrphanSkill"]
        orphan_providers = guard["OrphanProviderBinding"]
        return {
            "status": "complete" if not any([missing_capabilities, missing_skills, missing_providers, orphan_skills, orphan_providers]) else "needs_attention",
            "MissingCapability": missing_capabilities,
            "MissingSkill": missing_skills,
            "MissingProvider": missing_providers,
            "OrphanSkill": orphan_skills,
            "OrphanProvider": orphan_providers,
        }

    def lifecycle_guard_report(self) -> dict[str, Any]:
        domain_ids = {item["domain_id"] for item in FROZEN_DOMAINS}
        catalog_capabilities = {item["capability_id"] for item in CAPABILITY_DEFINITIONS}
        missing_domain_for_capability = sorted(
            {
                item["capability_id"]
                for item in CAPABILITY_DEFINITIONS
                if item["domain_id"] not in domain_ids
            }
        )

        skill_payload = self.skill_registry_payload()
        skills = [skill for capability in skill_payload["capabilities"] for skill in capability["skills"]]
        skill_ids = {skill["skill_id"] for skill in skills}
        missing_capability_for_skill = sorted(
            {
                skill["capability_id"]
                for skill in skills
                if skill["capability_id"] not in catalog_capabilities
            }
        )
        missing_skill_for_runtime = sorted(
            {
                item.strategy
                for item in self.runtime_capabilities
                if _runtime_skill_id(item) not in skill_ids
            }
        )
        missing_provider_for_skill = sorted(
            {
                skill["skill_id"]
                for skill in skills
                if not skill.get("provider_bindings")
            }
        )
        orphan_skills = sorted(
            {
                skill["skill_id"]
                for skill in skills
                if skill["capability_id"] not in catalog_capabilities
            }
        )
        orphan_provider_bindings = sorted(
            {
                f"{binding.get('provider_id') or ''}:{binding.get('skill_id') or ''}"
                for skill in skills
                for binding in skill.get("provider_bindings", [])
                if not binding.get("provider_id") or binding.get("skill_id") != skill["skill_id"]
            }
        )
        issues = [
            missing_domain_for_capability,
            missing_capability_for_skill,
            missing_skill_for_runtime,
            missing_provider_for_skill,
            orphan_skills,
            orphan_provider_bindings,
        ]
        return {
            "status": "healthy" if not any(issues) else "needs_attention",
            "MissingDomainForCapability": missing_domain_for_capability,
            "MissingCapabilityForSkill": missing_capability_for_skill,
            "MissingSkillForRuntime": missing_skill_for_runtime,
            "MissingProviderForSkill": missing_provider_for_skill,
            "OrphanSkill": orphan_skills,
            "OrphanProviderBinding": orphan_provider_bindings,
        }

    def registry_health(
        self,
        *,
        catalog_payload: dict[str, Any] | None = None,
        skill_registry_payload: dict[str, Any] | None = None,
        governance_payload: dict[str, Any] | None = None,
        diagnostics_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        catalog_payload = catalog_payload or self.catalog_payload()
        skill_registry_payload = skill_registry_payload or self.skill_registry_payload()
        governance_payload = governance_payload or self.governance_payload()
        diagnostics_payload = diagnostics_payload or self.diagnostics_payload()
        report = self.consistency_report()
        provider_ids = {
            binding["provider_id"]
            for capability in skill_registry_payload["capabilities"]
            for skill in capability["skills"]
            for binding in skill.get("provider_bindings", [])
        }
        health_status = "healthy" if report["status"] == "complete" else report["status"]
        return {
            "status": health_status if diagnostics_payload["summary"]["status"] == "healthy" else "degraded",
            "domains": catalog_payload["summary"]["domain_count"],
            "capabilities": catalog_payload["summary"]["capability_count"],
            "skills": skill_registry_payload["summary"]["skill_count"],
            "providers": len(provider_ids),
            "findings": governance_payload["summary"]["finding_count"],
            "missing_capability": report["MissingCapability"],
            "missing_skill": report["MissingSkill"],
            "orphan_skills": report["OrphanSkill"],
            "missing_provider": report["MissingProvider"],
            "orphan_provider": report["OrphanProvider"],
        }

    def migration_assessment(self) -> dict[str, Any]:
        report = self.consistency_report()
        return {
            "ready_pages": [
                "capability_catalog",
                "skill_registry",
                "governance_center",
                "system_diagnostics",
            ],
            "conditional_pages": [],
            "missing_fields": {
                "capability_catalog": [],
                "skill_registry": ["receipt_supported needs real receipt integration for non-runtime actions"],
                "governance_center": ["governance findings are derived and need persisted lifecycle later"],
                "system_diagnostics": ["provider health is caller-supplied until diagnostics service integration"],
            },
            "builder_gaps": report,
            "next_step": "Wire a read-only builder service and contract tests before UI migration.",
        }

    def _generated_governance_findings(self) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []
        for skill in self.skill_registry_payload()["capabilities"]:
            for item in skill["skills"]:
                if item["risk_level"] == "high" and not item["requires_confirmation"]:
                    findings.append(
                        _finding(
                            scope="skill",
                            severity="P0",
                            domain_id=item["domain_id"],
                            capability_id=item["capability_id"],
                            skill_id=item["skill_id"],
                            provider_id=item["provider_bindings"][0]["provider_id"] if item["provider_bindings"] else "",
                            title="高风险能力缺确认",
                            message="High risk skill must require confirmation.",
                            recommendation="Mark the skill as confirmation-required before exposing it.",
                        )
                    )
                if item["status"] == "enabled" and not item["provider_binding_count"]:
                    findings.append(
                        _finding(
                            scope="skill",
                            severity="P1",
                            domain_id=item["domain_id"],
                            capability_id=item["capability_id"],
                            skill_id=item["skill_id"],
                            provider_id="",
                            title="已开放 Skill 缺 Provider",
                            message="Enabled skill has no provider binding.",
                            recommendation="Bind a provider or disable the skill.",
                        )
                    )
        for provider in self.provider_health:
            payload = _provider_health_payload(provider)
            if payload["status"] not in {"healthy", "ok"}:
                findings.append(
                    _finding(
                        scope="provider",
                        severity="P1",
                        domain_id="",
                        capability_id="",
                        skill_id="",
                        provider_id=payload["provider_id"],
                        title="Provider 异常",
                        message=payload.get("last_error") or "Provider health is not healthy.",
                        recommendation="Check provider credentials, permissions, and connectivity.",
                    )
                )
        for config in self.tool_configs:
            payload = _tool_config_payload(config)
            if not payload["enabled"]:
                findings.append(
                    _finding(
                        scope="skill",
                        severity="P2",
                        domain_id="",
                        capability_id="",
                        skill_id=payload["tool_name"],
                        provider_id=payload["provider"],
                        title="Tool Config 未启用",
                        message="Tool configuration is disabled.",
                        recommendation="Confirm whether this tool should stay disabled before UI migration.",
                    )
                )
            if payload["supports_write"] and not payload["provider"]:
                findings.append(
                    _finding(
                        scope="provider",
                        severity="P1",
                        domain_id="",
                        capability_id="",
                        skill_id=payload["tool_name"],
                        provider_id="",
                        title="写 Tool 缺 Provider",
                        message="Write-capable tool has no provider.",
                        recommendation="Bind a provider or keep the tool hidden from Skill Registry.",
                    )
                )
        return findings


def build_mock_capability_registry_payload(company_id: str = "mock_company") -> dict[str, Any]:
    builder = CapabilityRegistryBuilder(
        company_id=company_id,
        provider_health=(
            {"provider_id": "feishu_approval", "status": "healthy", "last_checked_at": "2026-06-21T00:00:00+00:00"},
            {"provider_id": "feishu_task", "status": "healthy", "last_checked_at": "2026-06-21T00:00:00+00:00"},
        ),
        governance_findings=(
            {
                "finding_id": "mock.skill.task_create.receipt_pending",
                "scope": "skill",
                "severity": "P2",
                "domain_id": "workspace",
                "capability_id": "task_create",
                "skill_id": "task.create_task",
                "provider_id": "feishu_task",
                "title": "回执能力待验证",
                "message": "Mock finding for registry-driven governance page.",
                "recommendation": "Verify action receipt before rollout.",
                "status": "open",
            },
        ),
        generated_at="2026-06-21T00:00:00+00:00",
    )
    return builder.build()


def _skill_id(skill: SkillAtomicCapability) -> str:
    return f"{skill.source}.{skill.operation}"


def _runtime_skill_id(capability: RuntimeCapability) -> str:
    return f"{capability.source}.{capability.operation}"


def _capability_id_for_source_operation(source: str, operation: str) -> str:
    strategy = _strategy_for_source_operation(source, operation)
    if strategy:
        return STRATEGY_CAPABILITY.get(strategy, strategy)
    if source == "approval":
        if operation == "approve":
            return "approval_approve"
        if operation == "reject":
            return "approval_reject"
        if operation in {"list_pending", "list_initiated"}:
            return "approval_query"
        return "approval_detail"
    if source == "task":
        if operation in {"list_my_tasks", "search_tasks"}:
            return "task_query"
        if "create" in operation:
            return "task_create"
        if operation in {"comment_task", "update_followers", "update_reminders"}:
            return "task_follow_up"
        return "task_update"
    if source == "calendar":
        return "meeting_schedule" if operation == "create_event" else "calendar_query"
    if source == "mail":
        return "mail_reply_draft" if operation == "create_draft" else "mail_search"
    if source == "im":
        return "message_send" if operation in {"send_message", "send_result"} else "chat_search"
    if source in {"docs", "markdown", "slides", "whiteboard"}:
        return "document_create" if operation.startswith(("edit", "write")) else "document_read"
    if source == "wiki":
        return "knowledge_search"
    if source == "drive":
        return "file_lookup"
    if source == "sheets":
        return "sheet_update" if operation.startswith(("create", "write")) else "sheet_read"
    if source in {"vc", "minutes", "note", "vc_agent"}:
        return "meeting_summary"
    if source == "attendance":
        return "attendance_query"
    if source == "okr":
        return "okr_query"
    if source == "base":
        return "customer_profile"
    if source == "openapi":
        return "business_analysis"
    return "business_analysis"


def _strategy_for_source_operation(source: str, operation: str) -> str | None:
    for capability in RUNTIME_CAPABILITIES:
        if capability.source == source and capability.operation == operation:
            return capability.strategy
    return None


def _runtime_supported(skill: SkillAtomicCapability) -> bool:
    return any(item.source == skill.source and item.operation == skill.operation for item in RUNTIME_CAPABILITIES)


def _receipt_supported(skill: SkillAtomicCapability) -> bool:
    return skill.question_type != "action" or _runtime_supported(skill)


def _provider_binding(source: str, operation: str, skill_id: str) -> dict[str, Any]:
    provider_id = _provider_id(source)
    return {
        "provider_id": provider_id,
        "provider_name": PROVIDER_NAMES.get(source, source),
        "provider_operation": operation,
        "skill_id": skill_id,
        "status": "enabled",
        "permission_required": [],
        "health_status": "unknown",
        "last_checked_at": None,
    }


def _provider_id(source: str) -> str:
    if source.startswith("lark-"):
        return source.replace("lark-", "feishu_").replace("/", "_").replace("-", "_")
    return f"feishu_{source}" if source in PROVIDER_NAMES and source not in {"knowledge", "workevent", "memory", "web"} else source


def _finding(
    *,
    scope: str,
    severity: str,
    domain_id: str,
    capability_id: str,
    skill_id: str,
    provider_id: str,
    title: str,
    message: str,
    recommendation: str,
    status: str = "open",
) -> dict[str, Any]:
    finding_id = ".".join(part for part in [scope, capability_id, skill_id, provider_id, title] if part)
    return {
        "finding_id": finding_id,
        "scope": scope,
        "severity": severity,
        "domain_id": domain_id,
        "capability_id": capability_id,
        "skill_id": skill_id,
        "provider_id": provider_id,
        "title": title,
        "message": message,
        "recommendation": recommendation,
        "status": status,
    }


def _normalize_governance_finding(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "finding_id": str(item.get("finding_id") or item.get("id") or "governance.finding"),
        "scope": str(item.get("scope") or "skill"),
        "severity": str(item.get("severity") or "P2"),
        "domain_id": str(item.get("domain_id") or ""),
        "capability_id": str(item.get("capability_id") or ""),
        "skill_id": str(item.get("skill_id") or ""),
        "provider_id": str(item.get("provider_id") or ""),
        "title": str(item.get("title") or "治理项"),
        "message": str(item.get("message") or ""),
        "recommendation": str(item.get("recommendation") or ""),
        "status": str(item.get("status") or "open"),
    }


def _tool_config_payload(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return {
            "tool_name": str(item.get("tool_name") or item.get("name") or ""),
            "provider": str(item.get("provider") or ""),
            "enabled": bool(item.get("enabled", True)),
            "supports_write": bool(item.get("supports_write", False)),
        }
    return {
        "tool_name": str(getattr(item, "tool_name", "")),
        "provider": str(getattr(item, "provider", "")),
        "enabled": bool(getattr(item, "enabled", True)),
        "supports_write": bool(getattr(item, "supports_write", False)),
    }


def _dedupe_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for item in findings:
        key = item["finding_id"]
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _provider_health_payload(item: dict[str, Any]) -> dict[str, Any]:
    provider_id = str(item.get("provider_id") or item.get("id") or item.get("provider") or "unknown")
    return {
        "provider_id": provider_id,
        "status": str(item.get("status") or "unknown"),
        "last_checked_at": item.get("last_checked_at") or item.get("checked_at") or "",
        "last_error": item.get("last_error") or item.get("error"),
    }
