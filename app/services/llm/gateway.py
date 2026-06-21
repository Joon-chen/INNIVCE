import math
import re
from hashlib import sha256

import httpx
from openai import OpenAI

from app.core.config import settings


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
            return OpenAI(api_key=settings.deepseek_api_key, base_url=settings.deepseek_base_url)
        self.provider = "openai"
        self.model = settings.openai_model
        return self._build_openai_client()

    def _build_deepseek_client(self):
        if not settings.deepseek_api_key:
            return None
        return OpenAI(api_key=settings.deepseek_api_key, base_url=settings.deepseek_base_url)

    def _build_openai_client(self):
        if not settings.openai_api_key:
            return None
        return OpenAI(api_key=settings.openai_api_key)

    def _build_local_client(self, *, model: str):
        if not settings.local_llm_base_url or not model:
            return None
        return OpenAI(api_key=settings.local_llm_api_key or "ollama", base_url=settings.local_llm_base_url)

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
                return None
        response = self.client.responses.create(
            model=self.model,
            input=prompt,
            temperature=temperature,
        )
        return response.output_text.strip()

    def complete_reasoning_text(self, prompt: str, *, temperature: float = 0.2) -> str | None:
        return self.complete_deepseek_text(prompt, temperature=temperature) or self.complete_text(
            prompt,
            temperature=temperature,
        )

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
        if not self.deepseek_client:
            return None
        response = self.deepseek_client.chat.completions.create(
            model=settings.deepseek_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
        )
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
