# Enterprise AI OS V1

本文档定义 Digital Advisor 作为企业 AI OS 的顶层系统目标。

审批只是第一条样板链路，不是系统边界。系统最终要服务的是企业管理者的整体工作方式：看清事实、理解风险、形成判断、推动执行、沉淀组织记忆。

## 1. North Star

Enterprise AI OS 的目标不是把一堆工具接进聊天窗口，而是让企业工作从：

```text
人找系统 -> 人读材料 -> 人判断 -> 人催办 -> 人复盘
```

变成：

```text
系统感知事实 -> 系统整理证据 -> 系统形成认知 -> 系统建议动作 -> 人确认关键判断
```

AI OS 必须减少管理者负担，而不是制造新的信息负担。

## 2. Product Principle

系统面向管理者必须回答五类问题：

```text
What happened?
What matters?
What is risky?
What should I do?
What should the organization remember?
```

所有业务线都服从这五个问题：

- Approval：这张单能不能批，缺什么，风险在哪里。
- Mail：这封邮件要不要回，谁负责，是否影响客户或项目。
- Meeting：会议形成了什么决议，谁要跟进，哪些事项卡住。
- Task：哪些任务延期，原因是什么，谁需要协同。
- Customer：客户当前状态如何，风险和机会是什么。
- Finance：费用、付款、回款、预算是否异常。
- People：组织、人员、权限、协作关系是否清楚。

## 3. AI OS Layering

Enterprise AI OS 分为六层：

```text
Interaction
Command
Policy
Runtime
Tool
Cognitive Foundation
```

其中 Cognitive Foundation 是企业认知底座：

```text
WorkEvent -> Evidence -> Snapshot -> MemoryCandidate -> Memory
```

V1 已建立：

- WorkEvent：事实层
- Snapshot：当前认知层
- MemoryCandidate：长期记忆候选层

下一步要补齐：

- Evidence：证据层

## 4. Evidence Layer

Evidence 是 AI OS 的关键缺层。

没有 Evidence，系统会在两种错误之间摇摆：

- 直接把原始系统噪音交给 AI。
- 直接把 AI 判断交给管理者，但缺少可解释依据。

Evidence 的职责是把业务系统数据变成管理者可理解的证据。

```text
Raw System Data -> Evidence -> Cognitive Snapshot
```

Evidence 不做最终建议。

Evidence 只回答：

- 我们知道什么？
- 我们不知道什么？
- 哪些证据完整？
- 哪些证据缺失？
- 哪些证据解析失败？
- 这些证据对业务判断意味着什么？

## 5. Evidence Contract V1

所有业务线共用同一 Evidence Contract：

```json
{
  "evidence_type": "",
  "quality": "complete|partial|failed|pending",
  "object_type": "",
  "object_id": "",
  "facts": {},
  "missing": [],
  "conflicts": [],
  "technical_notes": [],
  "manager_summary": "",
  "suggested_next_step": "",
  "source_event_ids": []
}
```

字段含义：

- `facts`：结构化业务事实。
- `missing`：缺失的业务证据。
- `conflicts`：事实之间的冲突。
- `technical_notes`：系统内部解析状态，只供调试和二次处理，不直接展示给管理者。
- `manager_summary`：面向管理者的证据摘要。
- `suggested_next_step`：不是最终审批/发送/执行动作，而是证据层建议下一步。

技术噪音必须被翻译：

```text
bad: widget17602323038220001
good: 费用明细表未成功还原，无法核对每笔费用。
```

## 6. Snapshot Contract

Snapshot 是当前认知层，不是缓存层。

Snapshot 消费 Evidence，而不是直接消费原始业务系统数据。

```text
Evidence -> Snapshot
```

Snapshot 负责：

- recommendation
- risk_level
- summary
- reasons
- suggested_manager_action
- confidence

Snapshot 不负责：

- 保存实时业务状态
- 保存完整业务对象详情
- 替代 Feishu / Mail / Calendar / Task 数据源
- 暴露技术解析噪音

## 7. Manager Interaction Contract

Interaction 层只展示可行动信息：

```text
Live Data + Evidence Summary + Snapshot Judgment
```

管理者不应该看到：

- JSON 错误
- widget ID
- OCR 原始长文本
- API 字段名
- 内部 open_id 作为姓名
- 无法判断但伪装成高风险

管理者应该看到：

- 当前状态
- 关键事实
- 证据是否完整
- 风险是否真实
- 缺什么
- 下一步怎么处理

AI OS 的交互目标是让员工和管理者优先在：

```text
Bot + Detail View + Management Portal
```

中完成大部分工作。

除非权限、合规、数据开放能力或飞书 API 限制导致系统无法闭环，否则不应默认把用户引导回飞书原生页面。原生页面是兜底，不是主路径。

## 8. First Vertical Sample

Approval 是第一条样板，因为它同时覆盖：

- 实时业务事实
- 附件/OCR
- 金额核对
- 风险判断
- 管理者确认动作
- 长期组织记忆候选

但 Approval 的目标不是做审批工具，而是验证 AI OS 的通用闭环：

```text
WorkEvent -> Evidence -> Snapshot -> Interaction -> Runtime Action -> MemoryCandidate
```

审批中出现的 `widget...` 问题，本质不是审批问题，而是 Evidence Layer 缺失问题。

## 9. Current Phase

当前进入：

```text
Enterprise Evidence Layer V1
```

先以 Approval 作为样板验证通用 Evidence Contract。

第一阶段只做：

- Evidence Contract
- Approval Form Evidence
- Approval Attachment Evidence
- Approval Expense Evidence
- Snapshot Builder consuming Evidence
- Portal / Card 展示 Evidence Summary

不做：

- 新数据库表
- Event Bus
- Workflow Engine
- Memory Engine
- 跨业务全量实现
- Task/Mail/Meeting 业务开发
- Batch Approval
- Transfer/AddSign
- Diagnostics 重构

## 10. Acceptance Criteria

AI OS V1 的验收不是“某个审批单显示正常”，而是：

- 所有业务样板都能区分 Raw Data、Evidence、Snapshot。
- 管理者看不到技术噪音。
- 系统能说明证据完整度。
- 系统能说明缺什么。
- 系统能给出下一步建议。
- AI 判断有证据来源。
- 技术失败不会被误判成业务高风险。

Approval V1 的具体验收：

- `widget...` 不进入管理者展示文案。
- 费用明细解析失败被表达为 Evidence quality。
- Snapshot 原因来自 Evidence Summary。
- Portal 展示 Live Data + Evidence Summary + Snapshot Judgment。
- 管理者能直接拿系统建议去通过、拒绝或要求补充。

## 11. Product Judgment

Digital Advisor 不是飞书 API 的聊天壳，也不是审批卡片集合。

它应该成为企业的认知操作系统：

```text
事实进入系统
证据被整理
判断被形成
行动被执行
经验被沉淀
```

这才是 AI OS。
