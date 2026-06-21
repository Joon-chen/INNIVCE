# Capability Registry Read API Review

本文档冻结 Capability Registry Read API 方案。

目标：

验证 `CapabilityRegistryBuilder` 是否能够成为四个页面唯一数据源。

页面：

```text
能力目录
能力清册
治理中心
系统诊断
```

本阶段只做 API Review，不改 UI、不改数据库、不改现有业务逻辑。

## 1. Options

### Option A: Root Payload

```text
GET /api/v5/capability-registry
```

返回：

```text
catalog_payload
skill_registry_payload
governance_payload
diagnostics_payload
```

优点：

- 一个请求即可驱动四个页面。
- 最适合作为 UI 迁移前的唯一真源。
- 前端状态管理简单。
- 可以一次性校验四个页面职责边界。
- 更容易做版本化和合同测试。

缺点：

- Payload 比拆分端点更大。
- 系统诊断刷新频率可能高于能力目录。
- 后续如果治理项很多，Root Payload 会膨胀。

### Option B: Split Endpoints

```text
GET /api/v5/capability-registry/catalog
GET /api/v5/capability-registry/skills
GET /api/v5/capability-registry/governance
GET /api/v5/diagnostics/runtime
```

优点：

- 每个页面只取自己需要的数据。
- 更适合高频刷新 Diagnostics。
- Governance Finding 很多时更容易分页。
- API 职责天然贴合页面职责。

缺点：

- UI 迁移时需要多个请求和多套加载状态。
- 更容易出现四页数据版本不一致。
- 初期更难证明四个页面来自同一 Registry Builder。
- 前端消费复杂度更高。

## 2. Recommendation

推荐：

```text
Option A first, Option B compatible later
```

冻结 V1 Read API 为：

```text
GET /api/v5/capability-registry
```

返回完整 Root Payload。

原因：

- 当前目标是验证 Registry Builder 能否成为四个页面唯一数据源。
- Root Payload 可以一次性证明能力目录、能力清册、治理中心、系统诊断都来自同一 Builder。
- 当前 Payload 规模仍可控，不需要为了性能提前拆分。
- 页面尚未迁移，先减少前端消费复杂度更重要。

保留未来拆分：

```text
GET /api/v5/capability-registry/catalog
GET /api/v5/capability-registry/skills
GET /api/v5/capability-registry/governance
GET /api/v5/diagnostics/runtime
```

但拆分端点不作为 V1 必需项。

## 3. Payload Size Assessment

基于当前 Builder mock 和静态清册：

```text
Domain: 7
Capability: 约 60
Skill: 约 57
ProviderBinding: 约 57
Finding: 默认少量，随治理输入增长
Diagnostics Provider: 默认少量，随 Provider health 输入增长
```

估算：

| Payload | Size Risk | Notes |
| --- | --- | --- |
| catalog_payload | Low | 只有 Domain 和 Capability，变化慢 |
| skill_registry_payload | Medium | Skill 和 ProviderBinding 较多，但仍可控 |
| governance_payload | Medium to High | Finding 可能随治理扩大增长 |
| diagnostics_payload | Low to Medium | Provider health 和错误摘要可能增长 |
| root payload | Medium | 当前可接受，后续治理项多时需分页 |

V1 限制：

- Governance Finding 不做全量历史。
- Diagnostics 不返回完整日志。
- Provider health 只返回摘要。
- Root Payload 不返回业务对象列表。

如果 Root Payload 超过可接受范围，第一优先拆：

```text
governance_payload
```

第二优先拆：

```text
diagnostics_payload
```

## 4. Cache Strategy

V1 推荐短缓存：

```text
Cache-Control: private, max-age=30
```

原因：

- Catalog / Skill Registry 变化慢。
- Governance / Diagnostics 变化快，但当前只用于管理后台读取。
- 30 秒缓存可以降低重复加载成本，同时不掩盖运行问题太久。

子 Payload 建议 TTL：

| Payload | Suggested TTL |
| --- | --- |
| catalog_payload | 5 minutes |
| skill_registry_payload | 1 minute |
| governance_payload | 30 seconds |
| diagnostics_payload | 10-30 seconds |

Root API V1 统一采用：

```text
30 seconds
```

未来拆分端点后再按子 Payload 单独缓存。

缓存 Key：

```text
company_id
payload_version
builder_version
```

不得跨公司共享缓存。

## 5. Frontend Consumption Complexity

### Option A

前端加载：

```text
fetch /api/v5/capability-registry
-> state.capabilityRegistry
-> catalog page reads catalog_payload
-> skill page reads skill_registry_payload
-> governance page reads governance_payload
-> diagnostics page reads diagnostics_payload
```

复杂度：

```text
Low
```

适合第一轮迁移。

### Option B

前端加载：

```text
fetch catalog
fetch skills
fetch governance
fetch diagnostics
```

复杂度：

```text
Medium
```

需要处理：

- 多请求 loading。
- 多请求错误状态。
- 子 Payload 版本不一致。
- 页面切换时重复请求。
- Governance / Diagnostics 高频刷新。

结论：

V1 先用 Option A，避免 UI 重构阶段同时处理数据拆分复杂度。

## 6. Builder And API Boundary

API 只做：

```text
读取 company_id
调用 CapabilityRegistryBuilder
返回 Root Payload
设置 cache header
```

API 不做：

- 业务分类判断。
- Skill 归属推断。
- Provider health 计算。
- Governance Finding 生成逻辑。
- Diagnostics 汇总逻辑。
- Runtime 执行。
- Tool 调用。
- Provider 调用。
- 数据库写入。
- 页面结构拼装。

Builder 负责：

```text
RuntimeCapability
+ SkillAtomicCapability
+ ToolConfig
+ ProviderHealth
+ GovernanceFinding
-> Root Payload
```

API 负责：

```text
Root Payload transport
```

## 7. Page Proof

### Capability Catalog

唯一数据源：

```text
catalog_payload
```

满足页面：

- Domain tabs。
- Capability list。
- Capability status。
- Supported surfaces。
- Business value。

不需要：

- Skill。
- Provider。
- Runtime log。
- Governance Finding。

结论：

```text
可以完全由 Registry API 驱动。
```

### Skill Registry

唯一数据源：

```text
skill_registry_payload
```

满足页面：

- Capability filter。
- Skill table。
- Risk level。
- Status。
- Confirmation required。
- Runtime supported。
- Receipt supported。
- Provider count。

不需要：

- 资源同步列表。
- 系统日志。
- 业务对象列表。

结论：

```text
可以完全由 Registry API 驱动。
```

### Governance Center

唯一数据源：

```text
governance_payload
```

满足页面：

- P0 / P1 / P2 / P3 summary。
- Capability findings。
- Skill findings。
- Provider findings。
- Recommendation。
- Finding status。

不需要：

- 普通资源列表。
- Runtime trace detail。
- 业务对象列表。

结论：

```text
可以完全由 Registry API 驱动。
```

### System Diagnostics

唯一数据源：

```text
diagnostics_payload
```

满足页面：

- Runtime status。
- Provider status。
- Permission status。
- Result Context status。
- Response Experience status。
- Follow-up status。
- Action State status。

不需要：

- Domain。
- Capability。
- Skill。
- 业务域健康度。

结论：

```text
可以完全由 Registry API 驱动。
```

## 8. Migration Path

### Step 1: Read API Contract Test

实现前先补合同测试：

- Root Payload 包含四个子 Payload。
- `company_id` 必须存在。
- Catalog 不包含 Skill / Provider。
- Diagnostics 不包含 Domain / Capability / Skill。
- Governance 只返回 Finding。

### Step 2: Read API Implementation

只增加只读路由：

```text
GET /api/v5/capability-registry
```

不改 UI。

不改数据库。

不改 Runtime。

### Step 3: Shadow Verification

在不切 UI 的情况下，人工或测试读取 Root Payload：

- 能力目录是否够用。
- 能力清册是否够用。
- 治理中心是否够用。
- 系统诊断是否够用。

### Step 4: Minimal UI Migration

只在确认 Root Payload 足够后，才进入 UI 迁移。

迁移顺序：

1. 能力目录。
2. 能力清册。
3. 治理中心。
4. 系统诊断。

### Step 5: Optional Split Endpoints

当满足任一条件时再拆：

- Governance Finding 过多。
- Diagnostics 需要高频刷新。
- Root Payload 加载明显变慢。
- 页面需要独立权限或分页。

## 9. Frozen Contract

V1 冻结为：

```text
GET /api/v5/capability-registry
```

返回：

```text
CapabilityRegistryRootPayload
```

包含：

```text
catalog_payload
skill_registry_payload
governance_payload
diagnostics_payload
```

暂不冻结：

```text
GET /catalog
GET /skills
GET /governance
GET /diagnostics
```

这些作为 V1.1 或 V2 拆分路径。

## 10. Acceptance Criteria

- 推荐方案明确。
- Payload 大小风险可控。
- 缓存策略明确。
- 前端消费复杂度明确。
- Builder 与 API 边界明确。
- 四个页面都能由 Registry API 驱动。
- 不进入 UI 重构。
- 不修改数据库。
- 不修改业务逻辑。

## 11. Next Step

进入：

```text
Capability Registry Read API Implementation
```

范围：

- 新增只读 API。
- 新增 API 合同测试。
- 不改 UI。
- 不改数据库。
- 不改 Runtime 业务逻辑。
