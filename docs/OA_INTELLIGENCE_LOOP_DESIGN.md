# OA Intelligence Loop Design

本文档定义 Digital Advisor 如何基于飞书 OA 原始数据沉淀企业认知，并把认知反哺到员工日常 OA 工作中。

目标不是替代飞书 OA，也不是把用户重新引回飞书原生页面，而是在 AI OS 内让员工完成更多带 AI 的工作。

## 1. Core Loop

标准链路：

```text
OA Raw Data
-> WorkEvent
-> Evidence
-> Snapshot
-> MemoryCandidate
-> Insight
-> Action
```

含义：

- `OA Raw Data`：飞书审批、任务、会议、邮件、日程、文档等原始业务数据。
- `WorkEvent`：企业事实层，记录发生过什么。
- `Evidence`：证据层，把原始数据转成可核对、可解释的业务依据。
- `Snapshot`：当前认知层，保存对象当前判断。
- `MemoryCandidate`：长期记忆候选层，记录可能值得沉淀的组织模式。
- `Insight`：洞察层，输出 Recommendation，回答“这件事意味着什么，为什么重要，建议怎样推进”。
- `Action`：动作层，由 Insight 驱动，进入 Runtime 执行或等待人确认。

Action 不应直接由 raw data 或 snapshot 驱动。Action 必须有 Insight 作为可解释前置。

Insight 不负责执行，不规划动作，不生成 Action Candidate。Action 继续归 Runtime。

## 2. Product Principle

Digital Advisor 的价值不是给飞书 OA 加一个聊天入口，而是让 OA 工作变得更聪明：

```text
普通 OA：人打开系统 -> 人读材料 -> 人判断 -> 人操作
AI OS：系统整理证据 -> 系统形成洞察 -> 人确认关键判断 -> 系统协助执行
```

用户优先在以下入口完成工作：

```text
Bot + Detail View + Management Portal
```

飞书原生页面是兜底路径，不是默认路径。

只有在以下情况才引导回原生页面：

- 飞书 API 不开放必要数据。
- 合规或权限要求必须在原生页面完成。
- 当前 AI OS 尚未实现对应执行能力。
- 用户明确要求打开原生页面。

## 3. Insight Boundary

Insight 不是 Snapshot，也不是 Action。

Snapshot 回答：

```text
当前状态是什么？
```

Evidence 回答：

```text
依据是什么？
```

Insight 回答：

```text
这件事意味着什么？
为什么现在需要处理？
对谁有影响？
建议怎样推进？
```

Action 回答：

```text
要执行什么？
由谁确认？
通过哪个 Tool/Provider 执行？
```

Insight = Recommendation。

Action = Runtime 执行。

因此链路必须保持：

```text
Snapshot + Evidence + MemoryCandidate -> Insight -> Runtime Action
```

禁止：

```text
Raw Data -> Action
Snapshot -> Action
Evidence -> Action
```

## 4. Approval Example

审批样板链路：

```text
Approval Raw Data
-> WorkEvent
-> Approval Evidence
-> Approval Snapshot
-> MemoryCandidate
-> Approval Insight
-> Runtime Approval Action
```

示例：

- Evidence：申请金额 8902.33 元，附件可识别金额 52279.23 元，存在金额冲突。
- Snapshot：补充后再审，风险等级 review。
- MemoryCandidate：该申请人多次出现附件金额与申请金额不一致。
- Insight：这不是简单的高金额审批，而是“附件范围和本次申请范围不一致”，需要先确认附件是否包含历史或重复材料。
- Action：提示审批人核对附件明细；如果确认无误，再允许同意或拒绝。

## 5. Cross-OA Examples

### Task

```text
Task Raw Data
-> WorkEvent
-> Task Evidence
-> Task Snapshot
-> MemoryCandidate
-> Task Insight
-> Action
```

Insight 示例：

- 任务延期不是单点问题，而是依赖人连续三次未响应。
- 建议负责人先确认依赖阻塞，再决定是否发起协同提醒或调整负责人。

### Meeting

Insight 示例：

- 会议有决议但无负责人。
- 建议先确认 owner，再由 Runtime 创建待办。

### Mail

Insight 示例：

- 客户邮件涉及交付风险，且历史上同类问题超过 2 次。
- 建议升级给项目负责人，并准备回复方向；是否生成草稿由 Runtime 执行。

### Finance

Insight 示例：

- 某供应商付款频率异常，且多次缺合同编号。
- 建议财务复核供应商付款链路。

## 6. Action Rules

Action 必须满足：

- 来自 Insight。
- 带 company_id。
- 进入 Runtime。
- 高风险动作需要 Policy 确认。
- Tool 只做被动执行。
- Interaction 只渲染 Result。

Action 可以是：

- approve / reject
- create_task
- send_message
- draft_mail
- schedule_meeting
- request_missing_info
- assign_owner
- escalate_risk

但 Action 不负责解释为什么。为什么属于 Insight。

Insight Contract V0 见 `docs/INSIGHT_CONTRACT_V0.md`。

## 7. Current Scope

当前只设计，不实现完整 Insight Engine。

允许：

- 定义 OA Intelligence Loop。
- 定义 Insight 与 Evidence/Snapshot/Action 边界。
- 用 Approval 作为示例。
- 更新当前任务方向。

禁止：

- 新增数据库表。
- 实现 Insight Engine。
- 实现 Memory Engine。
- 实现跨业务 Action。
- 重构 Runtime。
- 新增 Workflow Engine。

## 8. Acceptance

本阶段验收标准：

- 系统文档明确 `Insight -> Action`。
- Action 不再被描述为直接由 AI 或 Snapshot 驱动。
- AI OS 的目标明确为：让员工在 OA 工作中更智能，而不是回到原生页面手工处理。
- Approval 仍只是第一条样板，不是系统边界。
