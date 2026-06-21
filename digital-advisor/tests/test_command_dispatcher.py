import asyncio
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from app.services.feishu import command_dispatcher
from app.services.feishu.identity import BotIdentity


def _handlers(calls: list[tuple[str, object]]) -> command_dispatcher.CommandDispatchHandlers:
    async def async_reply(name: str, *args, **kwargs) -> str:
        calls.append((name, kwargs or args))
        return f"{name}-reply"

    async def quick_sync(db, app_config, question: str):
        calls.append(("quick_sync", question))
        return {}

    async def execute_pending_approval_action_reply(*args, **kwargs) -> str:
        calls.append(("execute_approval", {"args": args, "kwargs": kwargs}))
        return "execute_approval-reply"

    def daily(db, app_config):
        calls.append(("daily", app_config))
        return "daily"

    return command_dispatcher.CommandDispatchHandlers(
        help_text="owner-help",
        member_help_text=lambda identity: "member-help",
        identity_reply=lambda identity: "identity",
        bot_identity_reply=lambda identity: "bot-identity",
        online_status_reply=lambda identity: "online",
        greeting_reply=lambda identity: "hello",
        permission_denied_reply=lambda identity, resource: f"denied:{resource}",
        mail_capability_reply=lambda identity: "mail-capability",
        build_daily_report_reply=daily,
        sync_mail_reply=lambda db, app_config: async_reply("sync_mail"),
        quick_sync_for_question=quick_sync,
        recent_mail_reply=lambda db, app_config, **kwargs: f"mail:{kwargs.get('limit', 5)}",
        sync_approvals_reply=lambda db, app_config: async_reply("sync_approvals"),
        sync_contacts_reply=lambda db, app_config: async_reply("sync_contacts"),
        prepare_approval_action_reply=lambda *args, **kwargs: async_reply("prepare_approval", **kwargs),
        execute_pending_approval_action_reply=execute_pending_approval_action_reply,
        clear_pending_approval_action=lambda app_config, identity, chat_id: calls.append(("clear_pending", chat_id)),
        employee_bot_answer=lambda db, app_config, command, normalized, identity, chat_id: f"employee:{normalized}",
        employee_bot_answer_result=None,
    )


def _dispatch(
    normalized: str,
    *,
    command: str = "原始问题",
    identity: BotIdentity,
    ai_mode_enabled: bool = False,
    calls=None,
    db=None,
    app_config=None,
):
    calls = calls if calls is not None else []
    db = db or SimpleNamespace(name="db", scalar=lambda query: None)
    app_config = app_config or SimpleNamespace(name="app_config", company_id=uuid4())
    return asyncio.run(
        command_dispatcher.dispatch_command_reply(
            db,
            app_config,
            command=command,
            normalized=normalized,
            identity=identity,
            chat_id="oc_1",
            reply_target={"receive_id_type": "chat_id", "receive_id": "oc_1"},
            ai_mode_enabled=ai_mode_enabled,
            handlers=_handlers(calls),
        )
    )


def test_dispatch_routes_member_owner_cockpit_intent_to_employee_agent() -> None:
    result = _dispatch("驾驶舱概览", identity=BotIdentity(open_id="ou_1", role="member", access_scope="chat"))

    assert result.handled is True
    assert result.reply == "employee:驾驶舱概览"
    assert result.used_agent_runtime is True
    assert result.final_answer_owner == "agent_runtime"
    assert result.agent_identity["agent_owner_open_id"] == "ou_1"


def test_dispatch_owner_cockpit_uses_owner_employee_agent_runtime() -> None:
    calls: list[tuple[str, object]] = []
    result = _dispatch(
        "驾驶舱概览",
        identity=BotIdentity(open_id="ou_owner", role="owner", access_scope="company"),
        calls=calls,
    )

    assert result.handled is True
    assert result.reply == "employee:驾驶舱概览"
    assert result.used_agent_runtime is True
    assert result.final_answer_owner == "agent_runtime"
    assert result.agent_identity["agent_owner_open_id"] == "ou_owner"
    assert not any(name == "cockpit" for name, _ in calls)


def test_dispatch_admin_recent_mail_skips_pre_reply_quick_sync() -> None:
    calls: list[tuple[str, object]] = []
    result = _dispatch(
        "最近一封邮件",
        identity=BotIdentity(open_id="ou_owner", role="owner", access_scope="company"),
        calls=calls,
    )

    assert result.reply == "employee:最近一封邮件"
    assert result.used_agent_runtime is True
    assert result.final_answer_owner == "agent_runtime"
    assert ("quick_sync", "最近一封邮件") not in calls


def test_dispatch_ai_mail_question_skips_pre_reply_quick_sync() -> None:
    calls: list[tuple[str, object]] = []
    result = _dispatch(
        "我今天有新邮件吗",
        command="我今天有新邮件吗",
        identity=BotIdentity(open_id="ou_owner", role="owner", access_scope="company"),
        ai_mode_enabled=True,
        calls=calls,
    )

    assert result.reply == "employee:我今天有新邮件吗"
    assert result.used_agent_runtime is True
    assert result.final_answer_owner == "agent_runtime"
    assert not any(name == "quick_sync" for name, _ in calls)


def test_dispatch_sync_mail_delegates_final_reply_to_agent_runtime() -> None:
    calls: list[tuple[str, object]] = []
    result = _dispatch(
        "同步邮箱",
        identity=BotIdentity(open_id="ou_owner", role="owner", access_scope="company"),
        calls=calls,
    )

    assert result.handled is True
    assert result.reply == "范围：指定公司｜同步邮箱\nsync_mail-reply"
    assert result.used_agent_runtime is True
    assert result.final_answer_owner == "agent_runtime"
    assert result.route_path == "同步邮箱"
    assert result.agent_runtime_trace["steps"][1]["name"] == "sync_mail"
    assert calls[0][0] == "sync_mail"


def test_dispatch_member_recent_mail_uses_agent_runtime_authorization_boundary() -> None:
    result = _dispatch(
        "最近邮件",
        identity=BotIdentity(open_id="ou_1", role="member", access_scope="personal"),
    )

    assert result.handled is True
    assert result.reply == "employee:最近邮件"
    assert result.used_agent_runtime is True
    assert result.final_answer_owner == "agent_runtime"
    assert result.agent_identity["agent_owner_open_id"] == "ou_1"


def test_dispatch_owner_tasks_uses_agent_runtime_not_cockpit_direct_reply() -> None:
    calls: list[tuple[str, object]] = []
    result = _dispatch(
        "待办事项",
        identity=BotIdentity(open_id="ou_owner", role="owner", access_scope="company"),
        calls=calls,
    )

    assert result.handled is True
    assert result.reply == "employee:待办事项"
    assert result.used_agent_runtime is True
    assert result.final_answer_owner == "agent_runtime"
    assert not any(name == "cockpit" for name, _ in calls)


def test_dispatch_daily_report_generates_then_agent_runtime_final_answer() -> None:
    calls: list[tuple[str, object]] = []
    result = _dispatch(
        "今日日报",
        identity=BotIdentity(open_id="ou_owner", role="owner", access_scope="company"),
        calls=calls,
    )

    assert result.handled is True
    assert result.reply == "employee:今日日报"
    assert result.used_agent_runtime is True
    assert result.final_answer_owner == "agent_runtime"
    assert result.agent_identity["agent_owner_open_id"] == "ou_owner"
    assert any(name == "daily" for name, _ in calls)


def test_dispatch_member_daily_report_uses_agent_runtime_data_boundary() -> None:
    calls: list[tuple[str, object]] = []
    result = _dispatch(
        "今日日报",
        identity=BotIdentity(open_id="ou_1", role="member", access_scope="personal"),
        calls=calls,
    )

    assert result.handled is True
    assert result.reply == "employee:今日日报"
    assert result.used_agent_runtime is True
    assert result.final_answer_owner == "agent_runtime"
    assert result.agent_identity["agent_owner_open_id"] == "ou_1"
    assert not any(name == "daily" for name, _ in calls)


def test_dispatch_management_lookup_uses_employee_agent_runtime() -> None:
    calls: list[tuple[str, object]] = []
    result = _dispatch(
        "管理人员查询",
        identity=BotIdentity(open_id="ou_owner", role="owner", access_scope="company"),
        calls=calls,
    )

    assert result.handled is True
    assert result.reply == "employee:管理人员查询"
    assert result.used_agent_runtime is True
    assert result.final_answer_owner == "agent_runtime"
    assert not any(name == "executive" for name, _ in calls)


def test_dispatch_organization_uses_employee_agent_runtime_for_member_boundary() -> None:
    calls: list[tuple[str, object]] = []
    result = _dispatch(
        "组织架构",
        identity=BotIdentity(open_id="ou_1", role="member", access_scope="chat"),
        calls=calls,
    )

    assert result.handled is True
    assert result.reply == "employee:组织架构"
    assert result.used_agent_runtime is True
    assert result.final_answer_owner == "agent_runtime"
    assert result.agent_identity["agent_owner_open_id"] == "ou_1"
    assert not any(name == "organization" for name, _ in calls)


def test_dispatch_recent_approvals_uses_agent_runtime_final_answer() -> None:
    calls: list[tuple[str, object]] = []
    result = _dispatch(
        "最近审批",
        identity=BotIdentity(open_id="ou_owner", role="owner", access_scope="company"),
        calls=calls,
    )

    assert result.handled is True
    assert result.send_approval_card is True
    assert result.reply == "employee:最近审批"
    assert result.used_agent_runtime is True
    assert result.final_answer_owner == "agent_runtime"
    assert not calls


def test_dispatch_approval_advice_uses_agent_runtime_final_answer() -> None:
    calls: list[tuple[str, object]] = []
    result = _dispatch(
        "审批建议",
        identity=BotIdentity(open_id="ou_owner", role="owner", access_scope="company"),
        calls=calls,
    )

    assert result.handled is True
    assert result.reply == "employee:审批建议"
    assert result.used_agent_runtime is True
    assert result.final_answer_owner == "agent_runtime"
    assert not any(name == "approval_advice" for name, _ in calls)


def test_dispatch_approval_detail_uses_agent_runtime_final_answer() -> None:
    calls: list[tuple[str, object]] = []
    result = _dispatch(
        "审批详情请求",
        identity=BotIdentity(open_id="ou_owner", role="owner", access_scope="company"),
        calls=calls,
    )

    assert result.handled is True
    assert result.reply == "employee:审批详情请求"
    assert result.used_agent_runtime is True
    assert result.final_answer_owner == "agent_runtime"
    assert not any(name == "approval_detail" for name, _ in calls)


def test_dispatch_unknown_command_respects_ai_mode_flag() -> None:
    identity = BotIdentity(open_id="ou_1", role="member", access_scope="chat")

    assert _dispatch("未知命令", identity=identity, ai_mode_enabled=False).handled is False
    result = _dispatch("未知命令", identity=identity, ai_mode_enabled=True)

    assert result.handled is True
    assert result.reply == "employee:未知命令"


def test_dispatch_ai_mode_uses_agent_runtime_trace_when_available() -> None:
    calls: list[tuple[str, object]] = []
    handlers = _handlers(calls)
    handlers = command_dispatcher.CommandDispatchHandlers(
        **{
            **handlers.__dict__,
            "employee_bot_answer_result": lambda db, app_config, command, normalized, identity, chat_id: SimpleNamespace(
                answer="公司摘要",
                trace={
                    "route_path": "company_qa",
                    "route_label": "公司级问答",
                    "agent_identity": {
                        "agent_type": "employee_personal_agent",
                        "agent_id": "company:ou_owner",
                        "agent_owner_open_id": "ou_owner",
                        "shared_business_tool_count": 9,
                        "data_permission_model": "identity_scoped_tighten_only",
                    },
                    "steps": [{"kind": "tool", "name": "company_qa", "status": "success", "metadata": {"provider": "local"}}],
                },
            ),
        }
    )

    result = asyncio.run(
        command_dispatcher.dispatch_command_reply(
            SimpleNamespace(name="db"),
            SimpleNamespace(name="app_config"),
            command="公司情况",
            normalized="公司情况",
            identity=BotIdentity(open_id="ou_owner", role="owner", access_scope="company"),
            chat_id="oc_1",
            reply_target={"receive_id_type": "chat_id", "receive_id": "oc_1"},
            ai_mode_enabled=True,
            handlers=handlers,
        )
    )

    assert result.handled is True
    assert result.reply == "公司摘要"
    assert result.used_agent_runtime is True
    assert result.final_answer_owner == "agent_runtime"
    assert result.route_path == "company_qa"
    assert result.route_label == "公司级问答"
    assert result.agent_identity["agent_id"] == "company:ou_owner"
    assert result.agent_identity["agent_type"] == "employee_personal_agent"
    assert result.agent_identity["shared_business_tool_count"] == 9
    assert result.agent_runtime_trace["route_path"] == "company_qa"


def test_dispatch_meta_command_still_exposes_employee_agent_identity() -> None:
    company_id = "company_1"
    result = _dispatch(
        "身份确认",
        identity=BotIdentity(open_id="ou_1", role="member", access_scope="personal", display_name="王工"),
        app_config=SimpleNamespace(company_id=company_id),
    )

    assert result.handled is True
    assert result.used_agent_runtime is True
    assert result.final_answer_owner == "agent_runtime"
    assert result.route_path == "general_chat"
    assert result.route_label == "基础沟通"
    assert result.agent_identity["agent_type"] == "employee_personal_agent"
    assert result.agent_identity["agent_id"] == "company_1:ou_1"
    assert result.agent_identity["agent_owner_open_id"] == "ou_1"
    assert result.agent_identity["agent_owner_display_name"] == "王工"
    assert result.agent_identity["shared_business_tool_count"] == 9
    assert result.agent_identity["data_access_scope"] == "personal"
    assert result.agent_identity["user_identity_constraints"] == ["resource_owner_authorization"]
    assert result.agent_runtime_trace["reply_mode"]["mode_id"] == "fast"
    assert result.agent_runtime_trace["steps"][1]["name"] == "meta_identity"


def test_env_example_enables_feishu_bot_ai_mode_for_launch() -> None:
    env_example = Path(".env.example").read_text()

    assert "FEISHU_BOT_AI_MODE_ENABLED=true" in env_example


def test_dispatch_prepare_approval_approve_delegates_final_reply_to_agent_runtime() -> None:
    calls: list[tuple[str, object]] = []
    db = SimpleNamespace(name="db", scalar=lambda query: None)
    app_config = SimpleNamespace(name="app_config", company_id=uuid4())
    identity = BotIdentity(open_id="ou_owner", role="owner", access_scope="company")

    result = _dispatch(
        "审批通过请求",
        identity=identity,
        calls=calls,
        db=db,
        app_config=app_config,
    )

    assert result.handled is True
    assert result.reply == "范围：指定公司｜审批通过\n思考路径：\n1. 识别问题范围：指定公司\n2. 选择能力路径：审批通过\n3. 调用数据层/工具：WorkEvent / Knowledge / Memory / 多 Tool 分析\n4. 汇总结论、依据和下一步\n\nprepare_approval-reply"
    assert result.used_agent_runtime is True
    assert result.final_answer_owner == "agent_runtime"
    assert result.route_path == "feishu_approval_task_approve"
    assert calls[0][0] == "prepare_approval"
    assert calls[0][1]["action"] == "approve"


def test_dispatch_confirm_approval_passes_db_context_and_expected_action() -> None:
    calls: list[tuple[str, object]] = []
    db = SimpleNamespace(name="db", scalar=lambda query: None)
    app_config = SimpleNamespace(name="app_config", company_id=uuid4())
    identity = BotIdentity(open_id="ou_owner", role="owner", access_scope="company")

    result = _dispatch(
        "确认审批通过",
        identity=identity,
        calls=calls,
        db=db,
        app_config=app_config,
    )

    assert result.handled is True
    assert result.reply == "范围：指定公司｜审批通过\n思考路径：\n1. 识别问题范围：指定公司\n2. 选择能力路径：审批通过\n3. 调用数据层/工具：WorkEvent / Knowledge / Memory / 多 Tool 分析\n4. 汇总结论、依据和下一步\n\nexecute_approval-reply"
    assert result.used_agent_runtime is True
    assert result.final_answer_owner == "agent_runtime"
    assert result.route_path == "feishu_approval_task_approve"
    assert result.route_label == "审批通过"
    assert result.agent_runtime_trace["steps"][1]["kind"] == "workflow"
    assert result.agent_runtime_trace["steps"][1]["name"] == "approval_execute_approve"
    assert calls[0][0] == "execute_approval"
    assert calls[0][1]["args"] == (db, app_config, identity)
    assert calls[0][1]["kwargs"] == {
        "chat_id": "oc_1",
        "expected_action": "approve",
    }


def test_dispatch_cancel_approval_delegates_final_reply_to_agent_runtime() -> None:
    calls: list[tuple[str, object]] = []
    db = SimpleNamespace(name="db", scalar=lambda query: None)
    app_config = SimpleNamespace(name="app_config", company_id=uuid4())
    identity = BotIdentity(open_id="ou_owner", role="owner", access_scope="company")

    result = _dispatch(
        "取消审批操作",
        identity=identity,
        calls=calls,
        db=db,
        app_config=app_config,
    )

    assert result.handled is True
    assert result.reply.startswith("范围：指定公司｜审批问答\n思考路径：")
    assert result.reply.endswith("已取消刚才准备执行的审批操作。")
    assert result.used_agent_runtime is True
    assert result.final_answer_owner == "agent_runtime"
    assert result.route_path == "approval_qa"
    assert calls == [("clear_pending", "oc_1")]


def test_dispatch_confirm_approval_rejects_member_before_execute() -> None:
    calls: list[tuple[str, object]] = []
    identity = BotIdentity(open_id="ou_1", role="member", access_scope="chat")

    result = _dispatch("确认审批拒绝", identity=identity, calls=calls)

    assert result.handled is True
    assert result.reply == "denied:审批操作"
    assert not calls
