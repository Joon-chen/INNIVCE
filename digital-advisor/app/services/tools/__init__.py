"""Tool Router boundary for V5 Agent Runtime."""

from app.services.tools.approval import answer_approval_question
from app.services.tools.bitable import answer_bitable_question
from app.services.tools.calendar import answer_calendar_question
from app.services.tools.chat import (
    answer_current_chat_question,
    answer_current_chat_summary,
    answer_current_chat_tasks,
)
from app.services.tools.company import answer_company_question
from app.services.tools.conversation import answer_general_conversation
from app.services.tools.domain import answer_domain_question
from app.services.tools.knowledge import answer_public_knowledge_question
from app.services.tools.mail import answer_mail_question
from app.services.tools.personal import answer_personal_tasks
from app.services.tools.task import answer_task_question

__all__ = [
    "answer_approval_question",
    "answer_bitable_question",
    "answer_calendar_question",
    "answer_company_question",
    "answer_current_chat_question",
    "answer_current_chat_summary",
    "answer_current_chat_tasks",
    "answer_general_conversation",
    "answer_domain_question",
    "answer_mail_question",
    "answer_personal_tasks",
    "answer_public_knowledge_question",
    "answer_task_question",
]
