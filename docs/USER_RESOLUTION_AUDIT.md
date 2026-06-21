# USER Parameter Resolution Audit

本文档回答：USER 参数体系边界应该长什么样。

本阶段不是 Runtime V2 实现，不接通讯录，不解析 OpenID，不执行 transfer/add_sign。

## 1. USER Parameter 生命周期

建议冻结 USER 参数生命周期为：

```text
USER_TEXT
-> USER_REFERENCE
-> USER_RESOLVED
-> OPEN_ID
-> EXECUTABLE_TARGET
```

### USER_TEXT

含义：

- 用户输入的原始文本，例如 `转交给张三`、`@李四`、`加签给王五`。
- 当前 `MissingParamResolver(USER)` 只负责从文本中提取干净的人名片段。

归属：

- Runtime 可保存。
- Runtime 可编排等待输入。
- Runtime 不解释身份。

当前样板：

```text
target_user = "张三"
```

### USER_REFERENCE

含义：

- 可被解析的人引用。
- 仍然不是飞书身份 ID。
- 可以包含文本、mention token、email、手机号、部门上下文或候选范围。

归属：

- Command 可以从自然语言或结构化事件中提取引用。
- Runtime 可以携带引用并进入 resolution step。
- Resolver 负责把引用转成候选人。

### USER_RESOLVED

含义：

- 已经过解析的候选人结果。
- 可能是唯一匹配，也可能是多候选，需要用户选择。
- 仍不等于可执行目标，除非通过 Policy。

归属：

- Resolver 产出。
- Policy 检查是否允许作为动作目标。
- Runtime 只保存 resolution 状态和候选结果。

### OPEN_ID

含义：

- 平台身份 ID，例如 Feishu `open_id`。
- 是 Tool/Provider 执行审批动作所需的技术 ID。

归属：

- People Provider / Tool Lookup 获取。
- Resolver 选择并标准化。
- Runtime 不生成 open_id。

### EXECUTABLE_TARGET

含义：

- 已满足执行条件的目标对象。
- 包含 action 所需字段，例如：
  - transfer: `transfer_user_id`
  - add_sign: `add_sign_user_ids`

归属：

- Resolver 将 resolved user 转换为目标参数。
- Policy 最后确认权限和风险。
- Runtime 在确认后把 executable target 交给 Tool/Provider。
- Provider 只执行，不再联系人名解析。

## 2. USER Resolution Boundary Matrix

| 能力 | Command | Policy | Runtime | Tool / Provider |
| --- | --- | --- | --- | --- |
| USER Extractor | 可从用户话语中识别人引用 | 不负责 | 可通过 MissingParamRegistry 保存 `target_user_text` | 不负责 |
| USER Resolver | 不负责解析身份 | 不负责解析身份 | 只编排 resolution step，不解析 open_id | Resolver/People Tool 负责解析候选 |
| USER Lookup | 不负责 | 可限制查询范围 | 不直接查通讯录 | People Provider 调通讯录或人员索引 |
| People Provider | 不负责 | 不负责 | 只能调用，不内嵌查询逻辑 | 被动返回人员候选/详情 |
| Transfer Execution | 不负责 | 判断是否允许转交 | 编排确认与执行状态 | Approval Provider 执行已解析目标 |
| Add Sign Execution | 不负责 | 判断是否允许加签 | 编排确认与执行状态 | Approval Provider 执行已解析目标 |

冻结边界：

- Command 识别“用户引用”，不解析身份。
- Policy 判断“是否允许”，不查人。
- Runtime 编排状态，不解析人名，不生成 open_id。
- Tool / Provider 被动查人或执行已解析动作。

## 3. Runtime 是否应该解析人名

结论：Runtime 不应该解析人名，也不应该解析 open_id。

Runtime 只负责：

- 接收 `target_user_text`。
- 保存 `target_user` / `target_user_text` 到 pending action。
- 进入 `WAITING_INPUT`。
- 参数补齐后进入 `WAITING_CONFIRMATION`。
- 在未来 USER resolution step 中编排状态。
- 根据 Resolver/Policy 结果决定继续等待、要求用户选择、阻断或执行。

Runtime 不负责：

- 联系人搜索。
- 模糊匹配。
- open_id 解析。
- 多候选选择策略。
- 人员权限来源判断。
- 把人名拼成 Provider 执行参数。

原因：

- 人名解析依赖组织通讯录、平台身份、租户边界和权限，不是 Runtime 状态机职责。
- Runtime 解析 open_id 会把 Tool/Provider 能力和身份策略塞回主链路。
- 多租户场景下，人名必须在 `company_id` 和权限范围内解析，不能由 Runtime 猜测。

当前实现状态：

- `target_user -> USER` 只做文本抽取。
- transfer/add_sign 补齐后只到 `WAITING_CONFIRMATION`。
- 确认执行前命中 `guarded_pending_user_resolution`。
- Provider 不执行。
- `runtime_action_input.target` 不写入 `open_id`。

## 4. Transfer/AddSign Future Path

未来链路建议：

```text
request_transfer / request_add_sign
-> RuntimeActionInput(missing_params=["target_user"])
-> WAITING_INPUT
-> user supplies USER_TEXT
-> WAITING_CONFIRMATION
-> USER_RESOLUTION
-> USER_RESOLVED or NEED_USER_SELECTION or BLOCKED
-> Policy confirmation
-> EXECUTING
-> DONE / FAILED
```

USER_RESOLUTION 应该位于：

```text
Runtime orchestration step
-> Resolver
-> People Provider / Lookup Tool
-> Resolver result
-> Policy
-> Runtime state update
```

职责拆分：

- Runtime 创建 `USER_RESOLUTION` 状态或 step。
- Resolver 接收 `USER_REFERENCE` 和 `company_id`。
- People Provider / Lookup Tool 查询通讯录或人员索引。
- Resolver 返回候选、唯一人选或无法解析。
- Policy 判断目标是否允许用于 transfer/add_sign。
- Runtime 根据结果进入执行、等待选择或 blocked。
- Approval Provider 只接收 `EXECUTABLE_TARGET`。

未来状态建议：

```text
WAITING_INPUT
-> WAITING_CONFIRMATION
-> RESOLVING_USER
-> WAITING_USER_SELECTION
-> WAITING_POLICY_CONFIRMATION
-> EXECUTING
-> DONE / FAILED / BLOCKED
```

注意：这些是未来 Runtime V2 候选状态，不属于本阶段实现。

## 5. Out Of Scope

本阶段明确禁止：

- 通讯录接入。
- OpenID 解析。
- Transfer 执行。
- AddSign 执行。
- SidePanel 迁移。
- Batch Runtime 设计。
- Diagnostics。
- 新增 Runtime V2 状态机。
- 新增 UserResolver 代码。
- 修改 Approval Provider 执行逻辑。

## 6. Existing Code Risk Note

当前代码中，Approval Provider 内部存在历史辅助逻辑：

```text
_resolve_approval_target_user_ids()
-> feishu_contact_user_search
-> transfer_user_id / add_sign_user_ids
```

这不是 Runtime V1 的 USER Resolution。

当前 Runtime 主链路通过 guard 阻断 transfer/add_sign 执行，因此这段 provider 内部 lookup 不会成为 V1 样板路径。

未来若启用 transfer/add_sign，应该把该能力拆到明确的 USER Resolver / People Provider 边界中，而不是让 Approval Provider 在执行阶段顺手解析人。

## Audit Decision

USER 参数体系可以作为 Runtime V2 的边界设计输入。

冻结判断：

- `USER_TEXT` 属于当前 Missing Params Contract。
- `USER_REFERENCE` 到 `USER_RESOLVED` 属于未来 Resolver 边界。
- `OPEN_ID` 由 People Provider / Lookup Tool 获取，不由 Runtime 生成。
- `EXECUTABLE_TARGET` 只能在 Resolver + Policy 通过后进入 Runtime execution。
- Runtime 只负责编排 USER resolution 生命周期，不负责身份解析。
