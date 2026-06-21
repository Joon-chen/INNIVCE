# Snapshot Trigger Matrix

本文档定义 Snapshot 生成节奏。

核心原则：

- Snapshot 不由 Query 触发。
- Snapshot 不由用户打开页面触发。
- Snapshot 由 WorkEvent 驱动。
- Bot / Portal / SidePanel 只能读取 Snapshot。
- Snapshot Builder 根据 WorkEvent 判断是否需要重建 Snapshot。

## 1. Snapshot Trigger Matrix

| WorkEvent | object_type | Trigger Snapshot? | Builder Action | Notes |
| --- | --- | --- | --- | --- |
| `approval_created` | `approval` | Yes | create `pending_analysis` if missing | 只建立认知占位，不执行 AI 分析 |
| `attachment_processed` | `approval` | Yes | build or rebuild approval judgment | 附件处理完成后才允许进入 AI 分析 |
| `approval_analysis_completed` | `approval` | Yes | write `completed` snapshot | 这是 AI 分析完成事实，不再次触发 AI |
| `approval_approved` | `approval` | No | no rebuild | 动作事实，不改变审批前认知判断 |
| `approval_rejected` | `approval` | No | no rebuild | 动作事实，不改变审批前认知判断 |
| `approval_cancelled` | `approval` | No | no rebuild | 仅影响 live approval status，不影响认知判断 |
| `approval_form_changed` | `approval` | Yes | mark stale and rebuild | 未来事件，表单证据变化时重建 |
| `approval_attachment_changed` | `approval` | Yes | mark stale and rebuild | 未来事件，附件证据变化时重建 |
| `bot_query` | any | No | read only | 禁止触发 Snapshot Builder |
| `portal_opened` | any | No | read only | 禁止触发 Snapshot Builder |
| `sidepanel_opened` | any | No | read only | 禁止触发 Snapshot Builder |

## 2. Approval Snapshot Trigger Rules

Approval Snapshot 类型：

```text
object_type = approval
snapshot_type = approval_current_judgment
```

触发规则：

- `approval_created`：
  - 若 Snapshot 不存在，创建 `pending_analysis`。
  - 不读取附件。
  - 不执行 AI。
  - 不写 completed Snapshot。
- `attachment_processed`：
  - 若存在新的附件证据，Builder 可以执行 AI 分析。
  - 成功后写 `approval_analysis_completed` WorkEvent。
  - 再写 `completed` Snapshot。
- `approval_analysis_completed`：
  - 只负责把 AI Cognitive State 写入 Snapshot。
  - 不再次触发 AI。
- `approval_approved / approval_rejected`：
  - 记录动作事实。
  - 不重建审批前 AI 判断。
  - UI 的审批状态仍从 Feishu live data 读取。

读取规则：

```text
Live Approval Data + Approval Snapshot -> Interaction Output
```

- Live Approval Data 来自 Feishu。
- AI recommendation / risk_level / reasons 来自 Snapshot。
- Snapshot missing 或非 completed 时展示“分析中”。

## 3. Snapshot Versioning Rules

Snapshot 是当前认知层，可以更新，但必须可追溯。

规则：

- 每次 Snapshot 更新必须写入 `source_event_ids`。
- `source_event_ids` 必须包含导致本次认知变化的 WorkEvent。
- Snapshot 不保存审批状态、审批列表或审批详情缓存。
- Snapshot payload 只保存认知状态或 AI 分析结果。
- 同一业务对象同一 `snapshot_type` 只保留一个 current Snapshot。
- 历史变化由 WorkEvent 保留，不通过多张 Snapshot 表保存。

推荐版本字段，未来可补：

```text
snapshot_version
evidence_hash
built_from_event_id
expires_at
```

V0 暂不新增字段，先用 `source_event_ids + updated_at` 表达版本。

## 4. Snapshot Rebuild Rules

Builder 判断是否重建时，只看 WorkEvent。

允许重建：

- 新的 `attachment_processed` 到达。
- 新的 `approval_form_changed` 到达。
- 新的 `approval_attachment_changed` 到达。
- Snapshot 为 `failed` 且存在可重试证据事件。
- Snapshot 为 `pending_analysis` 且已有附件处理完成事件。

禁止重建：

- 用户再次发起 Bot Query。
- 用户打开 Portal / SidePanel / WebView。
- 仅审批状态变为 approved / rejected。
- 仅 live list 顺序变化。
- 没有新增证据事件。

去重规则：

- 对同一个 `company_id + object_type + object_id + snapshot_type`，同一证据集合只允许一个 Builder 任务进入执行。
- 若 Builder 发现当前 Snapshot 已基于最新证据完成，应返回 `skipped_completed`。
- 若 Builder 发现正在运行的任务基于同一证据集合，应返回 `skipped_running`。

## 5. Snapshot Expiration Rules

Expiration 表示认知是否过期，不表示业务对象是否过期。

Approval V0 规则：

- completed Snapshot 默认不因时间自动过期。
- completed Snapshot 只因新证据事件变 stale。
- pending Snapshot 不向用户展示判断，只展示“分析中”。
- failed Snapshot 不向用户展示旧判断，只展示“分析失败/待重新分析”。
- approved / rejected 不让 Snapshot 过期，因为审批状态来自 Feishu live data。

未来可引入：

- `expires_at`：用于周期性重新分析的业务对象。
- `stale_reason`：说明过期原因。
- `evidence_hash`：判断证据是否变化。

## 6. Acceptance Criteria

- Bot Query 永远只读取 Snapshot。
- Portal / SidePanel 打开永远只读取 Snapshot。
- Query 和页面打开不触发 Snapshot Builder。
- Snapshot Builder 根据 WorkEvent 判断是否需要重建 Snapshot。
- Approval 展示必须稳定：同一批 live data 和同一批 completed Snapshot 下，多次查询结果一致。
- Snapshot 变化只能由新的 WorkEvent 证据驱动。
