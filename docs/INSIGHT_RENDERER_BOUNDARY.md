# Insight Renderer Boundary

本文档定义 Insight 在 Interaction 层如何展示。

本阶段只定义展示边界，不实现 Insight Engine，不实现 Renderer Registry，不重构 Bot / Portal / SidePanel。

## 1. Position

Insight Renderer 位于：

```text
Insight
-> InteractionPayload
-> Bot / Detail View / Management Portal
```

Insight Renderer 只消费 Insight。

Insight Renderer 不消费：

- Raw OA Data。
- Provider response。
- Tool output。
- RuntimeActionInput。
- Portal UI state。

## 2. Render-Only Rule

Insight Renderer 只展示 Recommendation。

允许展示：

- title
- severity
- insight_type
- recommendation
- reasoning
- impact
- evidence_refs summary
- memory_refs summary
- confidence label
- status

禁止展示或生成：

- action_candidates
- approve / reject / transfer / add_sign
- RuntimeActionInput
- Tool / Provider name
- provider_operation
- workflow state
- raw form
- OCR 原文
- widget id
- open_id 作为姓名

Renderer 不能把 Insight 自动解释成动作。

例如：

```text
good: 建议先核对附件范围，再决定是否审批。
bad: 点击这里拒绝该审批。
```

## 3. Shared View Model

Insight 展示统一转成 View Model：

```json
{
  "renderer_type": "insight",
  "object_type": "",
  "object_id": "",
  "title": "",
  "severity": "",
  "severity_label": "",
  "insight_type": "",
  "insight_type_label": "",
  "recommendation": "",
  "reasoning": "",
  "impact": "",
  "evidence_refs": [],
  "memory_refs": [],
  "confidence_label": "",
  "status": ""
}
```

View Model 不包含 actions。

如果界面同时展示动作按钮，按钮必须来自 RuntimeResult / InteractionPayload actions，而不是 Insight。

## 4. Bot Renderer

Bot 中的 Insight 只能做摘要提醒。

适合展示：

- Insight title。
- Severity label。
- Recommendation 一句话。
- 打开 Detail / Portal 的入口。

不适合展示：

- 完整 reasoning。
- 多条 evidence_refs 明细。
- Memory 细节。
- 原始业务数据。

Bot 示例：

```text
发现 1 条审批洞察：附件金额与本次申请金额不一致。
建议：先核对附件是否包含历史、重复或非本次报销材料。
```

Bot 中的按钮：

- 可以打开详情页。
- 不由 Insight 生成 approve / reject。

## 5. Detail View Renderer

Detail View 是 Insight 的主展示位置。

适合展示：

- Recommendation。
- Reasoning。
- Impact。
- Evidence references 摘要。
- Memory references 摘要。
- Evidence drilldown 入口。

Detail View 的目标是让员工或管理者不用回到飞书原生页面，也能理解为什么要这么处理。

Detail View 可以同时展示 Runtime actions，但来源必须独立：

```text
Insight -> Recommendation card
RuntimeResult.actions -> Action buttons
```

禁止：

```text
Insight -> Action buttons
```

## 6. Management Portal Renderer

Management Portal 用于跨对象、跨业务聚合 Insight。

适合展示：

- Insight 列表。
- Severity 分组。
- Object type / object id。
- Recommendation。
- Impact。
- 关联 Evidence / Memory 数量。
- 进入对象 Detail 的入口。

不适合直接执行：

- approve / reject。
- send_message。
- create_task。
- schedule_meeting。

Portal 可以展示“建议下一步”，但实际动作仍需进入对象 Detail 或 Runtime 确认链路。

## 7. Approval Insight Rendering

Approval Insight 在 Detail View 中推荐展示顺序：

```text
1. Recommendation
2. Reasoning
3. Impact
4. Evidence References
5. Memory References
6. Evidence Drilldown
7. Runtime Action Buttons
```

Runtime Action Buttons 必须来自 RuntimeResult / InteractionPayload，不属于 Insight。

示例：

```text
洞察：附件金额与本次申请金额不一致
建议：先核对附件是否包含历史、重复或非本次报销材料，再决定是否审批。
原因：申请金额为 8902.33 元，附件可识别金额约 52279.23 元。
影响：如果附件范围不清，审批人无法确认本次费用是否真实、完整且归属正确。
```

## 8. Severity Display

Severity 展示建议：

```text
info: 信息
low: 低
medium: 需关注
high: 高风险
critical: 严重
```

Severity 只影响展示优先级。

Severity 不生成 Action。

## 9. Reference Display

Evidence References 展示为：

```text
依据：费用报销证据
说明：附件可识别金额高于申请金额，说明附件范围与本次申请可能不一致。
```

Memory References 展示为：

```text
历史模式：同一供应商多次出现付款材料缺失。
```

不展示内部 ID，除非处于调试模式。

## 10. Acceptance

本阶段验收标准：

- Insight Renderer 只展示 Recommendation。
- Insight Renderer 不生成 Action。
- Bot / Detail / Portal 的 Insight 展示边界清楚。
- Detail View 可以把 Insight 和 Runtime actions 同屏展示，但来源分离。
- Approval Insight 可以被展示，但不变成审批动作。
