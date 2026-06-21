from app.services.tools.base import ToolContext, ToolRequest
from app.services.tools.company import answer_company_question


def execute_report_tool(context: ToolContext, request: ToolRequest) -> str:
    if request.tool_name in {"company_qa", "owner_cockpit"}:
        return answer_company_question(
            context.db,
            company_id=context.company_id,
            actor=context.actor,
            question=request.question,
            normalized_command=request.normalized_command,
        )
    raise ValueError(f"Unsupported report tool: {request.tool_name}")
