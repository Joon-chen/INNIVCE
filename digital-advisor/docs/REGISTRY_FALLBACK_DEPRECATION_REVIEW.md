# Registry Fallback Deprecation Review

Capability Registry 项目已验收通过。

本文档只回答：旧数据源如何退役。

Registry 不再扩展新概念，进入维护状态。

## 1. Review Goal

识别旧数据源的去留：

- 可立即废弃。
- 保留一个版本周期。
- 长期保留。

这里的废弃指：

```text
不再作为四个页面的主事实来源
```

不等于删除 API。

Capability Registry 的主事实来源为：

```text
GET /api/v5/capability-registry
```

其 Root Payload 包含：

- `catalog_payload`
- `skill_registry_payload`
- `governance_payload`
- `diagnostics_payload`
- `registry_health`

## 2. Current Page Sources

| Page | Registry Source | Legacy Fallback |
| --- | --- | --- |
| 能力目录 | `catalog_payload` | OS overview / legacy tool board data |
| 能力清册 | `skill_registry_payload` | `/api/v5/tools` list rows |
| 治理中心 | `governance_payload` | `/api/v5/resources/sync-status` governance actions |
| 系统诊断 | `diagnostics_payload` | `/api/v5/system/logs` rows |

四个页面已由 Registry Payload 驱动。

Fallback 仍保留，用于 Registry 临时不可用时保护管理后台。

## 3. 可立即废弃

以下内容可以立即废弃其“页面主数据源”身份。

| Legacy Source | 原用途 | 废弃范围 | 保留说明 |
| --- | --- | --- | --- |
| OS overview / tool board 汇总 | 能力目录卡片来源 | 不再作为能力目录主数据源 | 能力目录只认 `catalog_payload` |
| `/api/v5/tools` list rows | 能力清册表格来源 | 不再作为 Skill Registry 主数据源 | `/api/v5/tools` 仍保留为 Tool 配置与执行入口 |
| `/api/v5/resources/sync-status.governance_actions` | 治理中心问题来源 | 不再作为 Capability / Skill / Provider 治理主数据源 | 资源同步治理应回归 Resource Governance |
| `/api/v5/system/logs` rows | 系统诊断健康来源 | 不再作为 Runtime Health 主数据源 | 系统日志仍保留为审计和 drilldown |

废弃动作：

- 文档层标记为 fallback-only。
- 后续代码中不再新增基于这些旧数据源的页面字段。
- 新字段必须先进入 Registry Payload，再进入页面。

## 4. 保留一个版本周期

以下 fallback 保留一个版本周期。

| Fallback | 保留原因 | 移除条件 |
| --- | --- | --- |
| 能力目录 legacy fallback | 防止 Registry API 异常导致控制台空白 | 连续一个版本周期 `catalog_payload` 稳定 |
| 能力清册 legacy fallback | `/api/v5/tools` 仍承载工具配置 UI，需要避免切换期断层 | Skill Registry 页面不再依赖旧 tool row 字段 |
| 治理中心 legacy fallback | 资源同步治理还没有拆出独立页面职责 | Capability Governance 与 Resource Governance 明确分层 |
| 系统诊断 legacy fallback | 日志 drilldown 对排查仍有价值 | Diagnostics 页面区分 Health Summary 与 Logs Drilldown |
| `capability-registry-diff` 日志 | 观察 Registry 与旧源差异 | fallback 移除后再关闭或降级 |

版本周期内要求：

- fallback 只能被动使用。
- fallback 不得新增字段。
- fallback 不得反向影响 Registry Payload。
- diff 日志用于发现覆盖缺口，不用于扩展旧源。

## 5. 长期保留

以下旧 API 长期保留，但职责重新定位。

| Source | 长期职责 | 不再承担 |
| --- | --- | --- |
| `/api/v5/tools` | Tool 配置、启停、策略、人工执行、批量操作 | Skill Registry 事实来源 |
| `/api/v5/tools/executions` | Tool 执行记录和审计 | Capability Health 事实来源 |
| `/api/v5/system/logs` | Runtime / API / Provider 日志 drilldown | 系统诊断主健康模型 |
| `/api/v5/resources/sync-status` | 资源同步状态、资源覆盖治理 | Capability Governance 主模型 |
| `/api/v5/resources/monitoring` | 数据资源监控和采集质量 | Registry Health 主模型 |

长期保留原则：

- Operational API 保留。
- Registry 页面事实来源统一。
- 资源治理、日志审计、工具配置不混入 Capability Registry。

## 6. Deprecation Matrix

| Category | Decision | Items |
| --- | --- | --- |
| 可立即废弃 | 废弃页面主数据源身份 | legacy tool board、legacy tool rows、resource governance actions、system log health rows |
| 保留一个版本周期 | 保留 fallback 分支和 diff 日志 | catalog / skills / governance / diagnostics fallback |
| 长期保留 | 保留为运维、审计、资源治理 API | tools、tool executions、system logs、resource sync、resource monitoring |

## 7. Guardrails

Registry 进入维护状态后：

- 不新增 Registry 设计文档。
- 不新增 Registry 页面。
- 不新增 Shadow Panel。
- 不扩展 Domain。
- 不用旧源补新字段。
- 不把 Resource Governance 混回 Capability Governance。
- 不把 System Logs 混回 Runtime Health。

新 Capability / Skill / Provider 仍必须通过 Lifecycle Guard：

```text
Domain
-> Capability
-> Skill
-> Provider
```

## 8. Next Step

Registry 后续只做维护。

主线恢复到：

```text
Task Cognitive Sample Design
```

目标是验证 Workspace 域下的 Task 能否复用：

```text
WorkEvent
-> Evidence
-> Snapshot
-> Insight
-> Runtime Action
```
