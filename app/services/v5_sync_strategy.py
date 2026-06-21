from typing import Any

from app.services.data.resource_sources import (
    EXTERNAL_MAIL_ACCOUNT,
    EXTERNAL_WEB,
    FEISHU_APP_IDENTITY,
    FEISHU_USER_IDENTITY,
    KNOWLEDGE_DATA,
    MEMORY_DATA,
    OPERATIONAL_DATA,
    PERSONAL_DINGTALK_ACCOUNT,
    storage_for_data_type,
)
from app.services.v5_resources import normalize_v5_resource_type


PRIMARY_DATA_SOURCE = {
    "platform": "feishu",
    "role": "primary_operational_platform",
    "advisor_role": "intelligence_platform",
    "principle": "飞书是企业运营和数据主平台；外部邮箱、个人钉钉等按授权补充；数字参谋只做智能索引、记忆、检索和报告，不复制飞书或重建 CRM/ERP。",
}

DATA_SOURCE_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "key": FEISHU_APP_IDENTITY,
        "name": "飞书企业级资源",
        "identity": "app_identity",
        "scope": "company",
        "examples": ["通讯录", "审批", "多维表格", "云文档", "Wiki", "任务", "日历", "IM 消息"],
    },
    {
        "key": FEISHU_USER_IDENTITY,
        "name": "飞书个人级资源",
        "identity": "user_identity",
        "scope": "user",
        "examples": ["个人飞书邮箱", "个人日历", "本人任务", "本人授权云空间"],
    },
    {
        "key": EXTERNAL_MAIL_ACCOUNT,
        "name": "外部邮箱",
        "identity": "user_identity",
        "scope": "owner_or_user",
        "examples": ["IMAP", "Gmail", "Outlook/Graph"],
    },
    {
        "key": PERSONAL_DINGTALK_ACCOUNT,
        "name": "个人钉钉",
        "identity": "external_connector",
        "scope": "user",
        "examples": ["个人钉钉待办", "个人钉钉消息", "个人钉钉审批"],
    },
)

DATA_TYPE_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "key": OPERATIONAL_DATA,
        "name": "Operational Data（业务数据）",
        "storage": storage_for_data_type(OPERATIONAL_DATA),
        "realtime_query": "lark_cli_first",
        "persistence": "important_business_data_to_work_events",
        "rule": "结构化、可统计、可分析，落 PostgreSQL 或 PostgreSQL 索引。",
    },
    {
        "key": KNOWLEDGE_DATA,
        "name": "Knowledge Data（知识数据）",
        "storage": storage_for_data_type(KNOWLEDGE_DATA),
        "external_storage": storage_for_data_type(KNOWLEDGE_DATA, source_type=EXTERNAL_WEB),
        "storage_layers": {
            "internal": "document_store_vector_db",
            "external": "web",
        },
        "levels": {
            "l1_hot": "sync_to_local_rag",
            "l2_cold": "register_only_lark_cli_realtime",
            "l3_external": "external_search_realtime_no_local_store",
        },
        "rule": "非结构化/RAG 检索，内部进入 Document Store + Vector DB，外部 WEB 只作为外部知识源。",
    },
    {
        "key": MEMORY_DATA,
        "name": "Memory Data（记忆数据）",
        "storage": storage_for_data_type(MEMORY_DATA),
        "rule": "个体化、长期上下文，只保存稳定事实和偏好。",
    },
)

DATA_QUERY_POLICIES: tuple[dict[str, Any], ...] = (
    {
        "key": "operational_realtime_cli_first",
        "data_type": OPERATIONAL_DATA,
        "primary_query_path": "lark_cli",
        "local_fallback": "work_events",
        "persistence_rule": "important_business_data_to_work_events",
        "xmind_rule": "Operational Data 实时查询优先使用 CLI，重要业务数据同步 WorkEvent 进行处理。",
    },
    {
        "key": "knowledge_l1_hot",
        "data_type": KNOWLEDGE_DATA,
        "level": "L1",
        "primary_query_path": "local_rag",
        "persistence_rule": "sync_to_document_store_vector_db",
        "xmind_rule": "L1 热知识同步到本地并做 RAG 优化。",
    },
    {
        "key": "knowledge_l2_cold",
        "data_type": KNOWLEDGE_DATA,
        "level": "L2",
        "primary_query_path": "lark_cli",
        "persistence_rule": "register_only_no_full_sync",
        "xmind_rule": "L2 冷知识只登记不同步，按需使用飞书 CLI 实时查询。",
    },
    {
        "key": "knowledge_l3_external",
        "data_type": KNOWLEDGE_DATA,
        "level": "L3",
        "primary_query_path": "external_search",
        "persistence_rule": "no_local_store",
        "xmind_rule": "L3 外部知识不入库，实时搜索。",
    },
)

SYNC_LAYERS: tuple[dict[str, Any], ...] = (
    {
        "key": "realtime_work_events",
        "name": "实时工作事件",
        "sync_mode": "realtime_or_near_realtime",
        "resource_types": ["chat", "approval", "calendar", "meeting", "task", "directory"],
        "storage": ["work_events", "extracted_items"],
        "query_path": "lark_cli_first_then_work_event_cache",
        "vectorize": "summary_only",
        "retention": "轻量摘要、时间线、参与人、来源链接和必要 payload。",
        "examples": ["飞书消息事件", "审批状态变化", "会议/日历变化", "任务变化", "飞书邮箱事件"],
        "sync_action": "realtime_event",
    },
    {
        "key": "master_data_index",
        "name": "主数据索引",
        "sync_mode": "scheduled_index",
        "resource_types": ["bitable"],
        "storage": ["business_object_index", "work_events"],
        "query_path": "lark_cli_first_then_index",
        "vectorize": "key_fields_only",
        "retention": "只保存对象 ID、名称、状态、负责人、关键字段摘要和飞书 record 链接。",
        "examples": ["客户", "订单", "项目", "供应商", "合同", "设备", "售后问题"],
        "sync_action": "master_data_index",
    },
    {
        "key": "knowledge_hot",
        "name": "高频知识",
        "sync_mode": "scheduled_chunking",
        "resource_types": ["wiki", "doc"],
        "storage": ["knowledge_items", "knowledge_chunks", "qdrant_vectors", "memory_facts"],
        "query_path": "local_rag",
        "vectorize": "full_chunk_or_summary_chunk",
        "retention": "项目复盘、技术案例、决策记录等高价值知识可切片和向量化。",
        "examples": ["项目复盘", "技术案例", "故障案例", "决策记录", "管理制度"],
        "sync_action": "knowledge_vectorize",
    },
    {
        "key": "knowledge_cold",
        "name": "低频大型资料",
        "sync_mode": "index_only_on_change",
        "resource_types": ["doc", "wiki"],
        "storage": ["document_index"],
        "query_path": "lark_cli_realtime",
        "vectorize": "outline_and_summary_only",
        "retention": "默认只保存标题、链接、版本、摘要、关键词和目录；全文按需读取。",
        "examples": ["原始技术手册", "大型测试报告", "设计文档", "历史归档文档", "压缩包"],
        "sync_action": "document_index_only",
    },
    {
        "key": "knowledge_external_web",
        "name": "外部 WEB 知识源",
        "sync_mode": "reference_index",
        "resource_types": ["web"],
        "storage": ["web"],
        "query_path": "external_search_realtime",
        "vectorize": "none_or_summary_after_capture",
        "retention": "只保存 URL、标题、来源、抓取时间、摘要和可信度，不把外部 WEB 当作内部知识库。",
        "examples": ["行业网页", "公开政策", "外部产品资料", "新闻线索"],
        "sync_action": "external_realtime_reference",
    },
    {
        "key": "long_term_memory",
        "name": "长期记忆",
        "sync_mode": "derived_after_sync",
        "resource_types": ["derived"],
        "storage": ["memory_facts"],
        "query_path": "memory_facts",
        "vectorize": "fact_level",
        "retention": "只保存稳定事实、管理偏好、重要决策和反复出现的问题，不保存每句话。",
        "examples": ["人员职责", "客户背景", "项目关键结论", "风险模式", "老板偏好"],
        "sync_action": "memory_extract_later",
    },
    {
        "key": "qdrant_vectors",
        "name": "语义检索索引",
        "sync_mode": "derived_after_permission_filter",
        "resource_types": ["derived"],
        "storage": ["qdrant"],
        "query_path": "vector_search",
        "vectorize": "chunks_and_summaries",
        "retention": "只放可检索摘要或知识切片；业务真相仍以 PostgreSQL 和飞书为准。",
        "examples": ["知识切片", "项目复盘片段", "决策记录片段", "重要工作事件摘要"],
        "sync_action": "knowledge_vectorize",
    },
)

DEFAULT_REALTIME_RESOURCE_TYPES = ["approval", "chat", "calendar", "meeting", "task", "directory"]
DEFAULT_INDEX_RESOURCE_TYPES = ["bitable"]
KNOWLEDGE_COLD_TYPES = {"doc", "wiki"}
EXTERNAL_WEB_LAYER = "knowledge_external_web"
REALTIME_LAYER = "realtime_work_events"
MASTER_DATA_LAYER = "master_data_index"
KNOWLEDGE_COLD_LAYER = "knowledge_cold"
LONG_TERM_MEMORY_LAYER = "long_term_memory"

FEISHU_CAPABILITY_REQUIREMENTS: tuple[dict[str, Any], ...] = (
    {
        "resource_type": "chat",
        "name": "群消息",
        "access_mode": "app_identity_with_auto_discovered_chat",
        "required_identifiers": [],
        "preferred_sync_path": "event_subscription_or_messages_api",
        "default_sync_action": "realtime_event",
        "note": "群资源由事件、群列表和本地消息自动发现；需要机器人在群内，不要求老板手工登记群 ID。",
    },
    {
        "resource_type": "approval",
        "name": "审批",
        "access_mode": "app_identity_with_auto_discovered_approval_resource",
        "required_identifiers": [],
        "preferred_sync_path": "native_approval_api",
        "default_sync_action": "realtime_event",
        "note": "审批资源标识由系统从待审批任务、事件和历史实例中自动发现，用户不需要手工维护。",
    },
    {
        "resource_type": "mailbox",
        "name": "飞书邮箱",
        "access_mode": "app_identity_with_auto_discovered_mailbox",
        "required_identifiers": [],
        "preferred_sync_path": "native_mail_api",
        "default_sync_action": "realtime_event",
        "note": "邮箱是独立账号资源：飞书邮箱可用邮箱地址发现文件夹，外部邮箱走 IMAP/Gmail/Outlook 授权；不从全员通讯录批量抓取。",
    },
    {
        "resource_type": "directory",
        "name": "通讯录",
        "access_mode": "app_identity",
        "required_identifiers": [],
        "preferred_sync_path": "contacts_api",
        "default_sync_action": "realtime_event",
        "note": "用于身份识别、部门/角色映射和权限路由。",
    },
    {
        "resource_type": "calendar",
        "name": "日历",
        "access_mode": "app_identity_or_user_authorization_with_auto_discovery",
        "required_identifiers": [],
        "preferred_sync_path": "calendar_api",
        "default_sync_action": "realtime_event",
        "note": "日历资源通过授权、事件或接口自动发现；未授权时进入资源盲区监控。",
    },
    {
        "resource_type": "meeting",
        "name": "会议",
        "access_mode": "app_identity_or_user_authorization_with_auto_discovery",
        "required_identifiers": [],
        "preferred_sync_path": "meeting_api",
        "default_sync_action": "realtime_event",
        "note": "会议资源通过日程、会议列表和事件自动发现；纪要、参会人和录制按授权范围同步。",
    },
    {
        "resource_type": "task",
        "name": "任务",
        "access_mode": "app_identity_or_user_authorization_with_auto_discovery",
        "required_identifiers": [],
        "preferred_sync_path": "task_api",
        "default_sync_action": "realtime_event",
        "note": "任务资源通过任务 API 和事件自动发现，变化进入工作事件，后续抽取待办和风险。",
    },
    {
        "resource_type": "bitable",
        "name": "多维表格",
        "access_mode": "app_identity_with_auto_discovered_bitable",
        "required_identifiers": [],
        "preferred_sync_path": "bitable_api",
        "default_sync_action": "master_data_index",
        "note": "多维表格从云盘、事件和已同步数据中自动发现，是业务主数据来源，本地只保存索引和关键字段摘要。",
    },
    {
        "resource_type": "doc",
        "name": "云文档",
        "access_mode": "app_identity_with_auto_discovered_document",
        "required_identifiers": [],
        "preferred_sync_path": "drive_doc_api",
        "default_sync_action": "document_index_only",
        "note": "云文档从云盘、Wiki 和消息附件自动发现；默认只建文档索引，高频知识经确认后再切片向量化。",
    },
    {
        "resource_type": "wiki",
        "name": "知识库",
        "access_mode": "app_identity_with_auto_discovered_wiki",
        "required_identifiers": [],
        "preferred_sync_path": "wiki_api",
        "default_sync_action": "document_index_only",
        "note": "知识库空间和节点由 Wiki API 自动发现；默认只同步目录、摘要和来源链接，不默认拉全文。",
    },
    {
        "resource_type": "bot",
        "name": "机器人交互",
        "access_mode": "outbound_and_mentioned_events",
        "required_identifiers": [],
        "preferred_sync_path": "im_api_and_event_subscription",
        "default_sync_action": "manual_review_required",
        "note": "单聊可直接回答；群聊只在被 @ 时回答。",
    },
    {
        "resource_type": "web",
        "name": "外部 WEB",
        "access_mode": "external_source_with_allowlist_or_user_instruction",
        "required_identifiers": ["url"],
        "preferred_sync_path": "web_reference_index",
        "default_sync_action": "document_index_only",
        "note": "外部 WEB 是知识补充来源，只保存引用、摘要和可信度，不作为飞书主数据或内部向量库事实源。",
    },
)


def sync_strategy_overview() -> dict[str, Any]:
    return {
        "data_source": PRIMARY_DATA_SOURCE,
        "data_sources": list(DATA_SOURCE_CATALOG),
        "data_types": list(DATA_TYPE_CATALOG),
        "query_policies": list(DATA_QUERY_POLICIES),
        "layers": list(SYNC_LAYERS),
        "authorization_model": list(FEISHU_CAPABILITY_REQUIREMENTS),
        "defaults": {
            "realtime_resource_types": DEFAULT_REALTIME_RESOURCE_TYPES,
            "index_resource_types": DEFAULT_INDEX_RESOURCE_TYPES,
            "large_documents": "index_only",
            "qdrant": "knowledge_chunks_and_summaries_only",
            "memory": "stable_facts_only",
            "sync_actions": [
                "realtime_event",
                "master_data_index",
                "document_index_only",
                "knowledge_vectorize",
                "external_realtime_reference",
                "memory_extract_later",
                "manual_review_required",
                "skip",
            ],
        },
        "rules": [
            "company_id 是第一隔离字段。",
            "飞书是企业运营和数据主平台；外部邮箱、个人钉钉等按授权作为补充数据来源。",
            "工作事件实时或准实时同步。",
            "Operational Data 实时查询优先使用飞书 CLI，重要业务数据再同步为 WorkEvent。",
            "客户、订单、项目、供应商等结构化运营数据以飞书多维表格为准，本地只建索引。",
            "高频知识切片进入 Qdrant 和长期记忆。",
            "低频大型资料默认只登记目录、摘要和索引，按需使用飞书 CLI 实时查询，不实时全文同步。",
            "外部知识不入内部库，按需实时搜索并只保存引用、摘要、来源和可信度。",
            "权限过滤必须发生在 Qdrant 检索和 LLM 调用之前。",
        ],
    }


def capability_requirement_for_resource(resource_type: str | None, resource_id: str | None = None) -> dict[str, Any]:
    normalized = normalize_v5_resource_type(resource_type, resource_id)
    for item in FEISHU_CAPABILITY_REQUIREMENTS:
        if item["resource_type"] == normalized:
            return dict(item)
    return {
        "resource_type": normalized,
        "name": "未知资源",
        "access_mode": "manual_review_required",
        "required_identifiers": [],
        "preferred_sync_path": "manual_review",
        "default_sync_action": "manual_review_required",
        "note": "该资源类型需要人工确认飞书 API 能力、授权方式和同步价值。",
    }


def data_layer_for_resource(resource_type: str | None, resource_id: str | None = None) -> str:
    normalized = normalize_v5_resource_type(resource_type, resource_id)
    if normalized in {"chat", "approval", "calendar", "meeting", "task", "mailbox", "directory"}:
        return REALTIME_LAYER
    if normalized == "bitable":
        return MASTER_DATA_LAYER
    if normalized == "web":
        return EXTERNAL_WEB_LAYER
    if normalized == "memory":
        return LONG_TERM_MEMORY_LAYER
    if normalized in KNOWLEDGE_COLD_TYPES:
        return KNOWLEDGE_COLD_LAYER
    return "manual_review"


def sync_action_for_resource(
    resource_type: str | None,
    resource_id: str | None = None,
    *,
    large_document_mode: str = "index_only",
    bitable_mode: str = "master_data_index",
) -> dict[str, Any]:
    normalized = normalize_v5_resource_type(resource_type, resource_id)
    data_layer = data_layer_for_resource(resource_type, resource_id)
    authorization = capability_requirement_for_resource(resource_type, resource_id)
    if data_layer == KNOWLEDGE_COLD_LAYER and large_document_mode == "disabled":
        return {
            "data_layer": data_layer,
            "resource_type": normalized,
            "action": "skip",
            "query_path": "none",
            "extract_items": False,
            "vectorize": "none",
            "authorization": authorization,
            "reason": "该资源类型在当前同步策略中被关闭。",
        }
    if data_layer == MASTER_DATA_LAYER and bitable_mode == "disabled":
        return {
            "data_layer": data_layer,
            "resource_type": normalized,
            "action": "skip",
            "query_path": "none",
            "extract_items": False,
            "vectorize": "none",
            "authorization": authorization,
            "reason": "多维表格索引在当前同步策略中被关闭。",
        }
    if data_layer == KNOWLEDGE_COLD_LAYER and normalized == "wiki" and large_document_mode == "knowledge_vectorize":
        return {
            "data_layer": data_layer,
            "resource_type": normalized,
            "action": "document_index_only",
            "query_path": "lark_cli_realtime",
            "extract_items": False,
            "vectorize": "outline_and_summary_only",
            "authorization": authorization,
            "reason": "Wiki 空间默认只登记空间和节点索引；需要发现具体 Wiki 文档 token 后再标记为高频知识切片。",
        }
    if data_layer == KNOWLEDGE_COLD_LAYER and large_document_mode == "knowledge_vectorize":
        return {
            "data_layer": "knowledge_hot",
            "resource_type": normalized,
            "action": "knowledge_vectorize",
            "query_path": "local_rag",
            "extract_items": True,
            "vectorize": "full_chunk_or_summary_chunk",
            "authorization": authorization,
            "reason": "该文档被显式标记为高频知识，可以按知识切片进入 Qdrant。",
        }
    if data_layer == EXTERNAL_WEB_LAYER:
        return {
            "data_layer": data_layer,
            "resource_type": normalized,
            "action": "external_realtime_reference",
            "query_path": "external_search_realtime",
            "extract_items": False,
            "vectorize": "none_or_summary_after_capture",
            "authorization": authorization,
            "reason": "外部 WEB 只作为外部知识引用和摘要索引，不写入内部 Document Store + Vector DB 事实层。",
        }
    if data_layer == KNOWLEDGE_COLD_LAYER:
        return {
            "data_layer": data_layer,
            "resource_type": normalized,
            "action": "document_index_only",
            "query_path": "lark_cli_realtime",
            "extract_items": False,
            "vectorize": "outline_and_summary_only",
            "authorization": authorization,
            "reason": "低频大型资料默认不实时拉取全文，只登记索引和来源。",
        }
    if data_layer == MASTER_DATA_LAYER:
        return {
            "data_layer": data_layer,
            "resource_type": normalized,
            "action": "master_data_index",
            "query_path": "lark_cli_first_then_index",
            "extract_items": False if bitable_mode == "master_data_index" else True,
            "vectorize": "key_fields_only",
            "authorization": authorization,
            "reason": "多维表格是结构化运营主数据，本地只建立索引和关键字段摘要。",
        }
    if data_layer == REALTIME_LAYER:
        return {
            "data_layer": data_layer,
            "resource_type": normalized,
            "action": "realtime_event",
            "query_path": "lark_cli_first_then_work_event_cache",
            "extract_items": True,
            "vectorize": "summary_only",
            "authorization": authorization,
            "reason": "实时/准实时工作事件进入统一时间线，并抽取任务、风险和决策。",
        }
    if data_layer == LONG_TERM_MEMORY_LAYER:
        return {
            "data_layer": data_layer,
            "resource_type": normalized,
            "action": "memory_extract_later",
            "query_path": "memory_facts",
            "extract_items": False,
            "vectorize": "fact_level",
            "authorization": authorization,
            "reason": "Memory Data 只保存稳定事实、偏好和长期上下文，不把原始对话或全文当作记忆。",
        }
    return {
        "data_layer": data_layer,
        "resource_type": normalized,
        "action": "manual_review_required",
        "query_path": "manual_review",
        "extract_items": False,
        "vectorize": "none",
        "authorization": authorization,
        "reason": "该资源类型需要人工确认同步价值和策略。",
    }
