from app.services.runtime_v5.interaction_intent import classify_interaction_intent


def test_interaction_intent_classifies_non_business_dialogue() -> None:
    assert classify_interaction_intent("你知道我是谁吗").kind == "user_context"
    assert classify_interaction_intent("你知道这个表情是什么情绪吗").kind == "emoji_or_reaction"
    assert classify_interaction_intent("你好呀").kind == "greeting"
    assert classify_interaction_intent("你太机械了").kind == "conversation_feedback"
    assert classify_interaction_intent("你是故意重复吗").kind == "conversation"
    assert classify_interaction_intent("做为一个员工，你需要薪酬吗").kind == "conversation"


def test_interaction_intent_keeps_business_query_as_business() -> None:
    assert classify_interaction_intent("主营业务").kind == "business"
    assert classify_interaction_intent("帮我看看企业工作负荷").kind == "business"
    assert classify_interaction_intent("有哪些任务需要我处理").kind == "business"


def test_interaction_intent_keeps_external_realtime_query_out_of_smalltalk() -> None:
    assert classify_interaction_intent("今天苏州天气怎么样").kind == "business"
    assert classify_interaction_intent("附近有打印店吗").kind == "business"
