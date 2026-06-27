# Skill Registry Cleanup Plan

本文档记录 Registry Cleanup Phase 的治理结果。

目标：

```text
Capability
-> Skill
-> Provider
```

本阶段只治理 Registry，不改 UI、不改数据库、不改页面。

## 1. Before

Registry Health before cleanup:

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

问题判断：

- Capability 已存在。
- Provider 绑定没有缺失。
- 主要问题是 Runtime strategy 已存在，但 SkillAtomicCapability 未显式登记。

## 2. Classification

### Workspace Skill

| Missing item | Decision | Target Capability | Provider | Exposure |
| --- | --- | --- | --- | --- |
| `task_reopen` | 保留，补 Skill | `task_update` | `lark-task` | Pending |
| `task_subtask_create` | 保留，补 Skill | `task_create` | `lark-task` | Pending |
| `task_comment` | 保留，补 Skill | `task_follow_up` | `lark-task` | Pending |
| `task_assign_members` | 保留，补 Skill | `task_update` | `lark-task` | Pending |
| `task_update_followers` | 保留，补 Skill | `task_follow_up` | `lark-task` | Pending |
| `task_update_reminders` | 保留，补 Skill | `task_follow_up` | `lark-task` | Pending |
| `task_upload_attachment` | 保留，补 Skill | `task_update` | `lark-task` | Pending |
| `task_add_to_tasklist` | 保留，补 Skill | `task_update` | `lark-task` | Pending |
| `task_set_ancestor` | 保留，补 Skill | `task_update` | `lark-task` | Pending |
| `task_clear_ancestor` | 保留，补 Skill | `task_update` | `lark-task` | Pending |
| `tasklist_create` | 保留，补 Skill | `task_create` | `lark-task` | Pending |
| `tasklist_update` | 保留，补 Skill | `task_update` | `lark-task` | Pending |
| `tasklist_delete` | 保留，补 Skill | `task_update` | `lark-task` | Pending |
| `tasklist_update_members` | 保留，补 Skill | `task_update` | `lark-task` | Pending |
| `tasklist_set_members` | 保留，补 Skill | `task_update` | `lark-task` | Pending |
| `task_section_create` | 保留，补 Skill | `task_create` | `lark-task` | Pending |
| `task_section_update` | 保留，补 Skill | `task_update` | `lark-task` | Pending |
| `task_section_delete` | 保留，补 Skill | `task_update` | `lark-task` | Pending |

说明：

- 这些属于 Workspace 域。
- 全部保留，但不立即开放。
- 等 Task Cognitive/Runtime Sample 明确后，再决定开放路径。

### Communication Skill

| Missing item | Decision | Target Capability | Provider | Exposure |
| --- | --- | --- | --- | --- |
| `chat_auto_join_public` | 保留，补 Skill | `chat_search` | `lark-im` | Pending |

说明：

- 这是治理/管理员能力，不是普通沟通能力。
- 保留 Registry 绑定，但暂不开放普通对话执行。

### Process Skill

| Missing item | Decision | Target Capability | Provider | Exposure |
| --- | --- | --- | --- | --- |
| `approval_initiated` | 合并到审批查询能力，补 Skill | `approval_query` | `lark-approval` | Enabled |

说明：

- `approval_initiated` 不是新 Capability。
- 作为 `approval_query` 下的 Skill 变体存在。

### Knowledge Skill

| Missing item | Decision | Target Capability | Provider | Exposure |
| --- | --- | --- | --- | --- |
| `general_query` | 合并为知识搜索 Skill | `knowledge_search` | `internal-ai` | Enabled |

说明：

- `general_query` 不作为独立业务模块。
- 它复用 `knowledge.search`。

### Business Skill

本次没有必须归入 Business 域的 missing skill。

说明：

- `company_intro` 里包含公司档案读取，但当前按 Intelligence 的 business analysis 能力承接。
- 不新增 Business 专属 Skill，避免把企业档案读取误建成业务运营模块。

### Intelligence Skill

| Missing item | Decision | Target Capability | Provider | Exposure |
| --- | --- | --- | --- | --- |
| `company_intro` | 保留，补 AI Skill | `business_analysis` | `internal-ai` / `web-search` | Enabled |
| `risk_analysis` | 保留，补 AI Skill | `risk_detect` | `internal-ai` | Enabled |
| `general_analysis` | 保留，补 AI Skill | `business_analysis` | `internal-ai` | Enabled |
| `decision_advice` | 保留，补 AI Skill | `decision_recommendation` | `internal-ai` | Enabled |

说明：

- 这些不是飞书产品能力。
- 它们属于企业认知与智能分析输出。
- Provider 使用 `internal-ai`、`workevent`、`knowledge`、`memory`、`web-search` 等被动能力。

## 3. Cleanup Actions

已执行：

- 补充 `approval.list_initiated`。
- 补充 18 个 Workspace Task/Tasklist/Section 原子 Skill。
- 补充 `im.auto_join_public_chats`。
- 补充 7 个 Intelligence / Knowledge / Memory / WorkEvent / Web 原子 Skill。
- 对高风险写动作保持 `requires_confirmation = true`。
- 对未完成样板验证的写动作保持 `exposed = false`。

未执行：

- 不新增 Capability。
- 不删除 Runtime strategy。
- 不重命名现有 Runtime strategy。
- 不改变 Provider 执行逻辑。
- 不开放 pending Skill。

## 4. After

Registry Health after cleanup:

```json
{
  "status": "healthy",
  "domains": 7,
  "capabilities": 60,
  "skills": 84,
  "providers": 27,
  "findings": 0,
  "missing_capability": [],
  "missing_skill": [],
  "orphan_skills": [],
  "missing_provider": [],
  "orphan_provider": []
}
```

统计变化：

| Metric | Before | After |
| --- | ---: | ---: |
| Status | `needs_attention` | `healthy` |
| Domains | 7 | 7 |
| Capabilities | 60 | 60 |
| Skills | 57 | 84 |
| Providers | 22 | 27 |
| Findings | 0 | 0 |
| Missing capability | 0 | 0 |
| Missing skill | 25 | 0 |
| Orphan skill | 0 | 0 |
| Missing provider | 0 | 0 |
| Orphan provider | 0 | 0 |

## 5. Remaining Governance Notes

Registry 现在健康，但不代表全部 Skill 都应立刻开放。

仍需后续治理：

- Task 高级写动作需要 Task Runtime Sample 验证后再开放。
- USER 参数相关 Skill 需要 USER Resolver 后再开放。
- DATE/TIME 参数相关 Skill 需要参数类型扩展后再开放。
- 资源治理类 Communication Skill 应保持 admin/gated。
- AI Skill 的输出边界仍应遵守 Insight 不执行、Action 归 Runtime。

## 6. Next Recommendation

下一步建议进入：

```text
Capability Registry Contract Freeze Phase
```

目标：

- 冻结当前 Registry 健康基线。
- 防止未来新增 Runtime strategy 时忘记补 Skill。
- 增加守护测试：RuntimeCapability 必须映射到 SkillAtomicCapability。
