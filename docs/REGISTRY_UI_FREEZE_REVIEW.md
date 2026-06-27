# Registry UI Freeze Review

本文档验证 Minimal UI Migration Phase 是否可以冻结。

## 结论

Registry UI V1 可以冻结。

当前四个页面已经可以由统一 Registry API 驱动：

```text
GET /api/v5/capability-registry
```

对应关系：

```text
能力目录 -> catalog_payload
能力清册 -> skill_registry_payload
治理中心 -> governance_payload
系统诊断 -> diagnostics_payload
```

旧数据源仍保留为 fallback，不在本阶段移除。

## 冻结范围

本次冻结的是 UI 数据源边界，不是 UI 设计重构。

已完成：

- 不改变页面布局。
- 不改变页面交互。
- 不新增页面。
- 不新增数据库。
- 不新增 Registry 设计。
- 不改业务 Runtime。
- 四个页面均通过 Registry Payload 优先渲染。
- 四个页面均保留旧数据源 fallback。
- 已增加 `capability-registry-diff` 日志。

## 页面冻结矩阵

| 页面职责 | Registry Payload | 当前状态 | Fallback | 冻结结论 |
| --- | --- | --- | --- | --- |
| 能力目录 | `catalog_payload` | 已迁移 | 原 Tool family board | 可冻结 |
| 能力清册 | `skill_registry_payload` | 已迁移 | `/api/v5/tools` | 可冻结 |
| 治理中心 | `governance_payload` | 已迁移 | resource sync governance actions | 可冻结 |
| 系统诊断 | `diagnostics_payload` | 已迁移 | `/api/v5/system/logs` | 可冻结 |

## 当前覆盖率

基于云端验证：

```text
registry_health.status = healthy
domains = 7
capabilities = 60
skills = 84
providers = 27
findings = 0
missing_capability = []
missing_skill = []
missing_provider = []
orphan_skills = []
orphan_provider = []
```

Diagnostics summary：

```text
status = healthy
runtime_status = healthy
provider_status = healthy
permission_status = healthy
result_context_status = healthy
response_experience_status = healthy
action_state_status = healthy
```

## 页面行为

### 能力目录

目标：

```text
面向业务用户回答：数字参谋能做什么？
```

当前由 `catalog_payload.domains` 驱动。

展示粒度：

```text
Domain -> Capability
```

旧 Tool family board 仅作为 Registry 不可用时 fallback。

### 能力清册

目标：

```text
面向管理员和开发者回答：系统内部有哪些原子能力？
```

当前由 `skill_registry_payload.capabilities[].skills` 驱动。

展示粒度：

```text
Capability -> Skill -> Provider
```

旧 `/api/v5/tools` 仅作为 Registry 不可用时 fallback。

### 治理中心

目标：

```text
治理 Capability / Skill / Provider 链路。
```

当前由 `governance_payload.findings` 驱动。

Registry healthy 时，治理项为空是正确状态。

旧 resource sync governance actions 不再作为能力治理主数据源，只保留 fallback。

### 系统诊断

目标：

```text
回答系统能否正常运行。
```

当前由 `diagnostics_payload` 驱动。

诊断维度固定为：

```text
Runtime
Provider
Permission
Result Context
Response Experience
Follow-up
Action State
```

系统诊断不展示业务域统计。

旧 `/api/v5/system/logs` 仅作为 Registry 不可用时 fallback。

## Diff 日志

前端已记录：

```text
[capability-registry-diff]
```

覆盖范围：

- `catalog_payload`
- `skill_registry_payload`
- `governance_payload`
- `diagnostics_payload`

用途：

- 对比 Registry Payload 与旧数据源。
- 验证覆盖率。
- 为后续移除 fallback 提供观察依据。

## 剩余技术债

当前保留：

- 旧数据源 fallback。
- 能力清册仍复用原工具管理布局。
- 治理中心仍复用资源治理表格布局。
- 系统诊断仍复用运行日志表格布局。

这些属于 Minimal UI Migration 的有意限制，不是冻结阻塞项。

## 不建议立即做的事

当前不建议立即移除 fallback。

原因：

- Registry UI 刚切换到云端。
- 需要观察真实管理员页面使用情况。
- fallback 可保护线上管理后台。

当前不建议做全量 UI 重构。

原因：

- 数据源边界刚稳定。
- 页面视觉和交互重构应作为独立阶段。

## 冻结标准

Registry UI V1 冻结标准：

- 四个页面均已接入 Registry Payload。
- Registry Health 云端为 healthy。
- Lifecycle Guard 已建立。
- 相关前端契约测试通过。
- 云端控制台可加载新脚本。
- Docker 服务正常运行。

当前均满足。

## 下一阶段建议

进入：

```text
Registry Fallback Observation Phase
```

目标：

- 保留 fallback 一个观察周期。
- 通过 `capability-registry-diff` 观察真实页面覆盖。
- 不新增 Registry 设计。
- 不新增页面。
- 不迁移业务逻辑。

观察稳定后，再进入：

```text
Registry Fallback Deprecation Review
```

届时再决定是否移除旧数据源 fallback。
