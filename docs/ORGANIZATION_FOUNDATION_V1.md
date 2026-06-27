# Organization Foundation V1

架构归属：`V5_RUNTIME_CONSTITUTION.md` 中的 Foundation Layer。

本文档冻结 Organization Foundation V1 的系统边界。若本文与 `V5_RUNTIME_CONSTITUTION.md` 冲突，以 Constitution 为准。

## 1. Position

Organization Foundation 是 AI OS 的组织事实层。

它属于 Foundation Layer，不属于 People Runtime，不属于 Business Domain，也不属于 Policy Engine。

所有模块消费 Organization Foundation：

```text
Command
Policy
Runtime
Workspace
Knowledge
Business
Cognitive
People
Communication
```

## 2. Principle

Organization Foundation 负责组织事实。

Policy Engine 负责权限决策。

Runtime 只消费已解析组织对象和 Policy Filter 之后的数据。

LLM 禁止猜组织对象，禁止读取未经 Policy Filter 的数据。

## 3. Data Sources

Feishu Organization 是官方组织数据来源。

同步进入本地 Organization Foundation：

```text
Feishu Organization
-> Organization Sync
-> Organization Foundation
```

Feishu 提供：

- Department。
- User。
- Department Membership。
- Leader / Manager，如果可用。
- 用户状态。
- 企业联系方式字段。

本地维护：

- Alias Dictionary。
- Role Model。
- Management Scope。
- 业务身份。
- 人工补充和纠错。
- 解析置信度与来源元数据。

## 4. Data Model

Organization Foundation 至少包含：

```text
Organization Directory
Contact Directory
Department Tree
Department Membership
Organization Graph
Role Model
Management Scope
Alias Dictionary
Identity Index
Organization Resolver
Source Metadata
```

Contact Directory 只是 Organization Foundation 的一个数据集。通讯录不能等同于组织模型。

## 5. Organization Graph

Organization Graph 描述：

```text
Group Company
-> Company
-> Division
-> Department
-> Group
-> User
```

节点：

- Department。
- Group。
- User。
- Leader。
- Manager。

边：

- belongs_to。
- member_of。
- manages。
- reports_to。

## 6. Sync Pipeline

V1 同步策略：

- Full Sync：组织树、用户、部门、Membership、Leader。
- Incremental Sync：Webhook 处理组织变化和人员变化。
- On-demand Refresh：Resolver 未命中时刷新单个 Department 或 User。

原则：

- Master Data 同步、标准化、长期缓存。
- Operational Data 仍实时查询。
- Task、Approval、Calendar 不做本地镜像主数据。
- Runtime 禁止每次对话直接扫飞书通讯录。

## 7. Organization Resolver

Organization Resolver 解析：

- 部门。
- 组。
- 事业部。
- 人员。
- Alias。
- 管理关系。

输入：

```text
自然语言对象
ConversationState
SemanticFrame
Organization Foundation
```

输出：

```text
resolved_user_id
resolved_department_id
resolved_scope
confidence
candidates
reason
needs_clarification
```

Resolver 未唯一命中时必须返回候选或澄清，不允许退回全量组织数据。

Runtime 禁止字符串 contains 解析：

- 商务部。
- 商务组。
- 业务部。
- 半导体事业部。
- 王悦。
- 陈俊。
- 负责人。

## 8. Role Model

Role Model 是本地组织角色模型，不等同于飞书职位。

最小角色：

- Owner。
- Company Admin。
- Division Manager。
- Department Manager。
- Group Leader。
- Employee。

## 9. Management Scope

Management Scope 描述可管理范围。

示例：

```text
Owner -> COMPANY
Division Manager -> DIVISION
Department Manager -> DEPARTMENT
Group Leader -> GROUP
Employee -> SELF
```

Policy Engine 只消费 Management Scope，不自行解析组织树。

## 10. Consumers

Command Engine 消费 Organization Resolver 结果生成 CommandFrame。

Policy Engine 消费 PolicySubject 与 ManagementScope。

Runtime 消费 resolved object 与 PolicyFilteredData。

Cognitive Engine 只绑定认知对象到组织对象，不做权限决策。

People Domain 只负责 People Capability，不负责组织事实。

## 11. Acceptance

以下问题必须经过 Organization Foundation 和 Organization Resolver：

- 商务部多少人。
- 商务组有哪些人。
- 半导体事业部负责人是谁。
- 王悦是谁。
- 那陈俊呢。
- 负责人有哪些组。
- 机械组有哪些成员。

权限问题必须经过 Organization Foundation 和 Policy Engine：

- 负责人能查部门任务吗。
- 组长能查组内任务吗。
- Owner 能查公司任务吗。

禁止验收路径：

```text
飞书实时通讯录
或 LLM 猜测
或 Runtime 字符串 contains
```
