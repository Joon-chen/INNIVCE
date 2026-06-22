# Current Mission

本文档只回答：现在在做什么。

系统最高级规则见 `docs/V5_RUNTIME_CONSTITUTION.md`。

## 当前阶段

```text
Command LLM Intent V0 Acceptance Phase
```

## 当前目标

把 LLM 纳入 V5 Command Engine，但只作为结构化 Intent Candidate 生成器。

当前链路：

```text
User Message
-> Rule Intent Parser
-> LLM Intent Candidate
-> Command Validator
-> IntentResult
-> Planner
-> Policy
-> Runtime
```

已完成：

- `recognize_intent` 仍是 Command 统一入口。
- 高置信规则动作优先，LLM 不覆盖写入/审批/发送类动作。
- LLM 候选必须通过 Validator 才能变成 `IntentResult`。
- Validator 限定已登记 intent、合法 question_type、合法 data_scope。
- 低置信 LLM 候选可在带 `missing_params` 时进入引导式对话。
- Composer 可优先展示 LLM 生成的 clarification prompt。

## 当前禁止范围

- 不让 LLM 直接调用 Tool / Provider / Feishu API。
- 不让 LLM 选择 credential、execution identity 或授权策略。
- 不新增 Runtime Engine / Policy Engine / Cognitive Engine 概念。
- 不新增数据库表。
- 不改 UI 页面。
- 不改 Provider 执行逻辑。
- 不扩大 Task / Calendar 能力范围。

## 当前验收标准

- 泛化自然语言查询可由 LLM Candidate 补足为现有 Runtime intent。
- 低置信且缺参数时进入 clarification，不执行 Provider。
- 未知 intent 被拒绝。
- 低置信且无可追问参数的候选被拒绝。
- Query 不得被 LLM 升级为 Action。
- 高置信规则动作不得被 LLM 覆盖。

## 当前验证

```text
.venv312/bin/python -m pytest tests/test_runtime_v5.py -q
.venv312/bin/python -m py_compile app/services/runtime_v5/llm_intent.py app/services/runtime_v5/intent.py app/services/runtime_v5/composer.py tests/test_runtime_v5.py
```

已知未处理：

- `tests/test_v5_architecture.py` 当前存在既有 Task write-service guard 失败：
  `app/services/runtime_v5/feishu_resource_providers.py:1178: .create_task(`。
  该问题属于 Task Provider 写入边界清理，不属于 Command LLM 本阶段。

## 下一步计划

```text
Unified Policy Engine V0 Implementation Planning
```

- 将 Query 身份选择、Scope 允许范围、实时数据与认知数据裁剪统一进 Policy。
- 先选择 Workspace 作为第一条 Policy 打通样板。
- 再回到企业级 Bot/Tenant 查询能力接入。
