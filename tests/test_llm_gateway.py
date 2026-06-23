from app.services.llm import gateway as gateway_module
from app.services.llm.gateway import LLMGateway


def test_hybrid_complete_text_falls_back_to_openai_when_local_unavailable(monkeypatch) -> None:
    class BrokenLocalClient:
        class Chat:
            class Completions:
                def create(self, **kwargs):
                    raise RuntimeError("local unavailable")

            completions = Completions()

        chat = Chat()

    class OpenAIClient:
        class Responses:
            def create(self, **kwargs):
                class Response:
                    output_text = "fallback ok"

                return Response()

        responses = Responses()

    monkeypatch.setattr(gateway_module.settings, "ai_provider", "hybrid")
    monkeypatch.setattr(gateway_module.settings, "local_llm_model", "local-model")
    monkeypatch.setattr(gateway_module.settings, "openai_model", "openai-model")
    monkeypatch.setattr(LLMGateway, "_build_local_client", lambda self, model: BrokenLocalClient())
    monkeypatch.setattr(LLMGateway, "_build_openai_client", lambda self: OpenAIClient())
    monkeypatch.setattr(LLMGateway, "_build_deepseek_client", lambda self: None)
    monkeypatch.setattr(LLMGateway, "_build_embedding_client", lambda self: None)

    assert LLMGateway().complete_text("hello") == "fallback ok"
