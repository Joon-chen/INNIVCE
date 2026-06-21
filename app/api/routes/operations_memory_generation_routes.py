from typing import Any

from fastapi import APIRouter

from app.services.operations_memory import GenerateMemoryRequest, enqueue_recent_memory_generation_from_request

router = APIRouter()


@router.post("/memory-facts/generate")
def generate_memory_facts(data: GenerateMemoryRequest) -> dict[str, Any]:
    return enqueue_recent_memory_generation_from_request(data)
