from typing import Any

from fastapi import APIRouter

from app.services.agent.reply_modes import reply_mode_catalog

router = APIRouter()


@router.get("/agent/reply-modes")
def agent_reply_modes() -> dict[str, Any]:
    return {
        "items": reply_mode_catalog(),
        "runtime_boundary": {
            "chain": [
                "Feishu Message Gateway",
                "Agent Runtime",
                "Tool Router",
                "Tool",
                "Data/Execution Source",
                "Tool structured result",
                "Agent Runtime final answer",
            ],
            "final_answer_owner": "agent_runtime",
            "tool_returns_structured_result": True,
        },
    }
