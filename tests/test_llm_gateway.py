from app.services.llm import gateway as gateway_module
from app.services.llm.call_budget import current_llm_call_budget_summary, llm_call_budget
from app.services.llm.call_trace import last_llm_call_trace
from app.services.llm.gateway import LLMGateway
from app.services.llm.prompt_audit import prompt_audit_payload
from app.services.llm.routing_policy import llm_route_for_task, llm_routing_policy_payload


def test_llm_routing_policy_defaults_bot_tasks_to_deepseek(monkeypatch) -> None:
    monkeypatch.setattr(gateway_module.settings, "deepseek_model", "deepseek-test")
    monkeypatch.setattr(gateway_module.settings, "local_llm_base_url", "")

    assert llm_route_for_task("command_intent").provider == "deepseek_api"
    assert llm_route_for_task("command_intent").lane == "foreground_fast"
    assert llm_route_for_task("conversation").lane == "foreground_grounded"
    assert llm_route_for_task("insight_generation").lane == "background_reasoning"
    assert llm_route_for_task("conversation").provider == "deepseek_api"
    assert llm_route_for_task("presentation").provider == "deepseek_api"
    assert llm_route_for_task("command_intent").model == "deepseek-test"
    assert llm_route_for_task("command_intent").allow_fallback is False


def test_llm_routing_policy_keeps_deepseek_for_foreground_with_local_available(monkeypatch) -> None:
    monkeypatch.setattr(gateway_module.settings, "deepseek_model", "deepseek-test")
    monkeypatch.setattr(gateway_module.settings, "local_llm_base_url", "http://ollama:11434/v1")
    monkeypatch.setattr(gateway_module.settings, "local_llm_model", "qwen2.5:1.5b")

    assert llm_route_for_task("command_intent").provider == "deepseek_api"
    assert llm_route_for_task("conversation").provider == "deepseek_api"
    assert llm_route_for_task("presentation").provider == "deepseek_api"
    assert llm_route_for_task("insight_generation").provider == "deepseek_api"
    assert llm_route_for_task("command_intent").model == "deepseek-test"


def test_llm_routing_policy_payload_exposes_task_budgets(monkeypatch) -> None:
    monkeypatch.setattr(gateway_module.settings, "deepseek_model", "deepseek-test")
    monkeypatch.setattr(gateway_module.settings, "local_llm_base_url", "")

    payload = llm_routing_policy_payload()

    assert payload["command_intent"]["provider"] == "deepseek_api"
    assert payload["command_intent"]["lane"] == "foreground_fast"
    assert payload["presentation"]["lane"] == "foreground_grounded"
    assert payload["evidence_analysis"]["lane"] == "background_extraction"
    assert payload["command_intent"]["latency_budget_ms"] == 6000
    assert payload["snapshot_builder"]["latency_budget_ms"] == 30000
    assert payload["command_intent"]["allow_fallback"] is False


def test_complete_task_text_uses_deepseek_route_without_local_fallback(monkeypatch) -> None:
    class BrokenLocalClient:
        class Chat:
            class Completions:
                def create(self, **kwargs):
                    raise RuntimeError("local unavailable")

            completions = Completions()

        chat = Chat()

    class DeepSeekClient:
        class Chat:
            class Completions:
                def create(self, **kwargs):
                    assert kwargs["model"] == "deepseek-test"

                    class Message:
                        content = "deepseek ok"

                    class Choice:
                        message = Message()

                    class Response:
                        choices = [Choice()]

                    return Response()

            completions = Completions()

        chat = Chat()

    monkeypatch.setattr(gateway_module.settings, "ai_provider", "hybrid")
    monkeypatch.setattr(gateway_module.settings, "local_llm_base_url", "")
    monkeypatch.setattr(gateway_module.settings, "local_llm_model", "local-model")
    monkeypatch.setattr(gateway_module.settings, "deepseek_model", "deepseek-test")
    monkeypatch.setattr(LLMGateway, "_build_local_client", lambda self, model: BrokenLocalClient())
    monkeypatch.setattr(LLMGateway, "_build_openai_client", lambda self: None)
    monkeypatch.setattr(LLMGateway, "_build_deepseek_client", lambda self: DeepSeekClient())
    monkeypatch.setattr(LLMGateway, "_build_embedding_client", lambda self: None)

    assert LLMGateway().complete_task_text("hello", task_type="conversation") == "deepseek ok"

    trace = last_llm_call_trace()
    assert trace["task_type"] == "conversation"
    assert trace["lane"] == "foreground_grounded"
    assert trace["provider"] == "deepseek_api"
    assert trace["model"] == "deepseek-test"
    assert trace["allow_fallback"] is False
    assert trace["fallback_used"] is False
    assert trace["status"] == "success"
    assert trace["duration_ms"] >= 0


def test_complete_task_text_applies_route_timeout(monkeypatch) -> None:
    captured = {}

    class DeepSeekClient:
        class Chat:
            class Completions:
                def create(self, **kwargs):
                    captured.update(kwargs)

                    class Message:
                        content = "deepseek ok"

                    class Choice:
                        message = Message()

                    class Response:
                        choices = [Choice()]

                    return Response()

            completions = Completions()

        chat = Chat()

    monkeypatch.setattr(gateway_module.settings, "deepseek_model", "deepseek-test")
    monkeypatch.setattr(gateway_module.settings, "local_llm_base_url", "")
    monkeypatch.setattr(LLMGateway, "_build_openai_client", lambda self: None)
    monkeypatch.setattr(LLMGateway, "_build_deepseek_client", lambda self: DeepSeekClient())
    monkeypatch.setattr(LLMGateway, "_build_embedding_client", lambda self: None)

    assert LLMGateway().complete_task_text("hello", task_type="conversation") == "deepseek ok"

    assert captured["timeout"] == 6.0


def test_complete_task_text_respects_turn_call_budget(monkeypatch) -> None:
    call_count = 0

    class DeepSeekClient:
        class Chat:
            class Completions:
                def create(self, **kwargs):
                    nonlocal call_count
                    call_count += 1

                    class Message:
                        content = "deepseek ok"

                    class Choice:
                        message = Message()

                    class Response:
                        choices = [Choice()]

                    return Response()

            completions = Completions()

        chat = Chat()

    monkeypatch.setattr(gateway_module.settings, "deepseek_model", "deepseek-test")
    monkeypatch.setattr(gateway_module.settings, "local_llm_base_url", "")
    monkeypatch.setattr(LLMGateway, "_build_openai_client", lambda self: None)
    monkeypatch.setattr(LLMGateway, "_build_deepseek_client", lambda self: DeepSeekClient())
    monkeypatch.setattr(LLMGateway, "_build_embedding_client", lambda self: None)

    gateway = LLMGateway()
    with llm_call_budget(max_calls=1, turn_type="test_turn"):
        assert gateway.complete_task_text("hello", task_type="conversation") == "deepseek ok"
        assert gateway.complete_task_text("hello again", task_type="presentation") is None
        summary = current_llm_call_budget_summary()

    assert call_count == 1
    assert summary["used_calls"] == 1
    assert summary["denied_calls"] == 1
    assert summary["calls"][0]["lane"] == "foreground_grounded"
    assert summary["denied"][0]["lane"] == "foreground_grounded"
    trace = last_llm_call_trace()
    assert trace["status"] == "budget_denied"
    assert trace["fallback_used"] is True
    assert trace["prompt_audit"]["prompt_chars"] == len("hello again")
    assert trace["prompt_audit"]["lane"] == "foreground_grounded"


def test_prompt_audit_keeps_profile_planes_separate() -> None:
    command = prompt_audit_payload(
        prompt="Intent Profile：用户角色：owner\n用户问题：全公司任务",
        task_type="command_intent",
        lane="foreground_fast",
    )
    bad_command = prompt_audit_payload(
        prompt="用户画像：说话风格：casual\n用户问题：全公司任务",
        task_type="command_intent",
        lane="foreground_fast",
    )
    presentation = prompt_audit_payload(
        prompt="用户画像：说话风格：professional\n原答案：你有 2 条任务。",
        task_type="presentation",
        lane="foreground_grounded",
    )

    assert command["profile_plane"] == "intent"
    assert "command_with_presentation_profile" not in command["risks"]
    assert bad_command["profile_plane"] == "presentation"
    assert "command_with_presentation_profile" in bad_command["risks"]
    assert presentation["profile_plane"] == "presentation"
    assert "output_with_intent_profile" not in presentation["risks"]
