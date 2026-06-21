# Page Responsibility Audit

本文档评审当前 V5 页面内容与目标职责的差异。

目标页面职责：

```text
能力目录 = Domain -> Capability
能力清册 = Capability -> Skill
治理中心 = Capability -> Skill -> Provider
系统诊断 = Runtime Health
```

本阶段只做评审，不编码、不改页面、不改 API。

## 1. Current Observations

当前页面和服务中存在几类混用：

- `app/static/console/index.html` 的系统设置资源区同时承载公司资源、发现、登记、同步、治理动作、监控。
- `app/static/console/app.js` 的 Operating Center 同时展示资源数量、同步异常、待治理、工具状态、入口状态。
- `app/services/runtime_v5/capabilities.py` 中 `RuntimeCapability` 和 `SkillAtomicCapability` 是清册基础，但仍按 `source` 或飞书产品来源组织，例如 `approval`、`task`、`calendar`、`mail`、`docs`、`base`。
- `resource_monitoring`、`sync_status`、`governance_actions` 偏运行和资源同步，不应直接进入能力目录。
- `tools` 配置偏旧 Tool/Provider 管理，适合治理或兼容视图，不适合业务用户能力目录。

结论：

当前不是缺页面，而是页面职责边界需要重新归位。

## 2. Page Responsibility Matrix

| Page | Target Responsibility | Target Object | Current Content | Gap |
| --- | --- | --- | --- | --- |
| 能力目录 | 面向业务用户，说明数字参谋能做什么 | Domain -> Capability | 目前能力相关信息分散在 RuntimeCapability、SkillAtomicCapability、Tool family、资源同步策略中 | 当前没有纯业务用户视角的 Capability Catalog |
| 能力清册 | 面向管理员和开发者，说明系统内部有哪些原子能力 | Capability -> Skill | SkillAtomicCapability 已存在，但按 source/skill/provider 混合组织 | 缺 `capability_id`、Domain 映射和统一 Skill 状态 |
| 治理中心 | 判断能力是否安全、完整、可上线 | Capability -> Skill -> Provider Finding | 当前治理动作主要来自资源同步、访问决策、授权建议 | 缺 Capability/Skill/Provider 三层治理问题 |
| 系统诊断 | 判断 Runtime 是否健康 | Runtime Health | 当前监控包含资源同步状态、工具状态、入口状态、同步异常 | 需要剥离业务分类，只保留运行健康 |

## 3. Data Items To Migrate

### To Capability Catalog

迁移或新增：

- Domain。
- Capability ID。
- Capability 名称。
- Capability 描述。
- `user_visible`。
- `lifecycle_status`。
- `supported_surfaces`。
- 业务价值说明。

来源：

- `docs/CAPABILITY_REGISTRY_MODEL.md` 的 Domain -> Capability Mapping。
- 未来 Capability Registry Builder。

不直接来源：

- `RuntimeCapability.route_path`。
- Feishu endpoint。
- Tool provider config。
- resource sync status。

### To Skill Registry

迁移：

- `SkillAtomicCapability.source`。
- `SkillAtomicCapability.operation`。
- `SkillAtomicCapability.skill`。
- `SkillAtomicCapability.question_type`。
- `SkillAtomicCapability.execution_identity`。
- `SkillAtomicCapability.risk_level`。
- `SkillAtomicCapability.registered`。
- `SkillAtomicCapability.exposed`。
- `SkillAtomicCapability.requires_confirmation`。
- `SkillAtomicCapability.label`。
- `SkillAtomicCapability.reason`。

需要补齐：

- `domain`。
- `capability_id`。
- `skill_type`。
- `runtime_supported`。
- `receipt_supported`。
- `provider_bindings`。

### To Governance Center

迁移：

- `governance_actions` 中与资源接入、安全、权限、同步阻断相关的项目。
- Tool 配置中 Provider 不兼容、写能力缺确认、禁用状态等风险项。
- Resource monitoring 中 Provider 异常、权限异常、同步失败项。
- Runtime capability path maturity 中 legacy adapter / manual path 项。

需要转成 Finding：

```text
finding_id
scope: capability | skill | provider
severity: P0 | P1 | P2 | P3
domain
capability_id
skill_id
provider_id
title
message
recommendation
status
```

### To System Diagnostics

迁移：

- Runtime 可用性。
- Provider health。
- Permission health。
- Result Context health。
- Response Experience。
- Follow-up。
- Action State。
- 最近错误。
- 延迟和超时。

来源：

- `runtime_v5/diagnostics.py`。
- `runtime_v5/bot_diagnostics.py`。
- resource monitoring 中纯运行健康部分。
- system logs。

## 4. Data Items To Delete Or Demote

### Capability Catalog Must Not Show

- Skill ID。
- Provider name。
- CLI / MCP / API。
- Feishu endpoint。
- route_path。
- runtime strategy。
- confirmation / receipt technical detail。
- resource sync status。
- governance_actions。
- system logs。

### Skill Registry Must Not Show

- Resource sync preview。
- Company onboarding。
- WorkEvent 列表。
- 企业经营空间指标。
- 业务用户营销式说明。
- Runtime health logs。

### Governance Center Must Not Show

- 普通资源列表。
- 普通同步状态列表。
- 业务对象列表，例如审批、任务、会议。
- Runtime 日志详情。
- 能力目录展示文案。

治理中心只展示可处理的问题。

### System Diagnostics Must Not Show

- Domain 统计。
- Capability 目录。
- Skill 清册。
- 业务域健康度，例如 Process 健康、Workspace 健康。
- 业务治理建议。
- 飞书产品分类统计。

## 5. Capability Registry Design

目标只读结构：

```text
CapabilityRegistry
- domains
- capabilities
- skills
- provider_bindings
- governance_findings
```

消费关系：

```text
Capability Catalog:
Domain -> Capability

Skill Registry:
Capability -> Skill

Governance Center:
Capability -> Skill -> ProviderBinding -> Finding

System Diagnostics:
RuntimeHealth
```

第一阶段建议用 Registry Builder，不立刻建表：

```text
docs/CAPABILITY_REGISTRY_MODEL.md
+ RuntimeCapability
+ SkillAtomicCapability
+ Tool config
+ Provider status
-> CapabilityRegistryPayload
```

Builder 输出应显式区分：

- `catalog_view`：业务目录。
- `skill_registry_view`：原子能力清册。
- `governance_view`：治理问题。
- `diagnostics_view`：运行健康。

注意：

`diagnostics_view` 不应依赖业务 Domain 分类，只能引用 Runtime / Provider / Permission / Result Context / Action State。

## 6. Current Data Mapping

| Current Source | Keep | Target Page | Notes |
| --- | --- | --- | --- |
| `RuntimeCapability.strategy` | Yes | Skill Registry / Governance | 作为历史 runtime strategy，不直接进入目录 |
| `RuntimeCapability.source` | Yes | Provider Binding | 当前为飞书产品来源，需映射到 Provider |
| `RuntimeCapability.operation` | Yes | Skill Registry | 可作为 operation 候选 |
| `RuntimeCapability.route_path` | Demote | Governance / Diagnostics | 只用于迁移风险和运行排查 |
| `SkillAtomicCapability.skill` | Yes | Skill Registry | 需要变成 Skill ID 或 Provider Skill 类型 |
| `SkillAtomicCapability.risk_level` | Yes | Skill Registry / Governance | 高风险治理输入 |
| `SkillAtomicCapability.exposed` | Yes | Skill Registry | 表示开放状态，不等于业务目录可见 |
| `Tool config` | Yes | Governance | 不进入能力目录 |
| `resource sync status` | Split | Governance / Diagnostics | 阻断项进治理，运行状态进诊断 |
| `governance_actions` | Yes | Governance | 需要映射 severity 和 scope |
| `monitoring stats` | Split | Diagnostics / Governance | 运行异常进诊断，能力缺口进治理 |
| `operating center total_counts` | Demote | Overview | 不属于四个目标页核心结构 |

## 7. Final Page Structure

### Capability Catalog

结构：

```text
Domain Tabs
-> Capability List
   - Capability name
   - Description
   - Status
   - Supported surfaces
   - Business value
```

页面只回答：

```text
数字参谋能做什么？
```

### Skill Registry

结构：

```text
Capability Filter
-> Skill Table
   - Skill ID
   - Domain
   - Capability
   - Skill Type
   - Risk Level
   - Status
   - Confirmation Required
   - Runtime Supported
   - Receipt Supported
   - Provider Count
```

页面只回答：

```text
系统内部有哪些原子能力？
```

### Governance Center

结构：

```text
Governance Summary
P0 / P1 / P2 / P3
-> Capability Findings
-> Skill Findings
-> Provider Findings
```

页面只回答：

```text
哪些能力不安全、不完整、不可上线？
```

### System Diagnostics

结构：

```text
Runtime
Provider
Permission
Result Context
Response Experience
Follow-up
Action State
System Logs
```

页面只回答：

```text
系统现在能否正常运行？
```

## 8. Migration Plan

### Step 1: Registry Payload

建立只读 Capability Registry Payload。

不改数据库。

不改页面。

验收：

- 可以输出 Domain -> Capability。
- 可以输出 Capability -> Skill。
- 可以输出 Skill -> ProviderBinding。
- 可以输出 Governance Finding。

### Step 2: Page Data Boundary

为四个页面分别定义数据输入：

- Capability Catalog Payload。
- Skill Registry Payload。
- Governance Center Payload。
- System Diagnostics Payload。

验收：

- 页面之间不共享临时拼装字段。
- Diagnostics 不读取 Domain 分类。
- Catalog 不读取 Skill / Provider 明细。

### Step 3: Minimal UI Migration

先重排现有 console 信息，不新增复杂交互。

验收：

- 能力目录只展示 Domain -> Capability。
- 能力清册只展示 Capability -> Skill。
- 治理中心只展示 Finding。
- 系统诊断只展示 Runtime Health。

## 9. Acceptance Criteria

- 四个页面职责互不重叠。
- Capability Catalog 不展示技术实现。
- Skill Registry 不展示运行日志。
- Governance Center 不展示普通资源清单。
- System Diagnostics 不展示业务域统计。
- 所有飞书产品名只出现在 Provider 或 Provider Mapping。
- Task / Approval / Calendar 不作为页面分类根节点。

## 10. Freeze Decision

页面职责冻结为：

```text
能力目录 = Domain -> Capability
能力清册 = Capability -> Skill
治理中心 = Capability -> Skill -> Provider
系统诊断 = Runtime Health
```

下一阶段建议：

```text
Capability Registry Payload Design
```

目标：

先实现只读 Registry Payload，再做 UI 最小迁移。
