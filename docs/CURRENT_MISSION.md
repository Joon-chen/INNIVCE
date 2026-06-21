# Current Mission

本文档只回答：现在在做什么。

历史阶段归档见 `docs/history/`。
系统最高级规则见 `docs/V5_RUNTIME_CONSTITUTION.md`。
审批样板冻结版见 `docs/APPROVAL_RUNTIME_SAMPLE.md`。
Runtime V1 冻结复盘见 `docs/RUNTIME_V1_FREEZE_REVIEW.md`。
第二业务样板选择见 `docs/SECOND_BUSINESS_SAMPLE_SELECTION.md`。
审批验收复盘见 `docs/APPROVAL_RUNTIME_SAMPLE_ACCEPTANCE_REVIEW.md`。

## 当前阶段

Approval Runtime Sample Acceptance Review 已完成。

当前阶段回到：

```text
Approval Runtime Boundary Stabilization
```

当前系统边界已冻结到：

```text
Interaction 只展示
Command 只规划
Policy 只管边界和权限
Runtime 唯一执行
Tool / Provider 只做被动能力
```

## 当前目标

Approval Runtime Sample V1 可作为架构模板冻结，并继续守住 Runtime V1 边界：

- approve / reject 属于 Runtime Mainline。
- transfer / add_sign 只允许 Runtime Input + missing params + guard。
- batch approval 属于 Legacy Island。
- admin write approval API 属于 Out-of-Scope。
- Runtime 不解析人名，不生成 open_id。
- USER Resolution 属于未来 Resolver / People Provider 边界。
- Task 最适合验证 Approval Runtime Sample 的跨业务通用性。
- 首选最小样板是 `task_query -> task_complete -> feedback`。
- 飞书产品体验仍需真实手工验收，不能把合同测试当生产验收。

当前 V1 冻结基线：

```text
tests/test_runtime_v5.py tests/test_gateway_feishu.py tests/test_v5_architecture.py
191 passed
```

## 当前禁止范围

本阶段不要做：

- Batch Migration。
- Batch RuntimeActionInput / RuntimeState / RuntimeResult / Workflow Design。
- USER Resolver / contact lookup / open_id resolution。
- Transfer Execution。
- AddSign Execution。
- Diagnostics / Observability 重构。
- Memory / WorkEvent / Provider Snapshot company_id 改造。
- 全量 Portal / Card / SidePanel 重构。
- Renderer 重构。
- 开发 Task / Meeting / Customer 样板。
- 新增 Tool / Runtime State / Resolver。
- Runtime 代码或业务逻辑改动，除非下一阶段明确批准。

## 当前验收标准

当前边界继续成立：

- 单审批 approve / reject 必须走 `RuntimeActionInput -> Runtime -> Tool/Provider -> RuntimeResult -> InteractionPayload`。
- Card / Portal / SidePanel 不直接调用 Tool、Provider、Feishu API、MCP 或 CLI。
- `RuntimeResult` 是唯一业务状态来源。
- `InteractionPayload` 是唯一展示输入。
- Runtime 主链路所有执行路径必须携带 `company_id`。
- batch approval 只能留在明确登记的 legacy island。
- transfer / add_sign 不能执行 Provider，只能停在输入合同与 guard。
- `target_user` 只能作为 USER_TEXT / USER_REFERENCE 输入，不得变成 open_id。
- 第二条业务样板不得复制审批字段名和审批 action builder。
- Task 第一轮不得引入 assign_members、DATE/USER resolver 或复杂对象解析。
- Approval V1 可复制的是主链路和合同，不是审批 UI 细节或 batch legacy。

## 下一步计划

Approval Acceptance Review 完成后，仍不默认开发 Runtime V2。

候选方向：

- Task Sample Contract Design：只设计 Task 样板合同，不写业务代码。
- USER Resolver Contract Design：只设计 Resolver 输入/输出合同，不接通讯录。
- Runtime Diagnostics / Observability Design：把 diagnostics 归到 Observability Layer。
- Batch Runtime Design：为 batch approval 单独设计批量 Runtime。
- Runtime State Persistence Design：从 Session state 走向持久化 task/action/step/log。
- Renderer Registry Design：只设计 registry，不重构现有 Portal/Card/SidePanel。

建议下一步由业务风险选择，而不是自然扩展 Runtime。
