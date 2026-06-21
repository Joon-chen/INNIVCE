# Current Mission

本文档只回答：现在在做什么。

系统最高级规则见 `docs/V5_RUNTIME_CONSTITUTION.md`。
V5 能力分类见 `docs/V5_CAPABILITY_TAXONOMY.md`。
Capability Registry 模型见 `docs/CAPABILITY_REGISTRY_MODEL.md`。
页面职责评审见 `docs/PAGE_RESPONSIBILITY_AUDIT.md`。
Capability Registry Payload 设计见 `docs/CAPABILITY_REGISTRY_PAYLOAD_DESIGN.md`。
Capability Registry Read API 评审见 `docs/CAPABILITY_REGISTRY_READ_API_REVIEW.md`。

## 当前阶段

当前进入：

```text
Capability Registry Read API Implementation Phase
```

背景：

V5 业务域已冻结为：

```text
People
Communication
Workspace
Process
Knowledge
Business
Intelligence
```

Capability Registry Read API Review 已通过。

当前实现内部只读 Registry API，验证它是否可以成为四个页面唯一事实来源。

## 当前目标

实现只读 API：

```text
GET /api/v5/capability-registry
```

关键决策：

- 仅供内部验证使用。
- 暂不切换页面。
- 暂不开放普通用户访问。
- API 只负责 transport。

本阶段只做只读 API 和合同测试。

## 当前禁止范围

本阶段不要做：

- 新数据库表。
- Event Bus / Workflow / Memory / Evidence / Snapshot / Insight Engine。
- 第二套 Runtime。
- Task 业务代码 / Tool / Runtime Action / UI。
- 页面迁移实现。
- 数据库迁移。
- 拆分 catalog / skills / governance / diagnostics endpoint。
- Domain 调整。
- UI 重构。
- 业务逻辑改造。

## 当前验收标准

- 实现 `GET /api/v5/capability-registry`。
- Root Payload 包含 `registry_version`。
- Root Payload 包含 `generated_at`。
- Root Payload 包含 `registry_health`。
- API Contract Test 覆盖四个子 Payload。
- 不修改页面代码。
- 不修改数据库。
- 不修改业务逻辑。

## 下一步计划

1. 完成 Read API 和合同测试。
2. 输出 Registry API 示例响应、Registry Health 示例、四页面覆盖率。
3. 再进入 Registry API Shadow Verification，暂不做 UI 迁移。
