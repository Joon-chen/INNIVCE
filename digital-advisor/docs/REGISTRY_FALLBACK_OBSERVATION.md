# Registry Fallback Observation

本文档定义 Registry UI V1 冻结后的 fallback 观察期。

## 阶段结论

当前进入：

```text
Registry Fallback Observation Phase
```

目标不是删除旧数据源。

目标是观察：

- Registry Payload 是否稳定覆盖四个页面。
- fallback 是否仍被真实页面依赖。
- `capability-registry-diff` 是否暴露缺口。
- Registry Health 是否持续 healthy。

## 当前主数据源

统一入口：

```text
GET /api/v5/capability-registry
```

页面映射：

| 页面 | 主数据源 | fallback |
| --- | --- | --- |
| 能力目录 | `catalog_payload` | 原 Tool family board |
| 能力清册 | `skill_registry_payload` | `/api/v5/tools` |
| 治理中心 | `governance_payload` | resource sync governance actions |
| 系统诊断 | `diagnostics_payload` | `/api/v5/system/logs` |

## 观察指标

### 1. Registry Health

必须持续满足：

```text
registry_health.status = healthy
missing_capability = []
missing_skill = []
missing_provider = []
orphan_skills = []
orphan_provider = []
```

如果出现 `needs_attention` 或 `degraded`，不得进入 fallback 移除。

### 2. Payload Availability

四个 payload 必须同时存在：

```text
catalog_payload
skill_registry_payload
governance_payload
diagnostics_payload
```

任一 payload 缺失时，fallback 保留。

### 3. Page Coverage

四个页面必须均由 Registry Payload 优先渲染：

```text
能力目录 -> catalog_payload
能力清册 -> skill_registry_payload
治理中心 -> governance_payload
系统诊断 -> diagnostics_payload
```

观察期内不允许新增绕过 Registry 的新主数据源。

### 4. Diff Log

前端会输出：

```text
[capability-registry-diff]
```

需要观察的 scope：

```text
catalog_payload
skill_registry_payload
governance_payload
diagnostics_payload
```

如果 diff 日志显示 Registry Payload 缺关键字段，fallback 保留。

### 5. User Experience

观察重点：

- 页面是否可正常加载。
- 表格是否为空但符合真实状态。
- 管理员是否能理解当前页面含义。
- 点击行后的详情是否仍能显示。
- 页面是否误把业务治理动作混入能力治理。
- 系统诊断是否保持 Runtime Health 视角。

## 观察期禁止范围

本阶段禁止：

- 删除 fallback。
- 新增 Registry 设计。
- 新增 Registry 页面。
- 新增 Shadow Panel。
- 重构 UI 布局。
- 重构交互。
- 迁移业务逻辑。
- 新增数据库表。
- 新增第二套 Runtime。

## 观察验收

观察期可结束的标准：

- Registry Health 连续保持 healthy。
- 四个页面均正常加载。
- 四类 `capability-registry-diff` 均出现且无阻塞缺口。
- 治理中心在 `findings = 0` 时显示空状态符合预期。
- 系统诊断只展示 Runtime Health，不展示业务域统计。
- 无管理员反馈必须依赖 fallback 才能完成当前页面任务。

## 进入下一阶段条件

满足观察验收后，进入：

```text
Registry Fallback Deprecation Review
```

届时只评审是否移除 fallback。

不是直接删除。

## 当前建议

保留 fallback 至少一个观察周期。

观察期间只允许：

- 修复 Registry Payload 缺字段。
- 修复 adapter 映射错误。
- 修复页面空状态误导。
- 修复 diff 日志缺失。

不允许继续扩展 Registry 概念。

## 观察记录

### 2026-06-21 云端控制台观察

环境：

```text
https://ai.gaustek.com/console
company = 固势
```

观察结果：

| 页面 | 观察结果 | 结论 |
| --- | --- | --- |
| 能力目录 | 显示 7 个 Domain：People / Communication / Workspace / Process / Knowledge / Business / Intelligence | `catalog_payload` 已接管 |
| 能力清册 | 显示 `skill_count = 84`，`missing_provider_count = 0` | `skill_registry_payload` 已接管 |
| 治理中心 | `governance_source = governance_payload`，`governance_actions = []` | `governance_payload` 已接管，空状态符合 healthy Registry |
| 系统诊断 | `diagnostics_source = diagnostics_payload`，7 个诊断维度均为 healthy | `diagnostics_payload` 已接管 |

Console diff 已观察到：

```text
catalog_payload
skill_registry_payload
governance_payload
diagnostics_payload
```

发现的问题：

- 能力目录已显示 Domain，但标题仍为 `9 个 Tool`，容易误导。

处理：

- 将标题改为 `能力域`。
- 未改变布局。
- 未改变交互。
- 未移除 fallback。

观察结论：

Registry Payload 已覆盖四个页面。

fallback 当前仍建议保留。
