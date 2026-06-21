# Current Mission

本文档只回答：现在在做什么。

系统最高级规则见 `docs/V5_RUNTIME_CONSTITUTION.md`。
V5 能力分类见 `docs/V5_CAPABILITY_TAXONOMY.md`。
Capability Registry 模型见 `docs/CAPABILITY_REGISTRY_MODEL.md`。
页面职责评审见 `docs/PAGE_RESPONSIBILITY_AUDIT.md`。
Capability Registry Payload 设计见 `docs/CAPABILITY_REGISTRY_PAYLOAD_DESIGN.md`。
Capability Registry Read API 评审见 `docs/CAPABILITY_REGISTRY_READ_API_REVIEW.md`。
Capability Registry 影子校验见 `docs/CAPABILITY_REGISTRY_SHADOW_VERIFICATION.md`。
Skill Registry 治理计划见 `docs/SKILL_REGISTRY_CLEANUP_PLAN.md`。

## 当前阶段

当前进入：

```text
Registry Cleanup Phase
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

Registry Shadow Verification 已通过。

当前治理 `registry_health.status = needs_attention` 的根因。

## 当前目标

处理 `missing_skill` 中的全部项目：

```text
Workspace Skill
Communication Skill
Knowledge Skill
Business Skill
Intelligence Skill
```

关键决策：

- 不新增 Registry 设计。
- 不新增 Registry 页面。
- 不新增 Shadow Panel。
- 只处理 Capability -> Skill -> Provider 链路完整性。

本阶段优先完成 Registry 治理。

## 当前禁止范围

本阶段不要做：

- 新数据库表。
- Event Bus / Workflow / Memory / Evidence / Snapshot / Insight Engine。
- 第二套 Runtime。
- Task 业务代码 / Tool / Runtime Action / UI。
- 页面迁移实现。
- 数据库迁移。
- Domain 调整。
- UI 重构。
- 页面迁移。
- Shadow Panel。
- 业务逻辑改造。

## 当前验收标准

- 输出 Skill Registry Cleanup Plan。
- `missing_skill` 全部归类。
- 每项给出保留、合并、废弃或重命名判断。
- 重新运行 `registry_health`。
- 输出 before / after 统计变化。
- `registry_health.status` 从 `needs_attention` 变为 `healthy`。
- 不修改页面代码。
- 不修改数据库。
- 不修改业务逻辑。

## 下一步计划

1. 完成 Registry Cleanup。
2. 冻结 Registry 健康基线。
3. 增加守护测试，防止新增 Runtime strategy 后漏登记 Skill。
