import asyncio
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

from celery import Celery
from celery.schedules import crontab
from sqlalchemy import select

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.entities import Account, FeishuAppConfig, Resource, WorkEvent
from app.services.ai.extraction import extract_items_for_event
from app.services.ai.memory import generate_memory_facts_for_recent_events
from app.services.ai.reports import generate_daily_report
from app.services.feishu import FeishuClient, sync_feishu_information
from app.services.feishu.resources import discover_feishu_resources
from app.services.gateway.responder import send_feishu_text_reply
from app.services.audit import write_audit_log
from app.services.integrations.mail import ImapMailClient
from app.services.ai.vector_search import WorkEventVectorIndex, mark_event_vector_indexing_result, should_vectorize_work_event
from app.services.sync_runs import finish_sync_run, start_sync_run
from app.services.v5_auto_sync import company_auto_sync_due, select_company_auto_sync_resources
from app.services.v5_auto_sync import sync_company_auto_resources
from app.services.v5_sync_policy import enabled_v5_resource_sync_policies

celery_app = Celery(
    "feishu_mail_advisor",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.beat_schedule = {
    "auto-imap-sync": {
        "task": "mail.imap.sync_auto",
        "schedule": settings.auto_imap_interval_seconds,
    },
    "auto-daily-report": {
        "task": "reports.daily_auto",
        "schedule": crontab(
            hour=settings.auto_daily_report_hour,
            minute=settings.auto_daily_report_minute,
        ),
    },
    "auto-feishu-sync": {
        "task": "feishu.sync_auto",
        "schedule": settings.auto_feishu_sync_interval_seconds,
    },
    "auto-v5-resource-sync": {
        "task": "v5.resources.sync_auto",
        "schedule": settings.auto_v5_resource_sync_interval_seconds,
    },
    "people-snapshot-prewarm": {
        "task": "people.snapshot.prewarm",
        "schedule": 1800,
    },
    "approval-snapshot-builder-events": {
        "task": "approval.snapshot.build_from_events",
        "schedule": 15,
    },
}
celery_app.conf.timezone = "Asia/Shanghai"


@celery_app.task(name="work_events.extract")
def extract_work_event_task(event_id: str) -> dict:
    db = SessionLocal()
    try:
        event = db.get(WorkEvent, UUID(event_id))
        if not event:
            return {"count": 0}
        items = extract_items_for_event(db, event)
        db.commit()
        return {"count": len(items)}
    finally:
        db.close()


@celery_app.task(name="work_events.vectorize")
def vectorize_work_event_task(event_id: str) -> dict:
    db = SessionLocal()
    try:
        event = db.get(WorkEvent, UUID(event_id))
        if not event:
            return {"ok": False}
        ok = WorkEventVectorIndex().upsert_event(event)
        mark_event_vector_indexing_result(event, indexed=ok)
        db.commit()
        return {"ok": ok}
    finally:
        db.close()


@celery_app.task(name="work_events.vectorize_pending")
def vectorize_pending_work_events_task(limit: int = 200) -> dict:
    db = SessionLocal()
    try:
        events = list(
            db.scalars(
                select(WorkEvent)
                .where(WorkEvent.vector_status == "pending")
                .order_by(WorkEvent.occurred_at.desc())
                .limit(limit)
            ).all()
        )
        indexed = 0
        skipped = 0
        index = WorkEventVectorIndex()
        for event in events:
            if not should_vectorize_work_event(event):
                mark_event_vector_indexing_result(event, indexed=False)
                skipped += 1
                continue
            ok = index.upsert_event(event)
            mark_event_vector_indexing_result(event, indexed=ok)
            indexed += 1 if ok else 0
            skipped += 0 if ok else 1
        db.commit()
        return {"indexed": indexed, "skipped": skipped, "count": len(events)}
    finally:
        db.close()


@celery_app.task(name="reports.daily")
def daily_report_task(report_date: str, company_id: str | None = None) -> dict:
    db = SessionLocal()
    try:
        report = generate_daily_report(
            db,
            report_date=date.fromisoformat(report_date),
            company_id=UUID(company_id) if company_id else None,
        )
        db.commit()
        return {"report_id": str(report.id)}
    finally:
        db.close()


@celery_app.task(name="memory.generate_recent")
def generate_recent_memory_task(company_id: str | None = None, limit: int = 100) -> dict:
    db = SessionLocal()
    try:
        facts = generate_memory_facts_for_recent_events(
            db,
            company_id=UUID(company_id) if company_id else None,
            limit=limit,
        )
        db.commit()
        return {"count": len(facts), "memory_fact_ids": [str(fact.id) for fact in facts]}
    finally:
        db.close()


@celery_app.task(name="mail.imap.sync_auto")
def auto_imap_sync_task() -> dict:
    if not settings.auto_imap_sync_enabled:
        return {"enabled": False, "saved_count": 0}

    db = SessionLocal()
    try:
        accounts = _configured_imap_accounts(db)
        saved_ids: list[str] = []
        errors: list[dict[str, str]] = []
        for account in accounts:
            account_id = account.id
            company_id = account.company_id
            sync_run = start_sync_run(
                db,
                company_id=company_id,
                provider="imap",
                sync_type="auto",
                cursor={"account_id": str(account_id), "folder": settings.auto_imap_folder},
            )
            try:
                account_saved_ids = ImapMailClient(account).sync(
                    db,
                    folder=settings.auto_imap_folder,
                    limit=settings.auto_imap_limit,
                )
                saved_ids.extend(account_saved_ids)
                for event_id in account_saved_ids:
                    event = db.get(WorkEvent, UUID(event_id))
                    if event:
                        extract_items_for_event(db, event)
                finish_sync_run(
                    sync_run,
                    status="success",
                    saved_count=len(account_saved_ids),
                    summary={"work_event_ids": account_saved_ids},
                )
                db.commit()
            except Exception as exc:
                db.rollback()
                _record_failed_sync_run(
                    company_id=company_id,
                    provider="imap",
                    sync_type="auto",
                    cursor={"account_id": str(account_id), "folder": settings.auto_imap_folder},
                    error=str(exc)[:500],
                )
                errors.append({"account_id": str(account_id), "error": str(exc)[:500]})
        return {
            "enabled": True,
            "account_count": len(accounts),
            "saved_count": len(saved_ids),
            "work_event_ids": saved_ids,
            "errors": errors,
        }
    finally:
        db.close()


@celery_app.task(name="reports.daily_auto")
def auto_daily_report_task() -> dict:
    if not settings.auto_daily_report_enabled:
        return {"enabled": False, "report_ids": []}

    db = SessionLocal()
    try:
        report_date = datetime.now(UTC).date()
        company_ids = _configured_company_ids(db)
        report_ids: list[str] = []
        for company_id in company_ids:
            report = generate_daily_report(db, report_date=report_date, company_id=company_id)
            report_ids.append(str(report.id))
            db.commit()
            if settings.auto_daily_report_push_feishu:
                _push_report_to_feishu(db, report.content_markdown)
        return {"enabled": True, "report_ids": report_ids}
    finally:
        db.close()


@celery_app.task(name="feishu.sync_auto")
def auto_feishu_sync_task() -> dict:
    if not settings.auto_feishu_sync_enabled:
        return {"enabled": False, "results": {}}

    db = SessionLocal()
    try:
        app_configs = _configured_feishu_apps(db)
        kinds = _parse_csv(settings.auto_feishu_sync_kinds)
        if "mail" in kinds and not settings.auto_feishu_mail_user_mailbox_id:
            kinds = [kind for kind in kinds if kind != "mail"]
        results: dict[str, dict] = {}
        for app_config in app_configs:
            try:
                results[str(app_config.id)] = asyncio.run(
                    sync_feishu_information(
                        db,
                        app_config,
                        kinds=kinds,
                        limit=settings.auto_feishu_sync_limit,
                        max_pages=3,
                        user_mailbox_id=settings.auto_feishu_mail_user_mailbox_id,
                        folder_id=settings.auto_feishu_mail_folder_id,
                        extract_items=True,
                    )
                )
            except Exception as exc:
                db.rollback()
                results[str(app_config.id)] = {"error": str(exc)[:500]}
        return {"enabled": True, "app_count": len(app_configs), "results": results}
    finally:
        db.close()


@celery_app.task(name="feishu.resources.discover")
def discover_feishu_resources_task(app_config_id: str, payload: dict) -> dict:
    db = SessionLocal()
    try:
        app_config = db.get(FeishuAppConfig, UUID(app_config_id))
        if not app_config:
            return {"ok": False, "error": "Feishu app config not found"}
        result = asyncio.run(
            discover_feishu_resources(
                db,
                app_config,
                kinds=payload.get("kinds") or [],
                mailbox_id=payload.get("mailbox_id"),
                mailbox_ids=payload.get("mailbox_ids") or [],
                app_tokens=payload.get("app_tokens") or [],
                bitable_tables=payload.get("bitable_tables") or [],
                document_ids=payload.get("document_ids") or [],
                wiki_space_ids=payload.get("wiki_space_ids") or [],
                folder_tokens=payload.get("folder_tokens") or [],
                docs_search_keywords=payload.get("docs_search_keywords") or [],
                approval_codes=payload.get("approval_codes") or [],
                limit=int(payload.get("limit") or 50),
                include_local_mining=bool(payload.get("include_local_mining", True)),
            )
        )
        write_audit_log(
            db,
            action="feishu.resources.discover.async",
            company_id=app_config.company_id,
            target_type="feishu_app",
            target_id=str(app_config.id),
            payload={
                "kinds": payload.get("kinds") or [],
                "saved_count": result.get("saved_count", 0),
                "error_count": len(result.get("errors") or []),
            },
        )
        db.commit()
        return {
            "ok": True,
            "saved_count": result.get("saved_count", 0),
            "coverage": result.get("coverage") or {},
            "error_count": len(result.get("errors") or []),
            "next_steps": result.get("next_steps") or [],
        }
    except Exception as exc:
        db.rollback()
        return {"ok": False, "error": str(exc)[:500]}
    finally:
        db.close()


@celery_app.task(name="people.snapshot.prewarm")
def people_snapshot_prewarm_task() -> dict:
    db = SessionLocal()
    try:
        from app.services.feishu.cli_profile import feishu_app_cli_profile
        from app.services.runtime_v5.context import build_runtime_context
        from app.services.runtime_v5.feishu_resource_providers import build_feishu_provider_registry
        from app.services.runtime_v5.models import IntentResult, PlannerResult, ProviderRequest

        app_configs = _active_feishu_apps(db)
        results: dict[str, dict[str, Any]] = {}
        for app_config in app_configs:
            try:
                runtime_context = build_runtime_context(
                    message="系统预热通讯录组织快照",
                    identity={"open_id": "system", "user_id": "system", "name": "system", "role": "owner", "domains": ["all"]},
                    company_id=app_config.company_id,
                )
                provider = build_feishu_provider_registry(db=db, cli_profile=feishu_app_cli_profile(app_config))["people"]
                result = provider.execute(
                    ProviderRequest(
                        source="people",
                        operation="get_org_snapshot",
                        intent=IntentResult(
                            question_type="query",
                            intent="organization_snapshot",
                            data_scope="organization",
                            entities={"view": "people_aggregate", "people_query_mode": "count"},
                            confidence=1.0,
                            canonical_question="系统预热通讯录组织快照",
                        ),
                        planner=PlannerResult(strategy="organization_snapshot", sources=("people",)),
                        context=runtime_context,
                        execution_identity="bot",
                    )
                )
                results[str(app_config.id)] = {
                    "status": result.status,
                    "count": result.count,
                    "department_count": result.metadata.get("department_count", 0),
                    "cache_hit": result.metadata.get("cache_hit", False),
                    "fetch_ms": result.metadata.get("fetch_ms", 0),
                    "error": result.error,
                }
            except Exception as exc:
                db.rollback()
                results[str(app_config.id)] = {"status": "error", "error": str(exc)[:500]}
        return {"enabled": True, "app_count": len(app_configs), "results": results}
    finally:
        db.close()


_BOT_APPROVALS_RECENT_REPLY_TASK = ".".join(("bot", "approvals", "recent_reply"))
_BOT_APPROVALS_WORKBENCH_TASK = ".".join(("bot", "approvals", "workbench_reply"))
_BOT_RUNTIME_CARD_REPLY_TASK = ".".join(("bot", "runtime", "card_reply"))
_BOT_RUNTIME_ASYNC_FOLLOWUP_TASK = ".".join(("bot", "runtime", "async_followup"))
_BOT_APPROVALS_BATCH_APPROVE_TASK = ".".join(("bot", "approvals", "batch_approve"))
_APPROVAL_SNAPSHOT_BUILD_TASK = ".".join(("approval", "snapshot", "build"))
_APPROVAL_SNAPSHOT_BUILD_FROM_EVENTS_TASK = ".".join(("approval", "snapshot", "build_from_events"))


@celery_app.task(name=_APPROVAL_SNAPSHOT_BUILD_TASK)
def approval_snapshot_build_task(company_id: str, item: dict, actor: str = "system") -> dict:
    db = SessionLocal()
    try:
        from app.services.approval_snapshot_builder import build_approval_snapshot

        result = build_approval_snapshot(db, company_id=company_id, item=item, actor=actor)
        db.commit()
        return result
    except Exception as exc:
        db.rollback()
        return {"ok": False, "error": str(exc)[:500]}
    finally:
        db.close()


@celery_app.task(name=_APPROVAL_SNAPSHOT_BUILD_FROM_EVENTS_TASK)
def approval_snapshot_build_from_events_task(limit: int = 10) -> dict:
    db = SessionLocal()
    try:
        from app.services.approval_snapshot_builder import build_approval_snapshots_from_work_events

        result = build_approval_snapshots_from_work_events(db, limit=limit)
        db.commit()
        return result
    except Exception as exc:
        db.rollback()
        return {"ok": False, "error": str(exc)[:500]}
    finally:
        db.close()


@celery_app.task(name=_BOT_APPROVALS_RECENT_REPLY_TASK)
def bot_approvals_recent_reply_task() -> dict:
    return {
        "ok": False,
        "status": "retired",
        "reason": "recent approval replies are handled by the approval card entrypoint",
    }


@celery_app.task(name=_BOT_APPROVALS_WORKBENCH_TASK)
def bot_approvals_workbench_reply_task(
    app_config_id: str,
    question: str,
    normalized: str,
    identity_payload: dict,
    chat_id: str | None,
    reply_target: dict,
    message_id: str | None = None,
) -> dict:
    db = SessionLocal()
    try:
        from app.services.feishu import bot_runtime
        from app.services.feishu import replies as feishu_replies
        from app.services.feishu.identity import BotIdentity
        from app.services.runtime_v5.action_observer import record_action_trace
        from app.services.runtime_v5.context import clear_result_context, load_session_context

        app_config = db.get(FeishuAppConfig, UUID(app_config_id))
        if not app_config:
            return {"ok": False, "error": "Feishu app config not found"}
        identity = BotIdentity(
            open_id=identity_payload.get("open_id"),
            role=str(identity_payload.get("role") or "member"),
            access_scope=str(identity_payload.get("access_scope") or "personal"),
            display_name=identity_payload.get("display_name"),
            source=str(identity_payload.get("source") or "runtime_v5_background"),
            domains=tuple(identity_payload.get("domains") or ()),
            allowed_resources=tuple(identity_payload.get("allowed_resources") or ()),
            email=identity_payload.get("email"),
        )
        record_action_trace(
            chat_id,
            {
                "kind": "approval_workbench",
                "action": "approval_workbench",
                "strategy": "approval_query",
                "sources": ["approval"],
                "source": "approval",
                "status": "started",
                "execution_identity": "bot",
                "requires_confirmation": False,
                "route_path": "feishu_approval_task_query",
            },
        )
        runtime_answer = bot_runtime.employee_bot_approval_workbench_answer_result(
            db,
            app_config,
            question,
            normalized,
            identity,
            chat_id,
        )
        trace = runtime_answer.trace_payload if isinstance(runtime_answer.trace_payload, dict) else {}
        execution_status = str(trace.get("execution_status") or "")
        question_type = str(trace.get("question_type") or "")
        strategy = str(trace.get("strategy") or "")
        timing = _runtime_trace_timing(trace)
        record_action_trace(
            chat_id,
            {
                "kind": "approval_workbench",
                "action": "approval_workbench",
                "strategy": strategy or "approval_query",
                "sources": ["approval"],
                "source": "approval",
                "status": execution_status or "unknown",
                "execution_identity": "bot",
                "requires_confirmation": False,
                "route_path": str(trace.get("route_path") or ""),
                **timing,
            },
        )
        if question_type == "action" and execution_status == "success" and _should_clear_result_context_after_action(strategy):
            clear_result_context(chat_id, reason="runtime_action_success", source=strategy or "runtime_card_reply")
        if message_id and chat_id:
            latest_message_id = str(load_session_context(chat_id).get("runtime_v5_latest_user_message_id") or "")
            if latest_message_id and latest_message_id != str(message_id):
                record_action_trace(
                    chat_id,
                    {
                        "kind": "approval_workbench",
                        "action": "approval_workbench",
                        "status": "skipped",
                        "reason": "superseded_by_new_user_message",
                        "route_path": "feishu_approval_task_query",
                    },
                )
                db.commit()
                return {"ok": True, "skipped": True, "reason": "superseded_by_new_user_message"}
        asyncio.run(
            feishu_replies.send_smart_reply(
                app_config=app_config,
                reply_target=reply_target,
                reply=_append_runtime_timing_hint(runtime_answer.answer, timing),
                route_path=str(trace.get("route_path") or "feishu_approval_task_query"),
                chat_id=chat_id,
            )
        )
        db.commit()
        return {"ok": True, "route_path": trace.get("route_path"), "answer_chars": len(runtime_answer.answer)}
    except Exception as exc:
        db.rollback()
        try:
            from app.services.feishu import replies as feishu_replies
            from app.services.runtime_v5.action_observer import record_action_trace

            record_action_trace(
                chat_id,
                {
                    "kind": "approval_workbench",
                    "action": "approval_query",
                    "status": "error",
                    "route_path": "feishu_approval_task_query",
                },
            )

            app_config = db.get(FeishuAppConfig, UUID(app_config_id))
            if app_config:
                asyncio.run(
                    feishu_replies.send_text_reply(
                        app_config=app_config,
                        reply_target=reply_target,
                        text=f"待审批工作台生成失败：{str(exc)[:180]}。请重新查询待审批后再试。",
                    )
                )
        except Exception:
            pass
        return {"ok": False, "error": str(exc)[:500]}
    finally:
        db.close()


@celery_app.task(name=_BOT_RUNTIME_CARD_REPLY_TASK)
def bot_runtime_card_reply_task(
    app_config_id: str,
    command: str,
    normalized: str,
    identity_payload: dict,
    chat_id: str | None,
    reply_target: dict,
) -> dict:
    db = SessionLocal()
    try:
        from app.services.feishu import bot_runtime
        from app.services.feishu import authorization_card_entrypoint
        from app.services.feishu import replies as feishu_replies
        from app.services.feishu.identity import BotIdentity
        from app.services.runtime_v5.action_observer import record_action_trace, write_runtime_action_audit
        from app.services.runtime_v5.context import clear_result_context, load_session_context, save_session_context

        app_config = db.get(FeishuAppConfig, UUID(app_config_id))
        if not app_config:
            return {"ok": False, "error": "Feishu app config not found"}
        identity = BotIdentity(
            open_id=identity_payload.get("open_id"),
            role=str(identity_payload.get("role") or "member"),
            access_scope=str(identity_payload.get("access_scope") or "personal"),
            display_name=identity_payload.get("display_name"),
            source=str(identity_payload.get("source") or "runtime_v5_card_action"),
            domains=tuple(identity_payload.get("domains") or ()),
            allowed_resources=tuple(identity_payload.get("allowed_resources") or ()),
            email=identity_payload.get("email"),
        )
        runtime_answer = bot_runtime.employee_bot_answer_result(
            db,
            app_config,
            command,
            normalized,
            identity,
            chat_id,
        )
        trace = runtime_answer.trace_payload if isinstance(runtime_answer.trace_payload, dict) else {}
        execution_status = str(trace.get("execution_status") or "")
        receipt_status = execution_status or "success"
        strategy = str(trace.get("strategy") or normalized or command or "")
        timing = _runtime_trace_timing(trace)
        pending_action = load_session_context(chat_id).get("runtime_v5_pending_action") if chat_id else None
        action_id = str(pending_action.get("id") or uuid4().hex) if isinstance(pending_action, dict) else uuid4().hex
        record_action_trace(
            chat_id,
            {
                "kind": "runtime_confirmation",
                "action": strategy,
                "status": receipt_status,
                "action_id": action_id,
                "route_path": str(trace.get("route_path") or ""),
                **timing,
            },
        )
        write_runtime_action_audit(
            db,
            action="runtime_v5.action.confirmed",
            company_id=app_config.company_id,
            actor=identity.open_id,
            target_type="runtime_strategy",
            target_id=strategy,
            payload={
                "chat_id": chat_id,
                "status": receipt_status,
                "route_path": trace.get("route_path"),
                "question_type": trace.get("question_type"),
                "data_scope": trace.get("data_scope"),
                **timing,
            },
        )
        if (
            trace.get("question_type") == "action"
            and execution_status == "success"
            and _should_clear_result_context_after_action(strategy)
        ):
            clear_result_context(chat_id, reason="runtime_action_success", source=strategy or "runtime_workbench_reply")
            _clear_runtime_current_approval_item(chat_id, load_session_context=load_session_context, save_session_context=save_session_context)
        _save_runtime_card_action_receipt(
            chat_id=chat_id,
            action_id=action_id,
            strategy=strategy,
            status=receipt_status,
            answer=runtime_answer.answer,
            route_path=str(trace.get("route_path") or "runtime_v5"),
            timing=timing,
        )
        authorization_actions = _authorization_actions_from_runtime_result(trace)
        authorization_card_sent = False
        if authorization_actions:
            authorization_card_sent = asyncio.run(
                authorization_card_entrypoint.send_user_identity_authorization_card(
                    app_config,
                    identity,
                    reply_target,
                    answer=runtime_answer.answer,
                    actions=authorization_actions,
                )
            )
        if not authorization_card_sent:
            asyncio.run(
                feishu_replies.send_smart_reply(
                    app_config=app_config,
                    reply_target=reply_target,
                    reply=_append_runtime_timing_hint(runtime_answer.answer, timing),
                    route_path=str(trace.get("route_path") or "runtime_v5"),
                    chat_id=chat_id,
                )
        )
        followup_payload = _runtime_async_followup_payload(
            trace=trace,
            question=command,
            answer=runtime_answer.answer,
            chat_id=chat_id,
        )
        if followup_payload:
            bot_runtime_async_followup_task.apply_async(
                args=[app_config_id, followup_payload, reply_target],
                countdown=1,
            )
        db.commit()
        return {"ok": True, "route_path": trace.get("route_path"), "answer_chars": len(runtime_answer.answer)}
    except Exception as exc:
        db.rollback()
        try:
            from app.services.feishu import replies as feishu_replies
            from app.services.runtime_v5.action_observer import record_action_trace
            from app.services.runtime_v5.context import load_session_context

            pending_action = load_session_context(chat_id).get("runtime_v5_pending_action") if chat_id else None
            action_id = str(pending_action.get("id") or uuid4().hex) if isinstance(pending_action, dict) else uuid4().hex
            answer = f"操作执行失败：{str(exc)[:180]}。你可以重新发起操作或稍后再试。"
            record_action_trace(
                chat_id,
                {
                    "kind": "runtime_confirmation",
                    "action": normalized or command,
                    "status": "error",
                    "route_path": "runtime_v5",
                    "action_id": action_id,
                },
            )
            _save_runtime_card_action_receipt(
                chat_id=chat_id,
                action_id=action_id,
                strategy=normalized or command or "runtime_action",
                status="error",
                answer=answer,
                route_path="runtime_v5",
                timing={},
            )
            app_config = db.get(FeishuAppConfig, UUID(app_config_id))
            if app_config:
                asyncio.run(
                    feishu_replies.send_text_reply(
                        app_config=app_config,
                        reply_target=reply_target,
                        text=answer,
                    )
                )
        except Exception:
            pass
        return {"ok": False, "error": str(exc)[:500]}
    finally:
        db.close()


@celery_app.task(name=_BOT_RUNTIME_ASYNC_FOLLOWUP_TASK)
def bot_runtime_async_followup_task(
    app_config_id: str,
    payload: dict,
    reply_target: dict,
) -> dict:
    db = SessionLocal()
    try:
        from app.services.feishu import replies as feishu_replies
        from app.services.runtime_v5.async_followup import build_async_followup_text

        app_config = db.get(FeishuAppConfig, UUID(app_config_id))
        if not app_config:
            return {"ok": False, "error": "Feishu app config not found"}
        text = build_async_followup_text(payload)
        if not text:
            return {"ok": True, "skipped": True, "reason": "empty_followup"}
        asyncio.run(
            feishu_replies.send_smart_reply(
                app_config=app_config,
                reply_target=reply_target,
                reply=text,
                route_path=str(payload.get("route_path") or "runtime_async_followup"),
                chat_id=str(payload.get("chat_id") or ""),
            )
        )
        return {"ok": True, "answer_chars": len(text)}
    finally:
        db.close()


def _runtime_async_followup_payload(
    *,
    trace: dict[str, Any],
    question: str,
    answer: str,
    chat_id: str | None,
) -> dict[str, Any] | None:
    from app.services.runtime_v5.async_followup import should_schedule_async_followup

    runtime_result = _runtime_result_from_trace(trace)
    if not should_schedule_async_followup(runtime_result):
        return None
    metadata = runtime_result.get("metadata") if isinstance(runtime_result.get("metadata"), dict) else {}
    return {
        "question": question,
        "answer": answer,
        "chat_id": chat_id,
        "route_path": str(trace.get("route_path") or "runtime_async_followup"),
        "strategy": str(metadata.get("strategy") or trace.get("strategy") or ""),
        "result_type": str(runtime_result.get("result_type") or trace.get("result_type") or ""),
        "data_scope": str(metadata.get("data_scope") or trace.get("data_scope") or ""),
        "response_policy": metadata.get("response_policy") if isinstance(metadata.get("response_policy"), dict) else {},
    }


def _runtime_result_from_trace(trace: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(trace, dict):
        return {}
    composed = trace.get("composed") if isinstance(trace.get("composed"), dict) else {}
    metadata = composed.get("metadata") if isinstance(composed.get("metadata"), dict) else {}
    runtime_result = metadata.get("runtime_result") if isinstance(metadata.get("runtime_result"), dict) else {}
    return runtime_result


def _authorization_actions_from_runtime_result(trace_payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(trace_payload, dict):
        return []
    composed = trace_payload.get("composed") if isinstance(trace_payload.get("composed"), dict) else {}
    metadata = composed.get("metadata") if isinstance(composed.get("metadata"), dict) else {}
    runtime_result = metadata.get("runtime_result") if isinstance(metadata.get("runtime_result"), dict) else {}
    actions = runtime_result.get("actions") if isinstance(runtime_result.get("actions"), list) else []
    authorization = runtime_result.get("metadata", {}).get("authorization") if isinstance(runtime_result.get("metadata"), dict) else {}
    normalized: list[dict[str, Any]] = []
    for action in actions:
        if not isinstance(action, dict) or action.get("action") != "authorize_user_identity":
            continue
        normalized.append(
            {
                "resource_type": action.get("resource_type") or "user_identity_bundle",
                "label": action.get("label") or "授权个人能力包",
                "channel": action.get("channel") or "feishu_oauth",
                "url": action.get("url") or (authorization.get("url") if isinstance(authorization, dict) else ""),
                "authorization_flow": action.get("authorization_flow") or (authorization.get("authorization_flow") if isinstance(authorization, dict) else ""),
                "covered_resources": authorization.get("covered_resources", []) if isinstance(authorization, dict) else [],
                "owner_open_id": authorization.get("owner_open_id", "") if isinstance(authorization, dict) else "",
                "authorization_status": action.get("authorization_status") or (authorization.get("authorization_status") if isinstance(authorization, dict) else ""),
                "provider_boundary": authorization.get("provider_boundary", "") if isinstance(authorization, dict) else "",
            }
        )
    return [action for action in normalized if str(action.get("url") or "").strip()]


@celery_app.task(name=_BOT_APPROVALS_BATCH_APPROVE_TASK)
def bot_approvals_batch_approve_task(
    app_config_id: str,
    identity_payload: dict,
    chat_id: str | None,
    reply_target: dict,
) -> dict:
    action_id = ""
    db = SessionLocal()
    try:
        from app.services.feishu import replies as feishu_replies
        from app.services.feishu.cli_profile import feishu_app_cli_profile
        from app.services.feishu.identity import BotIdentity
        from app.services.runtime_v5.action_observer import record_action_trace, write_runtime_action_audit
        from app.services.runtime_v5.context import build_runtime_context, clear_result_context, load_session_context, save_result_context, save_session_context
        from app.services.runtime_v5.feishu_resource_providers import build_feishu_provider_registry
        from app.services.runtime_v5.models import IntentResult, PlannerResult, ProviderRequest, ResultContext

        app_config = db.get(FeishuAppConfig, UUID(app_config_id))
        if not app_config:
            return {"ok": False, "error": "Feishu app config not found"}
        identity = BotIdentity(
            open_id=identity_payload.get("open_id"),
            role=str(identity_payload.get("role") or "member"),
            access_scope=str(identity_payload.get("access_scope") or "personal"),
            display_name=identity_payload.get("display_name"),
            source=str(identity_payload.get("source") or "runtime_v5_batch_approval"),
            domains=tuple(identity_payload.get("domains") or ()),
            allowed_resources=tuple(identity_payload.get("allowed_resources") or ()),
            email=identity_payload.get("email"),
        )
        session_context = load_session_context(chat_id)
        pending = session_context.get("runtime_v5_pending_approval_batch")
        entries = pending.get("items") if isinstance(pending, dict) else None
        action_id = str(pending.get("id") or uuid4().hex) if isinstance(pending, dict) else uuid4().hex
        if not isinstance(entries, list) or not entries:
            _save_runtime_card_action_receipt(
                chat_id=chat_id,
                action_id=action_id,
                strategy="approval_batch_approve",
                status="stale",
                answer="批量审批已过期，请重新查询并选择。",
                route_path="feishu_approval_task_query",
                timing={},
            )
            asyncio.run(
                feishu_replies.send_text_reply(
                    app_config=app_config,
                    reply_target=reply_target,
                    text="批量审批已过期，请重新查询并选择。",
                )
            )
            return {"ok": False, "error": "batch_expired"}

        runtime_context = build_runtime_context(
            message="批量通过已选审批",
            identity=identity,
            company_id=app_config.company_id,
            chat_id=chat_id,
        )
        provider = build_feishu_provider_registry(db=db, cli_profile=feishu_app_cli_profile(app_config))["approval"]
        plan = PlannerResult(strategy="approval_approve", sources=("approval",))
        success: list[str] = []
        failed: list[str] = []
        for entry in entries:
            item = entry.get("item") if isinstance(entry, dict) and isinstance(entry.get("item"), dict) else {}
            index = entry.get("index") if isinstance(entry, dict) else ""
            title = str(item.get("title") or "未命名审批")
            intent = IntentResult(
                question_type="action",
                intent="approval_approve",
                data_scope="self",
                entities={"item": item, "comment": "同意"},
                confidence=1.0,
                canonical_question="批量通过已选审批",
            )
            result = provider.execute(
                ProviderRequest(
                    source="approval",
                    operation="approve",
                    intent=intent,
                    planner=plan,
                    context=runtime_context,
                    execution_identity="user",
                    params={"item": item, "comment": "同意"},
                )
            )
            label = f"{index}. {title}" if index else title
            if result.status == "success":
                success.append(label)
            else:
                failed.append(f"{label}：{result.error or result.answer or '提交失败'}")

        session_context.pop("runtime_v5_pending_approval_batch", None)
        session_context.pop("runtime_v5_approval_batch_selection", None)
        save_session_context(chat_id, session_context)
        record_action_trace(
            chat_id,
            {
                "kind": "approval_batch",
                "action": "approve",
                "strategy": "approval_approve",
                "sources": ["approval"],
                "source": "approval",
                "status": "success" if not failed else "partial" if success else "error",
                "action_id": action_id,
                "execution_identity": "user",
                "requires_confirmation": True,
                "confirmation_reasons": ["capability_requires_confirmation", "high_risk_action", "action_question"],
                "success": len(success),
                "failed": len(failed),
                "error": "；".join(failed[:3]) if failed else "",
            },
        )
        if success:
            clear_result_context(chat_id, reason="approval_batch_success", source="approval_batch")
            _clear_runtime_current_approval_item(chat_id, load_session_context=load_session_context, save_session_context=save_session_context)
        action_status = "success" if not failed else "partial" if success else "error"
        status_group = "terminal"
        save_result_context(
            chat_id,
            ResultContext(
                result_type="approval_action",
                query_id=uuid4().hex,
                count=len(success) + len(failed),
                items=tuple(
                    [
                        {
                            "source": "approval",
                            "operation": "batch_approve",
                            "status": "success",
                            "status_group": status_group,
                            "is_terminal": True,
                            "is_pending": False,
                            "action_id": action_id,
                            "route_path": "feishu_approval_task_query",
                            "summary": f"已通过：{label}",
                        }
                        for label in success[:20]
                    ]
                    + [
                        {
                            "source": "approval",
                            "operation": "batch_approve",
                            "status": "error",
                            "status_group": status_group,
                            "is_terminal": True,
                            "is_pending": False,
                            "action_id": action_id,
                            "route_path": "feishu_approval_task_query",
                            "summary": f"失败：{label}",
                        }
                        for label in failed[:20]
                    ]
                ),
                metadata={
                    "context_kind": "action_receipt",
                    "question_type": "action",
                    "data_scope": "self",
                    "actionable": False,
                    "execution_status": action_status,
                    "source": "approval",
                    "operation": "batch_approve",
                    "route_path": "feishu_approval_task_query",
                    "result_sources": ["approval"],
                    "provider_evidence": {
                        "provider_count": 1,
                        "sources": ["approval"],
                        "operations": ["batch_approve"],
                        "success_count": len(success),
                        "error_count": len(failed),
                        "denied_count": 0,
                        "skipped_count": 0,
                    },
                    "source_execution_status": {
                        "planned_sources": ["approval"],
                        "executed_sources": ["approval"],
                        "missing_sources": [],
                        "extra_sources": [],
                        "success_count": len(success),
                        "error_count": len(failed),
                    },
                    "action_id": action_id,
                    "action_status_group": status_group,
                    "is_terminal_action": True,
                    "is_pending_action": False,
                    "success_count": len(success),
                    "failed_count": len(failed),
                    "consume_policy": {
                        "prefer_items": True,
                        "allow_answer_fallback": False,
                        "requires_refresh_when_expired": True,
                        "supports_index_followup": bool(success or failed),
                        "supports_detail_followup": bool(success or failed),
                    },
                    "followup_fields": ["action_id", "status", "summary", "route_path"],
                    "item_identity_fields": ["action_id"],
                    "item_count": len(success) + len(failed),
                    "display_count": len(success) + len(failed),
                },
                answer=f"批量审批完成：成功 {len(success)} 笔，失败 {len(failed)} 笔。",
            ),
        )
        write_runtime_action_audit(
            db,
            action="runtime_v5.approval.batch_approve",
            company_id=app_config.company_id,
            actor=identity.open_id,
            target_type="approval_batch",
            target_id=chat_id,
            payload={
                "chat_id": chat_id,
                "status": "success" if not failed else "partial" if success else "error",
                "success": len(success),
                "failed": len(failed),
                "success_items": success[:10],
                "failed_items": failed[:10],
            },
        )
        lines = [f"批量审批完成：成功 {len(success)} 笔，失败 {len(failed)} 笔。"]
        if success:
            lines.append("")
            lines.append("已通过：")
            lines.extend(success[:10])
        if failed:
            lines.append("")
            lines.append("失败：")
            lines.extend(failed[:10])
        asyncio.run(
            feishu_replies.send_text_reply(
                app_config=app_config,
                reply_target=reply_target,
                text="\n".join(lines),
            )
        )
        db.commit()
        return {"ok": True, "success": len(success), "failed": len(failed)}
    except Exception as exc:
        db.rollback()
        try:
            from app.services.feishu import replies as feishu_replies
            from app.services.runtime_v5.action_observer import record_action_trace

            record_action_trace(
                chat_id,
                {
                    "kind": "approval_batch",
                    "action": "approve",
                    "status": "error",
                    "route_path": "feishu_approval_task_query",
                },
            )
            app_config = db.get(FeishuAppConfig, UUID(app_config_id))
            if app_config:
                answer = f"批量审批执行失败：{str(exc)[:180]}。请重新查询待审批后再试。"
                _save_runtime_card_action_receipt(
                    chat_id=chat_id,
                    action_id=action_id or uuid4().hex,
                    strategy="approval_batch_approve",
                    status="error",
                    answer=answer,
                    route_path="feishu_approval_task_query",
                    timing={},
                )
                asyncio.run(
                    feishu_replies.send_text_reply(
                        app_config=app_config,
                        reply_target=reply_target,
                        text=answer,
                    )
                )
        except Exception:
            pass
        return {"ok": False, "error": str(exc)[:500]}
    finally:
        db.close()


def _save_runtime_card_action_receipt(
    *,
    chat_id: str | None,
    action_id: str,
    strategy: str,
    status: str,
    answer: str,
    route_path: str,
    timing: dict,
) -> None:
    if not chat_id:
        return
    from app.services.runtime_v5.context import save_result_context
    from app.services.runtime_v5.models import ResultContext

    status_group = _runtime_action_status_group(status)
    save_result_context(
        chat_id,
        ResultContext(
            result_type="runtime_action",
            query_id=uuid4().hex,
            count=1,
            items=(
                {
                    "source": "runtime",
                    "operation": strategy or "runtime_action",
                    "status": status,
                    "status_group": status_group,
                    "is_terminal": status_group == "terminal",
                    "is_pending": status_group == "pending",
                    "action_id": action_id,
                    "route_path": route_path,
                    "summary": (answer or "操作已处理。")[:300],
                },
            ),
            metadata={
                "context_kind": "action_receipt",
                "question_type": "action",
                "data_scope": "self",
                "actionable": False,
                "execution_status": status,
                "source": "runtime",
                "operation": strategy or "runtime_action",
                "route_path": route_path,
                "result_sources": ["runtime"],
                "action_id": action_id,
                "action_status_group": status_group,
                "is_terminal_action": status_group == "terminal",
                "is_pending_action": status_group == "pending",
                "duration_ms": int(timing.get("pipeline_total_ms") or 0) if isinstance(timing, dict) else 0,
                "consume_policy": {
                    "prefer_items": True,
                    "allow_answer_fallback": False,
                    "requires_refresh_when_expired": True,
                    "supports_index_followup": True,
                    "supports_detail_followup": True,
                },
                "followup_fields": ["action_id", "status", "summary", "route_path"],
                "item_identity_fields": ["action_id"],
                "item_count": 1,
                "display_count": 1,
            },
            answer=answer or "操作已处理。",
        ),
    )


def _runtime_action_status_group(status: str) -> str:
    if status in {"queued", "started", "processing", "pending", "confirmation_card_started", "confirmation_card_sent"}:
        return "pending"
    if status in {"success", "partial", "error", "failed", "cancelled", "stale", "stale_cleanup", "denied", "skipped", "confirmation_card_failed"}:
        return "terminal"
    return "unknown"


@celery_app.task(name="v5.resources.sync_auto")
def auto_v5_resource_sync_task() -> dict:
    db = SessionLocal()
    try:
        policies = enabled_v5_resource_sync_policies(db)
        if not policies:
            return {"enabled": False, "count": 0, "saved_count": 0, "policies": 0}
        results: list[dict] = []
        errors: list[dict[str, str]] = []
        selected_resource_ids: list[str] = []
        for company_id, policy in policies:
            if not _v5_policy_due(db, company_id=company_id, policy=policy):
                continue
            result = asyncio.run(sync_company_auto_resources(db, company_id=company_id, policy=policy))
            selected_resource_ids.extend(result["selected_resource_ids"])
            results.extend(result["items"])
            errors.extend(result["errors"])
        return {
            "enabled": True,
            "policies": len(policies),
            "count": len(results),
            "selected_resource_ids": selected_resource_ids,
            "saved_count": sum(item.get("saved_count", 0) for item in results),
            "items": results,
            "errors": errors,
        }
    finally:
        db.close()


def _configured_imap_accounts(db) -> list[Account]:
    account_ids = _parse_uuid_list(settings.auto_imap_account_ids)
    query = select(Account).where(Account.provider == "imap").where(Account.is_active.is_(True))
    if account_ids:
        query = query.where(Account.id.in_(account_ids))
    return list(db.scalars(query.order_by(Account.created_at.asc())).all())


def _configured_company_ids(db) -> list[UUID]:
    company_ids = _parse_uuid_list(settings.auto_daily_report_company_ids)
    if company_ids:
        return company_ids

    return list(
        db.scalars(
            select(Account.company_id)
            .where(Account.provider == "imap")
            .where(Account.is_active.is_(True))
            .distinct()
        ).all()
    )


def _configured_feishu_apps(db) -> list[FeishuAppConfig]:
    app_ids = _parse_uuid_list(settings.auto_feishu_app_config_ids)
    query = select(FeishuAppConfig).where(FeishuAppConfig.is_active.is_(True))
    if app_ids:
        query = query.where(FeishuAppConfig.id.in_(app_ids))
    elif settings.feishu_default_app_config_id:
        query = query.where(FeishuAppConfig.id == UUID(settings.feishu_default_app_config_id))
    return list(db.scalars(query.order_by(FeishuAppConfig.created_at.asc())).all())


def _active_feishu_apps(db) -> list[FeishuAppConfig]:
    return list(
        db.scalars(
            select(FeishuAppConfig)
            .where(FeishuAppConfig.is_active.is_(True))
            .order_by(FeishuAppConfig.created_at.asc())
        ).all()
    )


def _v5_auto_sync_resources(db, *, company_id: UUID, policy: dict) -> list[Resource]:
    return select_company_auto_sync_resources(db, company_id=company_id, policy=policy)


def _v5_policy_due(db, *, company_id: UUID, policy: dict) -> bool:
    return company_auto_sync_due(db, company_id=company_id, policy=policy)


def _parse_csv(raw_value: str) -> list[str]:
    return [item.strip() for item in raw_value.split(",") if item.strip()]


def _should_clear_result_context_after_action(strategy: str) -> bool:
    approval_actions = {
        "approval_approve",
        "approval_reject",
        "approval_transfer",
        "approval_add_sign",
        "approval_rollback",
        "approval_remind",
        "approval_cancel",
        "approval_cc",
    }
    return strategy in approval_actions


def _clear_runtime_current_approval_item(chat_id: str | None, *, load_session_context, save_session_context) -> None:
    if not chat_id:
        return
    session_context = load_session_context(chat_id)
    session_context.pop("runtime_v5_current_approval_item", None)
    save_session_context(chat_id, session_context)


def _runtime_trace_timing(trace: dict) -> dict:
    summary = trace.get("runtime_trace_summary") if isinstance(trace.get("runtime_trace_summary"), dict) else {}
    pipeline_timing = summary.get("pipeline_timing") if isinstance(summary.get("pipeline_timing"), dict) else {}
    provider_results = summary.get("provider_results") if isinstance(summary.get("provider_results"), list) else []
    if not provider_results:
        provider_results = trace.get("provider_results") if isinstance(trace.get("provider_results"), list) else []
    slowest_provider = {}
    for item in provider_results:
        if not isinstance(item, dict):
            continue
        if int(item.get("duration_ms") or 0) > int(slowest_provider.get("duration_ms") or 0):
            slowest_provider = item
    return {
        "pipeline_total_ms": int(pipeline_timing.get("total_ms") or 0),
        "pipeline_slowest_stage": str(pipeline_timing.get("slowest_stage") or ""),
        "pipeline_slowest_ms": int(pipeline_timing.get("slowest_ms") or 0),
        "slowest_provider": str(slowest_provider.get("source") or ""),
        "slowest_provider_operation": str(slowest_provider.get("operation") or ""),
        "slowest_provider_ms": int(slowest_provider.get("duration_ms") or 0),
        "slowest_provider_substeps": slowest_provider.get("substeps") if isinstance(slowest_provider.get("substeps"), list) else [],
    }


def _runtime_timing_hint(timing: dict) -> str:
    provider = str(timing.get("slowest_provider") or "").strip()
    operation = str(timing.get("slowest_provider_operation") or "").strip()
    duration_ms = int(timing.get("slowest_provider_ms") or 0)
    if duration_ms < 3000 or not provider:
        return ""
    label = "很慢" if duration_ms >= 8000 else "较慢"
    suggestion = _runtime_provider_suggestion(provider)
    substep_hint = _runtime_slowest_substep_hint(timing.get("slowest_provider_substeps"))
    return (
        f"慢点提示：{provider}.{operation or 'execute'} {duration_ms}ms，{label}。"
        + (f"{substep_hint}" if substep_hint else "")
        + (f"建议：{suggestion}" if suggestion else "")
    )


def _append_runtime_timing_hint(answer: str, timing: dict) -> str:
    hint = _runtime_timing_hint(timing)
    if not hint:
        return answer
    return f"{answer}\n\n{hint}" if answer else hint


def _runtime_slowest_substep_hint(substeps) -> str:
    if not isinstance(substeps, list):
        return ""
    slowest = {}
    for item in substeps:
        if not isinstance(item, dict):
            continue
        if int(item.get("duration_ms") or 0) > int(slowest.get("duration_ms") or 0):
            slowest = item
    if not slowest:
        return ""
    return f"最慢子步骤：{_runtime_substep_label(str(slowest.get('step') or ''))} {int(slowest.get('duration_ms') or 0)}ms。"


def _runtime_substep_label(step: str) -> str:
    return {
        "approval_task_query": "查询待审批列表",
        "approval_detail_batch": "批量读取审批详情",
        "approval_history": "读取历史审批",
        "approval_attachments": "读取审批附件",
        "approval_ai_judgement": "AI 审批判断",
        "approval_detail": "读取审批详情",
        "base_create": "创建多维表格文件",
        "table_create": "创建数据表",
        "record_batch_create": "批量写入记录",
    }.get(step, step or "未知")


def _runtime_provider_suggestion(provider: str) -> str:
    return {
        "approval": "检查附件读取、审批详情接口和历史记录生成。",
        "people": "优先使用组织快照缓存，避免每次全量拉通讯录。",
        "base": "优先批量写入并减少字段/记录重复创建。",
        "im": "检查目标解析和消息发送权限。",
        "message": "检查目标解析和消息发送权限。",
        "calendar": "检查时间解析、参会人解析和日程写入接口。",
        "task": "检查任务分页和写入确认。",
        "mail": "检查邮件搜索范围和正文/附件读取。",
    }.get(provider, "查看该 Provider 的飞书接口耗时和权限返回。")


def _record_failed_sync_run(*, company_id, provider: str, sync_type: str, cursor: dict, error: str) -> None:
    db = SessionLocal()
    try:
        sync_run = start_sync_run(db, company_id=company_id, provider=provider, sync_type=sync_type, cursor=cursor)
        finish_sync_run(sync_run, status="failed", error_count=1, summary={"error": error})
        db.commit()
    finally:
        db.close()


def _parse_uuid_list(raw_value: str) -> list[UUID]:
    values: list[UUID] = []
    for item in raw_value.split(","):
        item = item.strip()
        if not item:
            continue
        values.append(UUID(item))
    return values


def _push_report_to_feishu(db, markdown: str) -> None:
    if not settings.feishu_default_app_config_id or not settings.feishu_default_receive_id:
        return

    app_config = db.get(FeishuAppConfig, UUID(settings.feishu_default_app_config_id))
    if not app_config or not app_config.is_active:
        return

    _send_feishu_text_via_gateway(
        app_config,
        {
            "receive_id_type": settings.feishu_default_receive_id_type,
            "receive_id": settings.feishu_default_receive_id,
        },
        markdown,
    )


def _send_feishu_text_via_gateway(
    app_config: FeishuAppConfig,
    reply_target: dict,
    text: str,
) -> dict:
    return asyncio.run(
        send_feishu_text_reply(
            app_config=app_config,
            reply_target=reply_target,
            text=text,
            client_factory=FeishuClient,
        )
    )
