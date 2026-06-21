# Capability Registry Shadow Verification

本文档验证现有四类页面是否可以由 `GET /api/v5/capability-registry` 驱动。

本阶段只做影子校验，不改 UI、不改数据库、不改业务逻辑。

## 1. Verification Scope

目标页面职责：

```text
能力目录 = catalog_payload
能力清册 = skill_registry_payload
治理中心 = governance_payload
系统诊断 = diagnostics_payload + registry_health
```

当前页面并没有独立的四个页面，相关内容分散在：

- `overview` 的 V5 架构视图和上线状态。
- `settings/resources` 的资源、同步、监控、治理动作。
- `settings/tools` 的工具配置、Tool 执行和 Provider 边界。
- `settings/audit` 的系统日志、Agent 链路和 Tool 执行日志。

因此本次按目标职责归并检查，不按当前 DOM 区块直接迁移。

## 2. Capability Catalog

目标：

```text
Domain -> Capability
```

当前页面字段清单：

- 业务工具族：`business_tool`、工具族名称、工具族范围。
- 数据层：Operational Data、Knowledge Data、Memory、WorkEvent。
- 入口：大飞哥、员工 Agent、权限边界、管理后台、iOS App。
- 运行状态：资源数、同步异常、工具启用数。

Registry 已覆盖字段：

- Domain：`domain_id`、`label`、`description`。
- Capability：`capability_id`、`label`、`description`、`status`、`user_visible`。
- 展示入口：`supported_surfaces`。
- 业务价值：`business_value`。
- 统计：`domain_count`、`capability_count`、`visible_capability_count`。

Registry 缺失字段：

- 无缺失的目录核心字段。
- 当前页面中的数据层、入口状态、工具启用数不是能力目录字段，应留在诊断或经营中心。

Registry 冗余字段：

- 无明显冗余。

可以立即迁移字段：

- Domain tabs。
- Capability list。
- Capability 状态。
- Capability 描述。
- Supported surfaces。

暂不能迁移字段：

- 工具族启用状态。
- 飞书资源数。
- 同步异常。
- 入口健康状态。

结论：

能力目录可以由 `catalog_payload` 完整驱动。

## 3. Skill Registry

目标：

```text
Capability -> Skill
```

当前页面字段清单：

- 工具配置：`tool_name`、`business_tool`、`provider`、`compatible_providers`。
- 工具状态：`enabled`、`supports_write`、`required_permissions`、`audit_action`。
- Tool Runtime hint：Provider 边界、MCP/API/CLI 职责、写策略。
- Tool 执行记录：执行源、数据源、状态、确认令牌、耗时、错误。

Registry 已覆盖字段：

- Skill：`skill_id`、`label`、`skill_type`。
- 归属：`domain_id`、`capability_id`。
- 风险：`risk_level`、`requires_confirmation`。
- 状态：`status`。
- Runtime 能力：`runtime_supported`。
- 回执能力：`receipt_supported`。
- Provider 绑定数量：`provider_binding_count`。
- Provider binding：`provider_id`、`provider_name`、`provider_operation`、`status`。
- 统计：`skill_count`、`enabled_count`、`pending_count`、`high_risk_count`、`missing_provider_count`。

Registry 缺失字段：

- `compatible_providers`。
- `required_permissions`。
- `audit_action`。
- `business_tool` 旧工具族字段。
- Provider boundary 详细解释。
- Tool 执行记录。

Registry 冗余字段：

- `source`、`operation` 对最终清册可隐藏，只在调试态保留。
- `reason` 可作为详情字段，不必在列表展示。

可以立即迁移字段：

- Skill 列表。
- Capability 归属。
- Risk level。
- Confirmation required。
- Runtime supported。
- Receipt supported。
- Provider binding count。
- Skill enabled/pending 状态。

暂不能迁移字段：

- Tool 配置编辑。
- Tool 执行记录。
- Provider boundary 详细文案。
- 兼容执行通道。
- 权限明细。

结论：

能力清册主体可以由 `skill_registry_payload` 驱动；工具配置和执行记录仍应留在原管理页或后续治理详情中。

## 4. Governance Center

目标：

```text
Capability / Skill / Provider -> Finding
```

当前页面字段清单：

- 治理动作：`action_code`、`title`、`count`、`business_domain`。
- 接入建议：`access_recommendation_summary`、`recommended_notify_targets`。
- 责任人：`responsible_role`。
- 业务影响：`business_impact`。
- 下一步：`owner_next_step_detail`、`system_behavior`。
- 关联资源：`resources`、`affected_resources`。
- 资源阻断：同步失败、访问阻断、缺资源标识、缺授权。

Registry 已覆盖字段：

- Finding：`finding_id`、`scope`、`severity`。
- 归属：`domain_id`、`capability_id`、`skill_id`、`provider_id`。
- 内容：`title`、`message`、`recommendation`、`status`。
- 统计：`finding_count`、`p0_count`、`p1_count`、`p2_count`、`p3_count`。

Registry 缺失字段：

- 资源接入治理动作尚未转换为 Finding。
- `action_code` 尚未映射到 Finding 类型。
- `business_impact`、`responsible_role`、`recommended_notify_targets` 尚未进入 Registry Finding。
- 关联资源列表尚未作为 Finding evidence/reference 挂载。
- Finding 生命周期还未持久化。

Registry 冗余字段：

- 当前无明显冗余。

可以立即迁移字段：

- Capability/Skill/Provider 层面的治理问题。
- 高风险缺确认。
- 已开放 Skill 缺 Provider。
- Provider 异常。
- Tool Config 未启用。

暂不能迁移字段：

- 资源接入治理。
- 数据覆盖治理。
- 自动同步治理。
- 需要人工处理流转的 owner action。

结论：

治理中心可以先影子展示 Registry Finding，但不能完全替代现有资源治理动作。

## 5. System Diagnostics

目标：

```text
Runtime Health
```

当前页面字段清单：

- 系统日志：`severity`、`category`、`status`、`reason`、`action`、`error`。
- Gateway 链路：`used_agent_runtime`、`final_answer_owner`、`route_path`、`reply_mode`。
- Agent Runtime：步骤数、Tool steps、Workflow steps。
- Tool 执行：状态、耗时、错误、确认令牌、执行源、数据源。
- 资源监控：同步状态、同步异常、资源类型统计。

Registry 已覆盖字段：

- Runtime：`runtime.status`、`runtime.last_error`、`runtime.latency_ms`。
- Provider：`providers[]`、`provider_status`。
- Permission：`permission_status`、`missing_scope_count`。
- Result Context：`result_context_status`、`missing_company_id_count`。
- Response Experience：`response_experience_status`、`slow_response_count`。
- Follow-up：`pending_follow_up_count`。
- Action State：`failed_action_count`、`waiting_confirmation_count`。
- Registry Health：`status`、`domains`、`capabilities`、`skills`、`providers`、`findings`。

Registry 缺失字段：

- 最近错误列表。
- 日志明细。
- Gateway 链路明细。
- Tool execution 明细。
- 延迟分布。
- Provider health 当前依赖调用方传入，尚未接 Runtime Diagnostics。

Registry 冗余字段：

- `domains`、`capabilities`、`skills`、`providers`、`findings` 对系统诊断可作为 Registry 自检，不应混成业务健康度。

可以立即迁移字段：

- 顶层健康状态。
- Runtime / Provider / Permission / Result Context / Response Experience / Follow-up / Action State 状态卡。
- Registry 完整性自检。

暂不能迁移字段：

- 日志表。
- Tool 执行表。
- Gateway message 链路表。
- 资源同步监控表。

结论：

系统诊断可由 `diagnostics_payload + registry_health` 驱动顶层健康页；详细日志仍需要现有 API。

## 6. Registry Health Findings

当前示例：

```json
{
  "status": "needs_attention",
  "domains": 7,
  "capabilities": 60,
  "skills": 57,
  "providers": 22,
  "findings": 0,
  "missing_capability": [],
  "missing_skill": [
    "approval_initiated",
    "chat_auto_join_public",
    "company_intro",
    "decision_advice",
    "general_analysis",
    "general_query",
    "risk_analysis",
    "task_add_to_tasklist",
    "task_assign_members",
    "task_clear_ancestor",
    "task_comment",
    "task_reopen",
    "task_section_create",
    "task_section_delete",
    "task_section_update",
    "task_set_ancestor",
    "task_subtask_create",
    "task_update_followers",
    "task_update_reminders",
    "task_upload_attachment",
    "tasklist_create",
    "tasklist_delete",
    "tasklist_set_members",
    "tasklist_update",
    "tasklist_update_members"
  ],
  "orphan_skills": [],
  "missing_provider": [],
  "orphan_provider": []
}
```

解释：

- Capability 存在但无 Skill：当前 `missing_skill` 中的 strategy 多数已经能映射到 Capability，但没有等价 `SkillAtomicCapability`。
- Skill 存在但无 Provider：当前无，`missing_provider = []`。
- 历史能力未归类：当前无 Capability orphan，`missing_capability = []`。
- Provider 未绑定：当前无，`orphan_provider = []`。
- 孤儿 Skill：当前无，`orphan_skills = []`。

需要治理的具体方向：

- 将任务评论、任务成员、任务清单、任务分组等 Runtime strategy 补为显式 Skill。
- 将 `general_query/general_analysis/risk_analysis/decision_advice/company_intro` 明确归为 AI Skill，而不是继续混在历史 Runtime strategy。
- 将 `approval_initiated` 明确为审批查询 Skill 或归并到 `approval_query` 的别名策略。
- 将 `chat_auto_join_public` 明确为 Communication 治理 Skill，或降级为 legacy/admin-only path。

## 7. Four Page Coverage

| Target Page | Registry Payload | Coverage | Blocker |
| --- | --- | --- | --- |
| 能力目录 | `catalog_payload` | 100% for target page | 无 |
| 能力清册 | `skill_registry_payload` | 80% | Tool config/edit/execution fields 不属于清册核心 |
| 治理中心 | `governance_payload` | 60% | 资源接入治理动作尚未转 Finding |
| 系统诊断 | `diagnostics_payload + registry_health` | 70% | 日志、链路和 Tool 执行明细仍在现有 API |

## 8. Minimal Migration Readiness

可以立即做影子接入：

- 在前端隐藏调试入口中读取 `/api/v5/capability-registry`。
- 不改变现有页面展示。
- 只在控制台输出或内部 debug 面板比较 payload counts。

可以最小迁移：

- 新增能力目录只读页面。
- 新增能力清册只读表。
- 新增 Registry Health 卡片。

暂不建议迁移：

- 治理中心主操作流。
- 系统日志和 Tool 执行明细。
- 资源同步、资源发现、授权处理。

下一步建议：

```text
Minimal Registry Shadow Panel
```

目标是在管理后台加一个只读、隐藏或内部 tab 的 Registry Shadow Panel，用真实 API 展示四个 payload summary 和 health，不替换现有页面。
