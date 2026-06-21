# Approval Insight Sample

本文档定义审批业务的 Insight 样板。

Approval Insight 是 Recommendation，不负责执行，不规划 approve / reject / transfer / add_sign。

## 1. Position

审批认知链路：

```text
Approval Raw Data
-> WorkEvent
-> Approval Evidence
-> Approval Snapshot
-> MemoryCandidate
-> Approval Insight
-> Runtime Action
```

Approval Insight 的职责：

- 把 Evidence、Snapshot、MemoryCandidate 转成审批人能理解的建议。
- 解释为什么需要关注。
- 说明不处理的影响。
- 引用支撑它的 Evidence / Memory。

Approval Insight 不负责：

- 同意审批。
- 拒绝审批。
- 转交。
- 加签。
- 生成 RuntimeActionInput。
- 生成 action_candidates。

## 2. Input Sources

Approval Insight V0 只消费：

- Approval Evidence。
- Approval Snapshot。
- MemoryCandidate / Memory。

禁止直接消费：

- 飞书 raw form。
- OCR 原文。
- widget id。
- Provider response。
- Portal UI state。

## 3. Insight Type Mapping

Approval V0 使用以下 Insight Type：

```text
decision_support
missing_information
conflict
risk
pattern
```

映射规则：

- `decision_support`：证据基本完整，帮助审批人确认当前判断。
- `missing_information`：缺发票、缺费用明细、缺事由、缺附件摘要。
- `conflict`：申请金额与附件金额不一致，费用归属与票据抬头不一致，表单与附件互相冲突。
- `risk`：疑似重复报销、超预算、非公司业务、金额异常且缺依据。
- `pattern`：同一人、供应商、部门反复出现类似缺失或异常。

## 4. Severity Mapping

Approval Severity V0：

```text
info
low
medium
high
critical
```

规则：

- `info`：证据完整，仅辅助确认。
- `low`：轻微缺失，不影响基础判断。
- `medium`：需要审批人核对后再决定。
- `high`：存在可能影响审批结论的冲突或风险。
- `critical`：疑似重大合规、资金或舞弊风险。

Severity 不等于审批动作。

例如：

- `severity=medium` 不等于 reject。
- `severity=high` 不等于 auto block。
- 是否 approve / reject 仍由人确认，并通过 Runtime Action 执行。

## 5. Sample: Evidence Conflict

场景：

- 申请金额：8902.33 元。
- 附件可识别金额：52279.23 元。
- Evidence 判断：附件金额高于申请金额。

Insight：

```json
{
  "id": "insight-approval-202606150009-conflict",
  "company_id": "company-1",
  "insight_type": "conflict",
  "severity": "medium",
  "object_type": "approval",
  "object_id": "202606150009",
  "title": "附件金额与本次申请金额不一致",
  "recommendation": "先核对附件是否包含历史、重复或非本次报销材料，再决定是否审批。",
  "reasoning": "申请金额为 8902.33 元，附件可识别金额约 52279.23 元，明显高于本次申请金额。",
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

## 6. Sample: Missing Information

场景：

- 申请金额存在。
- 附件摘要读取失败。
- 费用明细表未成功还原。

Insight：

```json
{
  "id": "insight-approval-missing-info",
  "company_id": "company-1",
  "insight_type": "missing_information",
  "severity": "medium",
  "object_type": "approval",
  "object_id": "approval-1",
  "title": "关键审批依据不完整",
  "recommendation": "请先要求申请人补充清晰费用明细和对应票据，再继续审批判断。",
  "reasoning": "Evidence 显示费用明细未成功还原，附件摘要也不足以核对费用真实性。",
  "impact": "缺少明细和票据依据时，审批人无法判断费用是否属于公司业务支出。",
  "evidence_refs": [
    {
      "evidence_id": "approval-evidence-approval-1",
      "evidence_type": "approval_expense",
      "object_type": "approval",
      "object_id": "approval-1",
      "reason": "Evidence quality 为 partial，缺少费用明细行和可核对附件摘要。"
    }
  ],
  "memory_refs": [],
  "confidence": 0.76,
  "status": "active",
  "created_at": "2026-06-21T00:00:00Z"
}
```

## 7. Sample: Pattern From Memory

场景：

- 同一供应商多次出现票据缺失。
- 当前审批也缺少发票金额或合同编号。

Insight：

```json
{
  "id": "insight-approval-supplier-pattern",
  "company_id": "company-1",
  "insight_type": "pattern",
  "severity": "high",
  "object_type": "approval",
  "object_id": "approval-2",
  "title": "同类票据缺失重复出现",
  "recommendation": "建议审批前复核该供应商近期付款和票据完整性。",
  "reasoning": "当前审批缺少关键票据信息，历史记忆候选显示同一供应商多次出现类似缺失。",
  "impact": "如果重复缺失未被识别，可能导致供应商付款风险持续累积。",
  "evidence_refs": [
    {
      "evidence_id": "approval-evidence-approval-2",
      "evidence_type": "approval_expense",
      "object_type": "approval",
      "object_id": "approval-2",
      "reason": "当前 Evidence 显示缺少发票金额或票据明细。"
    }
  ],
  "memory_refs": [
    {
      "memory_id": "memory-candidate-supplier-missing-invoice",
      "memory_type": "recurring_missing_invoice",
      "object_type": "supplier",
      "object_id": "supplier-a",
      "reason": "该供应商历史上多次出现付款材料缺失。"
    }
  ],
  "confidence": 0.71,
  "status": "active",
  "created_at": "2026-06-21T00:00:00Z"
}
```

## 8. Renderer Boundary

通用 Insight 展示边界见 `docs/INSIGHT_RENDERER_BOUNDARY.md`。

Approval Insight 在 Interaction 中只展示：

- title
- severity
- recommendation
- reasoning
- impact
- evidence_refs 摘要
- memory_refs 摘要

不展示：

- RuntimeActionInput。
- approve / reject 操作结构。
- Tool / Provider 名称。
- raw form。
- OCR 原文。
- widget id。

Insight 展示可以建议“先核对附件范围”，但不能直接生成“同意/拒绝”的动作。

## 9. Acceptance

Approval Insight Sample V0 验收标准：

- 每个 Insight 都符合 `Insight Contract V0`。
- 每个 Insight 至少引用 Evidence，除非明确是纯 Memory pattern。
- Insight 不包含 action_candidates。
- Insight 不包含 Tool / Provider / RuntimeActionInput。
- Approval Insight 只解释 Recommendation，不执行审批。
