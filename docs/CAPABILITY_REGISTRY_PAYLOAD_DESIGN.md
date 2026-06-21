# Capability Registry Payload Design

本文档冻结 Capability Registry 的页面消费 Payload。

目标：

```text
能力目录 = Catalog Payload
能力清册 = Skill Registry Payload
治理中心 = Governance Payload
系统诊断 = Diagnostics Payload
```

本阶段只设计只读 Payload，不实现 API、不改页面、不建表。

## 1. Design Principles

### Page Inputs Must Be Separated

四个页面不共享临时拼装字段：

```text
Capability Catalog
Skill Registry
Governance Center
System Diagnostics
```

每个页面只读取自己的 Payload。

### Catalog Does Not Expose Implementation

能力目录只展示：

```text
Domain -> Capability
```

禁止展示：

- Skill。
- Provider。
- Tool。
- route_path。
- Feishu endpoint。
- CLI / MCP / API。
- Runtime strategy。

### Diagnostics Does Not Use Business Classification

系统诊断只展示 Runtime Health。

禁止展示：

- Domain。
- Capability。
- Skill。
- 业务域健康度。
- 飞书产品分类统计。

### Governance Is Finding-Based

治理中心不展示普通资源列表。

治理中心只展示：

```text
Finding
```

Finding 必须归属：

```text
Capability | Skill | Provider
```

## 2. Root Payload

统一根结构：

```json
{
  "version": "capability_registry_payload_v1",
  "generated_at": "",
  "company_id": "",
  "catalog_payload": {},
  "skill_registry_payload": {},
  "governance_payload": {},
  "diagnostics_payload": {}
}
```

规则：

- `company_id` 必须存在。
- `generated_at` 表示 Payload 生成时间。
- 四个子 Payload 可独立演进。
- 页面不得跨读其他子 Payload。

## 3. Catalog Payload

用途：

```text
能力目录
= Domain -> Capability
```

结构：

```json
{
  "summary": {
    "domain_count": 7,
    "capability_count": 0,
    "visible_capability_count": 0
  },
  "domains": [
    {
      "domain_id": "workspace",
      "label": "Workspace",
      "description": "任务、日程与执行",
      "capabilities": [
        {
          "capability_id": "task_query",
          "label": "查询任务",
          "description": "",
          "status": "available",
          "user_visible": true,
          "supported_surfaces": ["bot", "portal"],
          "business_value": ""
        }
      ]
    }
  ]
}
```

字段说明：

- `domain_id` 必须来自冻结 Domain。
- `capability_id` 必须来自 Capability Registry。
- `status` 只表达用户可见状态，不表达 Provider 健康。
- `supported_surfaces` 只表达用户入口，不表达技术链路。

禁止字段：

```text
skill_id
provider_id
route_path
endpoint
tool_name
runtime_strategy
requires_confirmation
```

## 4. Skill Registry Payload

用途：

```text
能力清册
= Capability -> Skill
```

结构：

```json
{
  "summary": {
    "skill_count": 0,
    "enabled_count": 0,
    "pending_count": 0,
    "high_risk_count": 0,
    "missing_provider_count": 0
  },
  "capabilities": [
    {
      "domain_id": "process",
      "capability_id": "approval_approve",
      "label": "审批通过",
      "skills": [
        {
          "skill_id": "approval_approve",
          "label": "审批通过",
          "skill_type": "provider_skill",
          "risk_level": "high",
          "status": "enabled",
          "requires_confirmation": true,
          "runtime_supported": true,
          "receipt_supported": true,
          "provider_binding_count": 1
        }
      ]
    }
  ]
}
```

字段说明：

- `skill_id` 是清册主键。
- `capability_id` 是 Skill 的业务归属。
- `risk_level` 可用于治理输入，但清册只展示事实。
- `provider_binding_count` 表示绑定数量，不展开 Provider 细节。

禁止字段：

```text
governance_action
resource_sync_status
runtime_log
system_error_trace
business_object_items
```

## 5. Governance Payload

用途：

```text
治理中心
= Capability -> Skill -> Provider Finding
```

结构：

```json
{
  "summary": {
    "finding_count": 0,
    "p0_count": 0,
    "p1_count": 0,
    "p2_count": 0,
    "p3_count": 0
  },
  "findings": [
    {
      "finding_id": "skill.approval_approve.missing_receipt",
      "scope": "skill",
      "severity": "P1",
      "domain_id": "process",
      "capability_id": "approval_approve",
      "skill_id": "approval_approve",
      "provider_id": "feishu_approval",
      "title": "写操作缺回执",
      "message": "",
      "recommendation": "",
      "status": "open"
    }
  ]
}
```

Scope：

```text
capability
skill
provider
```

Severity：

```text
P0: 越权、误执行、无确认写操作
P1: 执行失败或状态不闭环
P2: 体验不完整或运维不可见
P3: 元数据不完整
```

字段说明：

- Finding 必须可定位到 Capability、Skill 或 Provider。
- `provider_id` 可为空，但 scope 为 provider 时必须存在。
- `status` 初始支持 `open` / `ignored` / `resolved`。

禁止字段：

```text
plain_resource_list
sync_preview_rows
business_object_rows
runtime_trace_detail
catalog_marketing_copy
```

## 6. Diagnostics Payload

用途：

```text
系统诊断
= Runtime Health
```

结构：

```json
{
  "summary": {
    "status": "healthy",
    "runtime_status": "healthy",
    "provider_status": "healthy",
    "permission_status": "healthy",
    "result_context_status": "healthy",
    "response_experience_status": "healthy",
    "action_state_status": "healthy"
  },
  "runtime": {
    "status": "healthy",
    "last_error": null,
    "latency_ms": null
  },
  "providers": [
    {
      "provider_id": "feishu_approval",
      "status": "healthy",
      "last_checked_at": "",
      "last_error": null
    }
  ],
  "permission": {
    "status": "healthy",
    "missing_scope_count": 0
  },
  "result_context": {
    "status": "healthy",
    "missing_company_id_count": 0
  },
  "response_experience": {
    "status": "healthy",
    "slow_response_count": 0
  },
  "follow_up": {
    "status": "healthy",
    "pending_follow_up_count": 0
  },
  "action_state": {
    "status": "healthy",
    "failed_action_count": 0,
    "waiting_confirmation_count": 0
  }
}
```

字段说明：

- Diagnostics 可以显示 Provider 健康，但不按 Domain 分组。
- Diagnostics 可以显示 Action State，但不展示 Capability 清册。
- Diagnostics 可以显示错误摘要，但不替代 System Logs。

禁止字段：

```text
domain_id
capability_id
skill_id
business_domain_status
feishu_product_status_group
```

## 7. Source Mapping

| Source | Target Payload | Notes |
| --- | --- | --- |
| `docs/CAPABILITY_REGISTRY_MODEL.md` | Catalog / Skill Registry | 静态冻结模型来源 |
| `RuntimeCapability` | Skill Registry / Governance | 不进入 Catalog |
| `SkillAtomicCapability` | Skill Registry / Governance | 需补 domain 和 capability_id |
| Tool Config | Governance | 只作为 Provider/Skill 风险输入 |
| Provider Health | Diagnostics / Governance | 健康进 Diagnostics，缺口进 Governance |
| Resource Sync Status | Diagnostics / Governance | 运行异常进 Diagnostics，治理动作进 Governance |
| Governance Actions | Governance | 必须转成 Finding |
| System Logs | Diagnostics | 只做运行排查 |

## 8. Builder Boundary

建议后续实现只读 Builder：

```text
CapabilityRegistryPayloadBuilder
```

输入：

```text
Static Capability Registry
RuntimeCapability
SkillAtomicCapability
Tool Config
Provider Health
Resource Sync Status
Governance Actions
System Logs Summary
```

输出：

```text
CapabilityRegistryPayload
```

Builder 不做：

- Runtime 执行。
- Tool 调用。
- Provider 调用。
- AI 分析。
- 数据同步。
- 页面渲染。
- 权限扩权。

## 9. API Boundary

未来 API 可以拆成：

```text
GET /api/v5/capability-registry/payload
GET /api/v5/capability-registry/catalog
GET /api/v5/capability-registry/skills
GET /api/v5/capability-registry/governance
GET /api/v5/diagnostics/runtime
```

规则：

- Catalog / Skills / Governance 属于 Capability Registry。
- Diagnostics 不属于 Capability Registry 分类树。
- `payload` 可以作为后台一次性加载入口。
- 页面最终可以按子 Payload 拆分请求。

## 10. Acceptance Criteria

- Catalog Payload 只包含 Domain 和 Capability。
- Skill Registry Payload 只包含 Capability 和 Skill。
- Governance Payload 只包含 Finding。
- Diagnostics Payload 不包含 Domain / Capability / Skill。
- 飞书产品名只作为 Provider 或 Provider Mapping 出现。
- 四个页面不共享临时拼装字段。
- 不新增数据库表。
- 不实现 API。
- 不改页面代码。

## 11. Freeze Decision

Capability Registry Payload V1 冻结为：

```text
Root Payload
-> catalog_payload
-> skill_registry_payload
-> governance_payload
-> diagnostics_payload
```

下一阶段建议：

```text
Capability Registry Builder Implementation
```

但第一步只实现只读 Builder 和合同测试，不做 UI 迁移。
