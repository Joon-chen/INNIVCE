# Insight Contract V0

本文档定义企业认知系统的输出层。

Insight 是 Recommendation，不负责执行，不规划动作，不生成 Action Candidate。

## 1. Position

Insight 位于：

```text
WorkEvent
-> Evidence
-> Snapshot
-> MemoryCandidate
-> Insight
-> Runtime Action
```

Insight 的职责是把 Evidence、Snapshot 和 MemoryCandidate 转成可解释建议。

Insight 不执行任何动作。所有 Action 仍归 Runtime。

## 2. Insight Model

标准结构：

```json
{
  "id": "",
  "company_id": "",
  "insight_type": "",
  "severity": "",
  "object_type": "",
  "object_id": "",
  "title": "",
  "recommendation": "",
  "reasoning": "",
  "impact": "",
  "evidence_refs": [],
  "memory_refs": [],
  "confidence": 0.0,
  "status": "active",
  "created_at": ""
}
```

字段说明：

- `id`：Insight 唯一标识。
- `company_id`：企业隔离边界，必填。
- `insight_type`：Insight 类型。
- `severity`：严重程度。
- `object_type`：对象类型，例如 approval、task、meeting、mail、customer。
- `object_id`：业务对象 ID。
- `title`：给人的短标题。
- `recommendation`：建议，不是动作。
- `reasoning`：为什么给出该建议。
- `impact`：不处理会影响什么。
- `evidence_refs`：引用的 Evidence。
- `memory_refs`：引用的 MemoryCandidate / Memory。
- `confidence`：建议可信度，0 到 1。
- `status`：active / dismissed / superseded。
- `created_at`：生成时间。

## 3. Insight Type

V0 类型：

```text
risk
missing_information
conflict
follow_up
opportunity
pattern
decision_support
```

含义：

- `risk`：存在业务风险。
- `missing_information`：关键信息缺失。
- `conflict`：证据、状态或历史模式存在冲突。
- `follow_up`：需要后续跟进。
- `opportunity`：存在可推进机会。
- `pattern`：发现重复组织模式。
- `decision_support`：辅助当前决策。

Approval V0 主要使用：

```text
risk
missing_information
conflict
decision_support
```

## 4. Severity

V0 严重程度：

```text
info
low
medium
high
critical
```

使用原则：

- `info`：仅提示，不影响决策。
- `low`：轻微异常或弱建议。
- `medium`：需要人工留意或补充确认。
- `high`：可能影响审批、交付、客户、财务或合规判断。
- `critical`：需要立即处理，可能造成重大风险。

Severity 不等于 Action。

Severity 只影响展示优先级和 Policy 判断输入，不直接触发执行。

## 5. Evidence References

Insight 必须可追溯到 Evidence。

结构：

```json
{
  "evidence_id": "",
  "evidence_type": "",
  "object_type": "",
  "object_id": "",
  "reason": ""
}
```

规则：

- `evidence_refs` 可以为空，但只有在 Insight 明确来自 Memory 时才允许为空。
- 不展示技术噪音。
- 不引用 raw widget、OCR 原文、API 字段名。
- `reason` 说明该 Evidence 为什么支撑这个 Insight。

示例：

```json
{
  "evidence_id": "approval-evidence-1",
  "evidence_type": "approval_expense",
  "object_type": "approval",
  "object_id": "202606150009",
  "reason": "附件可识别金额高于申请金额，说明附件范围与本次申请可能不一致。"
}
```

## 6. Memory References

Insight 可以引用长期记忆候选或正式 Memory。

结构：

```json
{
  "memory_id": "",
  "memory_type": "",
  "object_type": "",
  "object_id": "",
  "reason": ""
}
```

规则：

- Memory Reference 只提供历史模式或组织上下文。
- Memory Reference 不直接决定当前结论。
- 当前判断仍必须回到 Evidence 和 Snapshot。

示例：

```json
{
  "memory_id": "memory-candidate-1",
  "memory_type": "recurring_missing_invoice",
  "object_type": "approval",
  "object_id": "supplier-a",
  "reason": "同一供应商多次出现票据缺失，当前审批需要更谨慎核对。"
}
```

## 7. Approval Example

```json
{
  "id": "insight-approval-202606150009",
  "company_id": "company-1",
  "insight_type": "conflict",
  "severity": "medium",
  "object_type": "approval",
  "object_id": "202606150009",
  "title": "附件金额与申请金额不一致",
  "recommendation": "先核对附件范围是否包含历史或重复材料，再决定是否审批。",
  "reasoning": "申请金额为 8902.33 元，附件可识别金额约 52279.23 元，高于本次申请金额。",
  "impact": "如果附件范围不清，审批人无法确认本次费用是否真实、完整且归属正确。",
  "evidence_refs": [
    {
      "evidence_id": "approval-evidence-202606150009",
      "evidence_type": "approval_expense",
      "object_type": "approval",
      "object_id": "202606150009",
      "reason": "Evidence 识别出申请金额和附件金额存在明显差异。"
    }
  ],
  "memory_refs": [],
  "confidence": 0.82,
  "status": "active",
  "created_at": "2026-06-21T00:00:00Z"
}
```

## 8. Forbidden Scope

Insight Contract V0 禁止：

- Action Engine。
- Action Planner。
- Action Candidate。
- Workflow。
- Runtime 重构。
- Tool 调用。
- Provider 调用。
- 自动执行。

Insight 不能包含：

```json
{
  "action_candidates": [],
  "tool_name": "",
  "provider_operation": "",
  "runtime_action_input": {}
}
```

## 9. Acceptance

V0 验收标准：

- Insight = Recommendation。
- Insight 能引用 Evidence。
- Insight 能引用 MemoryCandidate / Memory。
- Insight 有类型和严重程度。
- Insight 不规划动作。
- Runtime 仍是唯一 Action 执行入口。
