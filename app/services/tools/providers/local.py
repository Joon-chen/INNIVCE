from app.services.tools import (
    answer_approval_question,
    answer_bitable_question,
    answer_calendar_question,
    answer_company_question,
    answer_current_chat_question,
    answer_current_chat_summary,
    answer_current_chat_tasks,
    answer_domain_question,
    answer_general_conversation,
    answer_mail_question,
    answer_personal_tasks,
    answer_public_knowledge_question,
    answer_task_question,
)
from app.services.tools.base import ToolContext, ToolRequest


def execute_local_tool(context: ToolContext, request: ToolRequest) -> str:
    if request.tool_name == "chat_summary":
        return answer_current_chat_summary(context.db, company_id=context.company_id, chat_id=context.chat_id)
    if request.tool_name == "chat_tasks":
        return answer_current_chat_tasks(context.db, company_id=context.company_id, chat_id=context.chat_id)
    if request.tool_name == "general_chat":
        return answer_general_conversation(question=request.question, actor=context.actor)
    if request.tool_name == "bitable_qa":
        return answer_bitable_question(context.db, company_id=context.company_id, question=request.question)
    if request.tool_name == "calendar_qa":
        return answer_calendar_question(context.db, company_id=context.company_id, question=request.question)
    if request.tool_name == "mail_qa":
        return answer_mail_question(context.db, company_id=context.company_id, question=request.question)
    if request.tool_name == "personal_tasks":
        return answer_personal_tasks(context.db, company_id=context.company_id, actor=context.actor)
    if request.tool_name == "task_qa":
        return answer_task_question(context.db, company_id=context.company_id)
    if request.tool_name == "approval_qa":
        return answer_approval_question(context.db, company_id=context.company_id, question=request.question)
    if request.tool_name == "domain_qa":
        return answer_domain_question(context.db, company_id=context.company_id, actor=context.actor, question=request.question)
    if request.tool_name == "chat_qa":
        return answer_current_chat_question(
            context.db,
            company_id=context.company_id,
            chat_id=context.chat_id,
            question=request.question,
            normalized_command=request.normalized_command,
            actor=context.actor,
        )
    if request.tool_name == "company_qa":
        return answer_company_question(
            context.db,
            company_id=context.company_id,
            actor=context.actor,
            question=request.question,
            normalized_command=request.normalized_command,
        )
    if request.tool_name == "public_knowledge_qa":
        return answer_public_knowledge_question(context.db, company_id=context.company_id, question=request.question)
    raise ValueError(f"Unsupported local agent tool: {request.tool_name}")
