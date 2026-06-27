# Current Mission

本文档只回答：现在在做什么。

最高级架构规则见 `docs/V5_RUNTIME_CONSTITUTION.md`。

## 当前阶段

```text
Conversation First Command Engine Refactor V1
```

## 当前目标

停止继续补 Intent、People、Knowledge 或其他业务域入口规则，把 Command Engine 一次性调整为 Conversation First。当前冻结方向不是 Intent Refactor，而是 Conversation First Command Engine Refactor。

当前连续阶段：

1. People：通讯录、组织架构、部门成员、人员解析。
2. Communication / IM：当前会话、群聊搜索、消息读取、机器人发送边界。
3. Communication / Mail：邮箱列表、搜索、详情、摘要、草稿，USER_TOKEN-first。
4. Knowledge：云文档、Wiki、Drive 文件、制度流程、项目资料、公司介绍等正式知识读取路径。
5. Command Acceptance：用真实基础数据源验收 ConversationState / SemanticFrame / CommandFrame / Policy / Response mode。
6. Business Domain Iteration：再按 Workspace / Process / Knowledge / Intelligence 等业务域逐步扩展样板。

系统原则：

- 所有基础域必须继续进入 `Command -> Policy -> Runtime -> Provider -> RuntimeResult -> InteractionPayload` 主链路。
- Provider 只执行一个被动外部能力，不做规划、不做权限判断、不做最终表达。
- Capability Registry 继续作为 Domain / Capability / Skill / Provider Binding 的唯一能力元数据来源。
- Execution Identity 继续拆分 `actor_identity` 与 `credential_mode`。
- 公司资源读取优先 `BOT + TENANT_TOKEN`；个人资源和写动作优先 `USER + USER_TOKEN`。
- `CLI_PROFILE` 只作为本地兼容或历史 fallback，不作为云端主路径。
- 基础域 ProviderResult 必须带统一 `foundation_data_source` 元数据，供 Command 验收、Trace 和治理页使用。
- 固定 Pipeline 为：`User Message -> ConversationState -> Semantic Understanding -> Dialogue Resolver -> CommandFrame -> Policy -> Runtime -> Response Orchestrator -> Interaction`。
- `ConversationState` 统一替代散落的 `result_context / pending_action / pending_confirmation / pending_clarification / last_assistant_question / previous_result_reference` 读取；旧对象暂不删除，但只能作为 `ConversationStateBuilder` 的输入。
- Semantic Understanding 只理解当前话语，不输出 Capability、Provider、Runtime、权限、Identity 或 Credential。
- Dialogue Resolver 是 Command Engine 唯一裁决器，直接输出 `CommandFrame`；不新增 `DialogueDecision / IntentFrame` 等中间对象。
- `CommandFrame` 是 Command Engine 唯一输出，不包含权限、Identity、Credential、Provider；这些全部交给 Policy / Runtime。
- Capability Registry 不参与理解，只在 Runtime 前后的能力解析中被消费。
- Policy 不理解用户，只负责 Scope、Permission、Identity、Credential、Visibility、Confirmation、Authorization、Result Filter。
- Response Orchestrator 负责自然表达、LLM Rewrite、Async Insight、Summary、Card、SidePanel 和 Follow-up Suggestion，不允许 Provider 拼最终话术；Conversation First 输出必须优先消费 `output_contract`，文本回答不得退回 Provider 模板。
- 本地同步层只能是带 TTL、来源和字段可信度的企业身份/资源索引，服务查询加速、上下文解析、权限判断和动作目标解析；飞书等源系统仍是权威来源，敏感字段必须走字段级权限和可见性边界。

## 当前禁止范围

- 不把本次重构称为 Intent Refactor。
- 不新增 Foundation。
- 不新增 Runtime Layer。
- 不新增 Architecture V2。
- 不新增 Policy V2。
- 不新增设计文档。
- 不做 UI Migration。
- 不接新 Provider。
- 不做完整 ACL Engine。
- 不把 Profile 当作权限依据。
- 不让 LLM 直接选择 Provider / Tool / Credential / Execution Identity。
- 不让规则直接决定 Capability。
- 不让 Provider 拼最终回复。
- 不围绕飞书测试语句做短语补丁。
- 不把普通自然语言塞进显式命令规则。
- 不在无 GPU 云机上部署 7B/14B 作为前台模型。
- 不为 People / IM / Mail / Knowledge 各自新增非标主链路或业务域关键词入口。
- 不绕过 Capability Registry、Policy、Runtime 或 Tool Router 直连 Provider。
- 不把 WorkEvent / Memory 当作实时业务源替代 Feishu 源系统。
- 不开放未确认的写动作；发送消息、创建邮件草稿、编辑文档等仍需 Runtime 确认或 USER_TOKEN。

## 当前验收标准

- Conversation First V1 至少有 40 条固定回归，且每条同时断言 ConversationState、SemanticFrame、CommandFrame、Policy decision 和 Response mode。
- V1 接入 People 与 Knowledge；Task 写动作、Approval 写动作、Mail 写动作和全量 Provider 迁移不在 V1 范围。
- 基础域 `ProviderResult.metadata.foundation_data_source` 统一包含 domain、source、operation、strategy、actor_identity、credential_mode、resource_scope、provider_runtime。
- People 通讯录读取优先 `BOT + TENANT_TOKEN`，没有 active Feishu App 配置才回退旧路径。
- People 本地快照用于身份索引和权限/目标解析，不作为不可审计的事实黑箱；性别、岗位、手机号、邮箱等字段必须保留来源口径，未知字段不得由姓名、语气或 LLM 推断。
- ResultContext 后续追问必须先判定这是上一轮结果的 `expand / filter / continue / reference` 操作，字段输出再通过 `field_projection` 控制；用户只要数量或姓名时不得把手机号、邮箱、open_id、company_id 等内部字段混入正文；`continue/补全/剩下` 必须基于上一轮 `display_offset/display_end/display_limit` 接着列，不能从头重列或重新查询。
- People Provider 必须优先消费 Intent 抽取出的 `entities.keyword / people_query_field`，不得绕回整句自然语言做 Provider 关键词；多字段查询（例如职位 + 手机）由同一条 People 字段投影合并回答，保证不同问法的信息完整性一致。
- People 交互默认 Text-first：单人字段事实（手机号、邮箱、职位、性别）直接用自然语言回答，不渲染卡片；侧边栏只承接多人、明细、候选确认、显式展开和字段较多的结构化查看。
- People / Knowledge 查询必须产出 Conversation First `CommandFrame`，并在 `params.semantic_frame / output_contract / context_contract` 里保留可审计上下文。
- Provider 可以返回结构化事实和原始摘要，但 People / Knowledge 的最终文本与卡片/侧边栏选择由 Response Orchestrator 根据 `output_contract.surface/mode` 决定：数量与单人字段走自然文本，名单/明细走侧边栏，不在正文堆列表模板。
- 任意入口 Bug 必须归因到 ConversationState、SemanticFrame、DialogueResolver、Policy 或 Response Orchestrator 之一；禁止继续通过新增业务域关键词入口修复。
- PeopleQuery 入口可以抢在闲聊/外卖边界之前识别“某人电话/邮箱/岗位”等嵌入式事实查询，但不得越过 self-profile、Mail、组织导出等更明确的业务边界；例如“你知道我在公司的职位吗”仍是自我上下文对话，“最近邮箱里有哪些邮件”仍走 Mail，“把组织架构放进表”仍走组织导出动作。
- 大结果、字段较多结果或存在分页窗口的结构化结果必须通过 RuntimeResult 输出统一 `sidepanel_context` 与 `open_sidepanel` action；聊天正文保持轻量表达，完整列表和字段明细交给 Interaction/SidePanel 渲染，不在各业务域自造卡片协议。摘要型 People 查询（例如“公司有多少人/多少男生/多少女生”）默认只返回结论，不自动弹卡；显式名单/展开才进入详情展示。
- Mail 查询语义保持 USER 资源边界；SELF 可走 USER_TOKEN fallback，DEPARTMENT / COMPANY 不得偷用个人授权扩大范围。
- Knowledge 必须服务正式企业知识域：公司介绍、制度流程、项目资料、模板规范等都必须来自 Wiki / Drive / Doc / Knowledge 路径，不允许 Presentation LLM 凭空生成。
- Command Engine 验收必须覆盖真实基础数据源和 Conversation First 合同，不只测 intent 分类。
- 写动作仍需 RuntimeActionInput / WAITING_CONFIRMATION / USER_TOKEN 或明确 fallback，不被 LLM 自动执行。

## 下一步计划

```text
Conversation First V1 Runtime Cutover + Knowledge Data Ingestion
```

建议在飞书真实环境验收：

- “公司有多少人 / 某部门有哪些人 / 某人是谁”是否走 People 数据源。
- “当前群 / 搜索群 / 最近消息”是否走 IM 数据源，并清楚区分 BOT 通知与 USER 代发。
- “我的邮件 / 搜索邮件 / 邮件详情 / 起草回复”是否走 Mail 数据源和 USER_TOKEN 边界。
- “公司介绍 / 制度 / 项目资料 / 文档链接”是否走 Knowledge 数据源。
- “我是谁 / 你是谁 / 怎么称呼 / 你记得吗”这类问题是否不误触通讯录。
- “我的任务 / 我的日程 / 我的审批”是否仍走业务卡片。
- “部门任务 / 公司任务”是否清楚说明实时能力与认知聚合边界。
- `/system diagnostics`、`/runtime trace`、`/capability registry`、`/policy identity` 等显式命令是否进入控制面命令集。

## 当前进展

- People：线上已走 `people` 基础域，通讯录人数、人员查询通过 `BOT + TENANT_TOKEN` 返回，不再依赖未配置的 CLI profile；已开始登记 `people.resolve_identity` 飞书 Skill 原子能力，基于 `lark-contact` 负责姓名/邮箱/open_id 人员解析，组织架构和部门成员继续走 OpenAPI；People 已接入 `DomainQuery` 合同，Intent 会输出 `entities.domain_query`，CommandFrame 会记录 `params.domain_query`，Provider 会优先消费 `domain_query.fields/output_mode/presentation_hint/context_ref`，不再把自然语言散落解析到各层；嵌入式 People 查询已可从“外卖/闲聊句子”中抽取“某人的电话/邮箱/岗位”，但入口边界会保护 self-profile、Mail 和组织导出动作不被 People 误抢；通讯录 ResultContext 已支持按性别、部门、岗位/职位继续筛选，筛选后会形成新的 People 上下文，后续追问统一归一为 `result_context_operation=expand/filter/continue/reference`，例如“全部列出 / 哪几位 / 补全 / 第 N 个 / 只要名字 / 只要数量”都先操作上一轮结构化结果，再由 `field_projection` 控制字段；展开结果会写回 `display_offset/display_end/display_limit/has_more`，下一轮“补全/继续/剩下”从上次结束位置接着列，并继承上一轮字段投影；姓名电话/邮箱、岗位/职位、性别这类新通讯录事实问题会覆盖上一轮 follow-up，避免被旧结果吞掉；People 文字回答默认用自然语言融入真实数据，不再把“字段可见度 / 说明 / 可继续问”这类卡片导语塞进对话正文；“公司有多少人”默认只回答人数，不强行带部门数，部门覆盖和组织架构明细留给用户明确询问或侧边栏；“有谁的号码/谁的电话”会编译成 `filters.field_present=mobile` 的字段可见性查询，不再误当作某个叫“谁”的人；“把公司通讯录发我下”会编译成 People list 合同，不再变成 Workspace 概览；People Query Mode 已区分 `count_only / gender_count / gender_list / title_count / title_list`，避免“只回答人数”误入部门查询或名单模板；People 摘要查询会标记 `result_context_presentation=summary`，避免简单数量回答自动变成卡片；单人字段事实也会标记 `result_context_presentation=summary`，即使人员 item 包含多个字段，也只用文字回答，不生成“人员明细”卡片；People Provider 已回收为优先使用 Intent `entities.keyword`，避免 Provider 把整句“某人的职位和手机是什么”当搜索词；多字段个人查询会一次性合并返回可见字段；People Context Frame 已输出 `current_person/current_requested_field/current_requested_fields/identity_resolution/visible_fields`，支持“他/她/那个人/查一下/那谁呢”的上下文继承；People 上下文已支持“上一轮查某字段，下一轮换人”的继承，例如“汤冠男是什么职位”后接“那陈俊呢”会继承岗位字段并重新查陈俊；人员搜索缺手机号/邮箱/岗位等请求字段时，会从组织快照补全匹配人员，而不是直接回答查不到；People 快照 TTL 已从 10 分钟提升到 6 小时，并新增 `people.snapshot.prewarm` celery beat 任务，每 30 分钟预热 active Feishu App 的组织快照，减少用户第一次问人数时现场扫通讯录的概率；生产已验证预热返回 47 人、39 个部门，二次执行命中缓存 `cache_hit=true/fetch_ms=0`，部门树 children 接口异常时会降级到授权范围 scopes，不再让单个飞书接口抖动打断整个快照；人员字段已开始按 Evidence Contract 输出来源可信度，性别统计和个人性别查询共享可靠性规则，性别只承认源系统明确字段，姓名错别字只返回近似候选确认，不直接回答手机号/岗位等事实。
- People → Communication / Workspace：People ResultContext 已开始进入动作参数层；“这些人/他们”等引用会解析为统一 `people_targets`；IM 多人发送必须先明确发送方式（机器人通知、本人分别发送、建群后发送），确认阶段仍有总闸门，不会自动私发或 Bot 群发；Mail 草稿可复用人员邮箱作为收件人，Calendar 创建可复用人员 open_id 作为参会人，Task 创建可复用人员 open_id 作为任务成员。
- Runtime Confirmation：带 `people_targets` 的写动作会进入统一 pending confirmation，上下文和确认文案会展示人员目标数量与摘要；“这些人发消息/写邮件/安排开会/创建任务”不再被普通 follow-up 消费。
- RuntimeResult / Interaction：已新增通用 `sidepanel_context` 合同；当结构化结果适合完整查看时，RuntimeResult 会输出 `open_sidepanel` action，包含 `result_type/entity_domain/item_count/display_offset/display_end/display_limit/has_more/visible_fields`，供侧边栏按统一结果上下文渲染；Portal 已新增 `/api/portal/result-context` 只读接口，SidePanel 会优先尝试渲染通用 ResultContext 表格/详情，审批结果继续回落到既有审批专用流程；飞书 RuntimeResult 卡片已能把 `open_sidepanel` action 渲染为轻量“打开侧边栏”按钮，URL 使用 `/sidepanel?view=result_context&autoload=1&chat_id=...`，不把通讯录/大列表重新做成卡片；通用侧边栏已从内部字段黑名单升级为业务域展示白名单，People 只展示姓名、职位、部门、手机、邮箱、可靠性别，并会合并重复部门；`open_id/company_id/raw/department_ids/title_source/resource_plane/resource_type/source_system/allowed_user_ids` 等系统字段不得进入用户界面。审批详情仍保留既有专用 action，新业务域不得绕过 RuntimeResult 自造侧边栏入口。
- Communication / IM：线上已走 `communication` 基础域，群聊数量和群列表通过当前 Feishu App 返回。
- Communication / Mail：线上保持个人资源边界；未授权时返回 USER_TOKEN 授权提示，不把个人授权扩展到公司范围。
- Knowledge：Runtime Provider 已接入正式知识资料路径，优先从已登记 Resource、Drive 列表、Doc/Docx 内容、企业画像读取；`foundation_data_source.domain=knowledge`，`provider_runtime=hybrid_knowledge`。
- Command Engine：Conversation First V1 已冻结并开始落地。新增 `ConversationState -> SemanticFrame -> DialogueResolver -> CommandFrame` 合同层；People / Knowledge 已通过 V1 接入 `command_layer.py`，其他域暂走旧路径；首批 44 条回归已覆盖公司人数、性别追问、名单/展开、单人字段、换人追问、Knowledge 公司/制度/资料、结果动作、确认/取消、小聊天等场景，并同时断言 ConversationState、SemanticFrame、CommandFrame、Policy decision 和 Response mode。旧 `IntentCandidate / DomainQuery / ResultFollowup` 暂保留为兼容路径，但不得再作为新增入口规则的方向。
- Response Orchestrator：People / Knowledge 已开始消费 Conversation First `output_contract`。`compose_answer` 会把人数、单人手机号/邮箱/职位/性别等摘要型问题自然化为文本，不再直接输出 Provider 的字段完整度或人员明细模板；`RuntimeResult` 会按 `surface=text/sidepanel` 决定是否生成侧边栏 action，单人字段事实不再弹卡，名单/明细不再在正文列长列表。

当前线上数据事实：

- 当前生产 Feishu App 可见的 Drive/Wiki 列表为空。
- V5 `resources` 中已登记的主要是 Base/运营表、审批编码、群聊和能力资源，暂未发现可读的公司介绍、报销流程、项目资料类 Doc/Docx/Wiki 正文资源。
- 因此 “公司是做什么的 / 报销流程怎么做 / 项目资料在哪里” 已经走对 Knowledge 路径，但在没有正式知识资料可读时会返回“没有找到可靠答案”，不会用邮件、底层日志或 LLM 编造。
