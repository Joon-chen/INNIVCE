# Enterprise Cognitive Foundation V1 Freeze Review

本文档冻结 Enterprise Cognitive Foundation V1。

当前认知模型：

```text
Raw Data
-> WorkEvent
-> Evidence
-> Snapshot
-> Insight
```

Action 继续归 Runtime。

## 1. Frozen Boundary

V1 边界正式冻结为：

```text
WorkEvent = 事实层
Evidence = 判断依据层
Snapshot = 当前认知层
Insight = 建议层
Action = Runtime 执行层
```

每层只承担自己的职责，不向相邻层扩张。

## 2. WorkEvent

WorkEvent 是事实层。

职责：

- 记录企业里已经发生、已被系统观察到或已由 Runtime 执行完成的事实。
- 保留事实来源、对象、参与者、时间和原始 payload。
- 作为 Evidence / Snapshot / Insight 的可追溯源头。

规则：

- append-only。
- 必须携带 `company_id`。
- 不做判断。
- 不做建议。
- 不做展示。
- 不执行动作。

WorkEvent 回答：

```text
发生了什么？
来自哪里？
属于哪个公司？
关联哪个对象？
```

## 3. Evidence

Evidence 是判断依据层。

职责：

- 把 Raw Data / WorkEvent 中的业务数据转成可核对、可解释的依据。
- 说明已知事实、缺失事实、证据冲突、证据质量。
- 翻译系统噪音，例如 widget id、OCR 失败、表单结构异常。
- 给 Snapshot 和 Insight 提供依据。

规则：

- 不做最终业务判断。
- 不执行动作。
- 不生成 Action Candidate。
- 不暴露技术噪音给管理者。

Evidence 回答：

```text
我们知道什么？
缺什么？
哪些证据冲突？
证据质量如何？
```

## 4. Snapshot

Snapshot 是当前认知层。

职责：

- 保存系统对某个业务对象的当前认知状态。
- 消费 Evidence，不直接消费 raw data。
- 为 Bot / Detail / Portal 提供稳定的当前判断。
- 表达 recommendation、risk_level、summary、reasons。

规则：

- Snapshot 不是业务缓存。
- Snapshot 不保存实时业务状态。
- Snapshot 不替代 Feishu / Mail / Calendar / Task 等源系统。
- Snapshot 可以更新，但必须保留 source_event_ids。
- Snapshot 未完成时，Interaction 不得展示不完整 AI 判断。

Snapshot 回答：

```text
这个对象当前怎么看？
当前建议是什么？
当前风险等级是什么？
依据来自哪些事件？
```

## 5. Insight

Insight 是建议层。

职责：

- 把 Evidence、Snapshot 和 MemoryCandidate 转成可解释 Recommendation。
- 解释为什么重要。
- 说明不处理的影响。
- 引用 Evidence / Memory。
- 为 Runtime Action 提供可解释前置。

规则：

- Insight = Recommendation。
- Insight 不负责执行。
- Insight 不规划动作。
- Insight 不生成 Action Candidate。
- Insight 不调用 Tool / Provider。

Insight 回答：

```text
这件事意味着什么？
为什么现在需要关注？
建议怎样推进？
不处理会有什么影响？
```

## 6. Generation Flow

认知生成链路冻结为：

```text
WorkEvent
-> Evidence
-> Snapshot
-> Insight
```

含义：

- WorkEvent 提供事实。
- Evidence 生成可核对依据。
- Snapshot 保存当前认知。
- Insight 输出建议。

禁止：

```text
Raw Data -> Snapshot
Raw Data -> Insight
Evidence -> Action
Snapshot -> Action
Insight -> Tool
```

## 7. Display Flow

展示链路冻结为：

```text
Bot
Detail
Portal
```

展示职责：

- Bot：摘要提醒和入口。
- Detail：单对象认知详情、证据展开、Insight 解释。
- Portal：多对象、多业务聚合视图。

展示规则：

- Interaction 只展示 Result / Payload。
- Bot / Detail / Portal 不直接调用 Tool。
- Bot / Detail / Portal 不生成业务判断。
- Bot / Detail / Portal 不生成 Action Candidate。
- 原生飞书页面是兜底路径，不是默认路径。

## 8. Execution Flow

执行链路冻结为：

```text
Insight
-> Runtime
-> Action
```

含义：

- Insight 提供建议和解释。
- Runtime 负责动作规划、权限、确认、执行状态。
- Action 由 Runtime 执行。

Action 继续归 Runtime，不属于 Cognitive Foundation。

## 9. V1 Forbidden Scope

ECF V1 冻结后不再新增：

- Evidence Engine。
- Snapshot Engine。
- Insight Engine。
- Insight Store。
- Insight Persistence。
- Action Candidate。
- Action Planner。
- 第二套 Runtime。

也不新增：

- Event Bus。
- Workflow Engine。
- Memory Engine。
- 跨业务全量实现。

## 10. Future Extension Points

以下只记录未来扩展点，不实现。

### Task Sample

复用链路：

```text
Task WorkEvent
-> Task Evidence
-> Task Snapshot
-> Task Insight
```

可能验证：

- 任务延期。
- 负责人不清。
- 依赖阻塞。
- 重复延期模式。

### Meeting Sample

复用链路：

```text
Meeting WorkEvent
-> Meeting Evidence
-> Meeting Snapshot
-> Meeting Insight
```

可能验证：

- 会议决议。
- 待办 owner。
- 会议后无人跟进。
- 决议与任务闭环关系。

### Customer Sample

复用链路：

```text
Customer WorkEvent
-> Customer Evidence
-> Customer Snapshot
-> Customer Insight
```

可能验证：

- 客户风险。
- 商机推进。
- 客户邮件和会议交叉证据。
- 长期客户健康度。

## 11. Freeze Decision

Enterprise Cognitive Foundation V1 状态：

```text
Frozen
```

冻结结论：

- WorkEvent / Evidence / Snapshot / Insight 四层边界成立。
- Action 继续归 Runtime。
- Approval 是第一条认知样板，不是系统边界。
- 下一阶段不继续抽象认知底座。

下一阶段：

```text
Task Cognitive Sample Selection
```

目标：

验证 Task 是否可以复用：

```text
WorkEvent
-> Evidence
-> Snapshot
-> Insight
```
