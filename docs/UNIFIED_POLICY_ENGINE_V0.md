# Unified Policy Engine V0

架构归属：`V5_RUNTIME_CONSTITUTION.md` 中的 Policy Engine。

本文档冻结 Unified Policy Engine V0 的系统边界。若本文与 `V5_RUNTIME_CONSTITUTION.md` 冲突，以 Constitution 为准。

## 1. Principle

AI OS 只有一套权限系统：

```text
Policy Engine
```

冻结原则：

```text
There is only one permission system in AI OS.

Policy Engine is the only component allowed to make permission decisions.

Operational Filter and Cognitive Filter are both Policy Engine filters.

Runtime, Cognitive Engine and LLM must only consume policy-filtered data.

No component is allowed to bypass Policy.
```

禁止新增：

- Permission Engine。
- Cognitive ACL。
- Organization ACL。
- Runtime Permission。
- LLM Permission。

## 2. Boundary

Policy Engine 负责：

- Permission。
- Scope。
- Identity。
- Credential。
- Visibility。
- Confirmation。
- Authorization。
- Operational Filter。
- Cognitive Filter。
- Result Filter。

Policy Engine 不负责：

- 组织解析。
- 查询飞书通讯录。
- 执行业务动作。
- 生成回答话术。
- 让 LLM 自行脱敏。

组织事实来自：

```text
Organization Foundation
```

## 3. Chain

统一链路：

```text
Organization Foundation
-> PolicySubject
-> ManagementScope
-> Policy Engine
-> Operational Filter
-> Cognitive Filter
-> PolicyFilteredData
-> Runtime
-> LLM
```

Runtime 只消费 Policy Filter 后的数据。

LLM 永远不能看到未经 Policy Filter 的 Operational 或 Cognitive 数据。

## 4. Internal Flow

Policy Engine 内部流转：

```text
Subject Resolver
-> Scope Resolver
-> Decision
-> Operational Filter
-> Cognitive Filter
-> Result Filter
```

Subject Resolver 与 Scope Resolver 只能消费 Organization Foundation 的标准对象。

## 5. PolicySubject

PolicySubject 描述当前请求主体。

来源：

```text
Organization Foundation
```

最小字段：

```json
{
  "actor_user_id": "",
  "actor_open_id": "",
  "company_id": "",
  "roles": [],
  "department_ids": [],
  "management_scope": [],
  "is_owner": false,
  "is_admin": false
}
```

## 6. PolicyScope

PolicyScope 描述请求范围。

最小字段：

```json
{
  "requested_scope": "self/user/group/department/division/company",
  "resolved_scope": "self/user/group/department/division/company",
  "target_user_id": "",
  "target_department_id": "",
  "target_group_id": "",
  "target_company_id": ""
}
```

`resolved_scope` 必须来自 Organization Foundation 和 Policy Engine 共同确定的标准对象，不允许 Runtime 自行推断。

## 7. Operational Filter

Operational Filter 面向实时业务数据：

- Task。
- Approval。
- Calendar。
- People。
- Knowledge。
- Mail。
- Business。

它是 Policy Engine 的过滤器，不是单独权限系统。

## 8. Cognitive Filter

Cognitive Filter 面向认知对象：

- WorkEvent。
- Evidence。
- Snapshot。
- Insight。
- Memory。

它是 Policy Engine 的过滤器，不是 Cognitive Engine 自己的 ACL。

Cognitive Engine 只负责认知对象的存储、索引和召回，不负责判断谁能看。

## 9. Runtime Contract

Runtime 输入：

```text
CommandFrame
PolicyDecision
PolicyFilteredData
```

Runtime 禁止：

- 权限判断。
- 直接查询飞书通讯录。
- 直接读取未经 Policy Filter 的 Cognitive Data。
- 让 LLM 看到未过滤数据后再要求其保密。

## 10. Acceptance

任何模块接入 Task、Approval、Mail、Knowledge、People、Business 时，必须满足：

```text
Organization Foundation
-> Policy Engine
-> Runtime
-> LLM
```

后续任何模块不得自行实现权限判断。

任何组织解析必须经过 Organization Resolver。

任何 LLM 上下文必须来自 Policy Filter 之后的数据。
