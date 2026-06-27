import math
import re
from hashlib import sha256
from time import perf_counter

import httpx
from openai import OpenAI

from app.core.config import settings
from app.services.llm.call_budget import authorize_llm_call
from app.services.llm.call_trace import record_llm_call_trace
from app.services.llm.prompt_audit import prompt_audit_payload
from app.services.llm.routing_policy import LLMTaskType, llm_route_for_task


class LLMGateway:
    """Provider boundary for OpenAI-compatible text and embedding calls."""

    def __init__(self) -> None:
        self.provider = settings.ai_provider.lower().strip()
        self.model = settings.openai_model
        self.client = self._build_client()
        self.openai_client = self._build_openai_client()
        self.deepseek_client = self._build_deepseek_client()
        self.embedding_client = self._build_embedding_client()

    def _build_client(self):
        if self.provider in {"local", "hybrid"}:
            self.model = settings.local_llm_model
            return self._build_local_client(model=settings.local_llm_model)
        if self.provider == "deepseek":
            self.model = settings.deepseek_model
            if not settings.deepseek_api_key:
                return None
            return OpenAI(api_key=settings.deepseek_api_key, base_url=settings.deepseek_base_url, max_retries=0)
        self.provider = "openai"
        self.model = settings.openai_model
        return self._build_openai_client()

    def _build_deepseek_client(self):
        if not settings.deepseek_api_key:
            return None
        return OpenAI(api_key=settings.deepseek_api_key, base_url=settings.deepseek_base_url, max_retries=0)

    def _build_openai_client(self):
        if not settings.openai_api_key:
            return None
        return OpenAI(api_key=settings.openai_api_key, max_retries=0)

    def _build_local_client(self, *, model: str):
        if not settings.local_llm_base_url or not model:
            return None
        return OpenAI(api_key=settings.local_llm_api_key or "ollama", base_url=settings.local_llm_base_url, max_retries=0)

    def _build_embedding_client(self):
        if not settings.openai_api_key:
            return None
        return OpenAI(api_key=settings.openai_api_key)

    def complete_text(self, prompt: str, *, temperature: float = 0.2) -> str | None:
        if not self.client:
            return None
        if self.provider in {"deepseek", "local", "hybrid"}:
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=temperature,
                )
                return response.choices[0].message.content or ""
            except Exception:
                if self.provider not in {"local", "hybrid"}:
                    raise
                try:
                    url = settings.local_llm_base_url.rstrip("/") + "/chat/completions"
                    response = httpx.post(
                        url,
                        json={
                            "model": self.model,
                            "messages": [{"role": "user", "content": prompt}],
                            "temperature": temperature,
                            "stream": False,
                        },
                        timeout=60,
                    )
                    response.raise_for_status()
                    data = response.json()
                    return (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
                except Exception:
                    pass
                if self.provider == "hybrid":
                    try:
                        fallback = self.complete_openai_text(prompt, temperature=temperature)
                    except Exception:
                        fallback = None
                    if fallback:
                        return fallback
                    try:
                        return self.complete_deepseek_text(prompt, temperature=temperature)
                    except Exception:
                        return None
                return None
        response = self.client.responses.create(
            model=self.model,
            input=prompt,
            temperature=temperature,
        )
        return response.output_text.strip()

    def complete_task_text(
        self,
        prompt: str,
        *,
        task_type: LLMTaskType,
        temperature: float = 0.2,
    ) -> str | None:
        route = llm_route_for_task(task_type)
        started = perf_counter()
        status = "success"
        error = ""
        text: str | None = None
        prompt_audit = prompt_audit_payload(prompt=prompt, task_type=route.task_type, lane=route.lane)
        allowed, budget_payload = authorize_llm_call(
            task_type=route.task_type,
            lane=route.lane,
            provider=route.provider,
            model=route.model,
        )
        if not allowed:
            record_llm_call_trace(
                {
                    "task_type": route.task_type,
                    "provider": route.provider,
                    "lane": route.lane,
                    "model": route.model,
                    "latency_budget_ms": route.latency_budget_ms,
                    "duration_ms": 0,
                    "budget_exceeded": False,
                    "allow_fallback": route.allow_fallback,
                    "fallback_provider": route.fallback_provider,
                    "fallback_used": True,
                    "status": "budget_denied",
                    "error": "",
                    "prompt_chars": len(prompt or ""),
                    "response_chars": 0,
                    "budget": budget_payload,
                    "prompt_audit": prompt_audit,
                }
            )
            return None
        try:
            if route.provider == "deepseek_api":
                text = self._complete_deepseek_model(
                    prompt,
                    model=route.model,
                    temperature=temperature,
                    timeout_seconds=_timeout_seconds(route.latency_budget_ms),
                    response_format=_response_format_for_task(route.task_type),
                    max_tokens=_max_tokens_for_task(route.task_type),
                )
            elif route.provider == "openai_api":
                text = self._complete_openai_model(
                    prompt,
                    model=route.model,
                    temperature=temperature,
                    timeout_seconds=_timeout_seconds(route.latency_budget_ms),
                )
            elif route.provider in {"local_fast", "local_reasoning"}:
                text = self._complete_local_model(
                    prompt,
                    model=route.model,
                    temperature=temperature,
                    timeout_seconds=_local_timeout_seconds(route.latency_budget_ms),
                    response_format=_response_format_for_task(route.task_type),
                    max_tokens=_max_tokens_for_task(route.task_type),
                )
            else:
                status = "disabled"
                text = None
            if text is None and status == "success":
                status = "empty"
            return text
        except Exception as exc:
            status = "error"
            error = type(exc).__name__
            raise
        finally:
            record_llm_call_trace(
                {
                    "task_type": route.task_type,
                    "provider": route.provider,
                    "lane": route.lane,
                    "model": route.model,
                    "latency_budget_ms": route.latency_budget_ms,
                    "duration_ms": int((perf_counter() - started) * 1000),
                    "budget_exceeded": int((perf_counter() - started) * 1000) > route.latency_budget_ms,
                    "allow_fallback": route.allow_fallback,
                    "fallback_provider": route.fallback_provider,
                    "fallback_used": False,
                    "status": status,
                    "error": error,
                    "prompt_chars": len(prompt or ""),
                    "response_chars": len(text or ""),
                    "budget": budget_payload,
                    "prompt_audit": prompt_audit,
                }
            )

    def complete_reasoning_text(self, prompt: str, *, temperature: float = 0.2) -> str | None:
        return self.complete_task_text(prompt, task_type="reasoning", temperature=temperature)

    def complete_openai_text(self, prompt: str, *, temperature: float = 0.2) -> str | None:
        if not self.openai_client:
            return None
        response = self.openai_client.responses.create(
            model=settings.openai_model,
            input=prompt,
            temperature=temperature,
        )
        return response.output_text.strip()

    def complete_deepseek_text(self, prompt: str, *, temperature: float = 0.2) -> str | None:
        return self._complete_deepseek_model(prompt, model=settings.deepseek_model, temperature=temperature)

    def _complete_deepseek_model(
        self,
        prompt: str,
        *,
        model: str,
        temperature: float = 0.2,
        timeout_seconds: float | None = None,
        response_format: dict[str, str] | None = None,
        max_tokens: int | None = None,
    ) -> str | None:
        if not self.deepseek_client:
            return None
        payload: dict[str, object] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "timeout": timeout_seconds,
        }
        if response_format:
            payload["response_format"] = response_format
        if max_tokens:
            payload["max_tokens"] = max_tokens
        response = self.deepseek_client.chat.completions.create(**payload)
        return response.choices[0].message.content or ""

    def _complete_openai_model(
        self,
        prompt: str,
        *,
        model: str,
        temperature: float = 0.2,
        timeout_seconds: float | None = None,
    ) -> str | None:
        if not self.openai_client:
            return None
        response = self.openai_client.responses.create(
            model=model,
            input=prompt,
            temperature=temperature,
            timeout=timeout_seconds,
        )
        return response.output_text.strip()

    def _complete_local_model(
        self,
        prompt: str,
        *,
        model: str,
        temperature: float = 0.2,
        timeout_seconds: float | None = None,
        response_format: dict[str, str] | None = None,
        max_tokens: int | None = None,
    ) -> str | None:
        client = self._build_local_client(model=model)
        if not client:
            return None
        payload: dict[str, object] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "timeout": timeout_seconds,
        }
        if response_format:
            payload["response_format"] = response_format
        if max_tokens:
            payload["max_tokens"] = max_tokens
        response = client.chat.completions.create(**payload)
        return response.choices[0].message.content or ""

    def embedding(self, text: str) -> list[float] | None:
        if not text.strip():
            return None
        if settings.embedding_provider.lower().strip() == "local_hash":
            return local_hash_embedding(text, size=settings.local_embedding_size)
        if not self.embedding_client:
            return None
        try:
            response = self.embedding_client.embeddings.create(
                model=settings.openai_embedding_model,
                input=text[:8000],
            )
            return response.data[0].embedding
        except Exception:
            return None


def local_hash_embedding(text: str, *, size: int = 384) -> list[float]:
    vector = [0.0] * size
    tokens = _embedding_tokens(text)
    for token in tokens[:2000]:
        digest = sha256(token.encode("utf-8", errors="ignore")).digest()
        index = int.from_bytes(digest[:4], "big") % size
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign
    norm = math.sqrt(sum(value * value for value in vector))
    if not norm:
        return vector
    return [value / norm for value in vector]


def _embedding_tokens(text: str) -> list[str]:
    compact = re.sub(r"\s+", " ", text.lower())
    words = re.findall(r"[a-z0-9_.@-]{2,}|[\u4e00-\u9fff]", compact)
    tokens = words[:]
    chinese_chars = [token for token in words if len(token) == 1 and "\u4e00" <= token <= "\u9fff"]
    tokens.extend("".join(chinese_chars[index : index + 2]) for index in range(max(len(chinese_chars) - 1, 0)))
    tokens.extend("".join(chinese_chars[index : index + 3]) for index in range(max(len(chinese_chars) - 2, 0)))
    return [token for token in tokens if token.strip()]


def _timeout_seconds(latency_budget_ms: int) -> float:
    return max(0.5, float(latency_budget_ms or 1800) / 1000.0)


def _local_timeout_seconds(latency_budget_ms: int) -> float:
    return max(8.0, _timeout_seconds(latency_budget_ms))


def _response_format_for_task(task_type: str) -> dict[str, str] | None:
    if task_type == "command_intent":
        return {"type": "json_object"}
    return None


def _max_tokens_for_task(task_type: str) -> int | None:
    if task_type == "command_intent":
        return 300
    if task_type in {"conversation", "presentation"}:
        return 120
    return None
