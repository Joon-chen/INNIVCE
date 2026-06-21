from app.services.agent.policies import BotActor


def answer_general_conversation(*, question: str, actor: BotActor) -> str:
    text = question.strip()
    if _is_identity_question(text):
        return _identity_answer(actor)
    if _is_status_or_complaint(text):
        if actor.role == "owner":
            return "老板，我在。刚才如果没有回复，通常是审批卡片或飞书回调链路异常；你可以直接再说“待审批”，我会优先给文本结果。"
        return "我在。刚才如果没有回复，可以再发一次；我会按你的权限范围回答。"
    if _is_greeting(text):
        if actor.role == "owner":
            return "老板，我在。你可以直接问：待审批、今日重点、风险、项目、邮件或数据盲区。"
        return "我在。你可以问当前会话、本人相关事项，或已授权业务域内的问题。"
    if actor.role == "owner":
        return "老板，这个问题我还没识别成具体业务意图。你可以换成：待审批、今日重点、风险、项目、邮件、数据盲区。"
    return "这个问题我还没识别成可查询的信息范围。你可以问当前会话、本人相关事项，或已授权业务域内的问题。"


def _is_greeting(text: str) -> bool:
    return any(term in text for term in ["你好", "好呀", "在吗", "早", "晚上好", "hello", "hi"])


def _is_status_or_complaint(text: str) -> bool:
    return any(term in text for term in ["不理我", "没回复", "没有回复", "什么情况", "卡住", "掉线", "离线", "还在吗"])


def _is_identity_question(text: str) -> bool:
    return any(term in text for term in ["你是谁", "你能做什么", "你是什么", "我是谁", "我的权限"])


def _identity_answer(actor: BotActor) -> str:
    if actor.role == "owner":
        return "我是你的企业数字助理。你是系统所有者，我会按老板权限查询已授权公司数据，并保护员工和跨权限数据边界。"
    if actor.role in {"admin", "manager", "lead"}:
        domains = f"授权业务域：{', '.join(actor.domains)}。" if actor.domains else "暂未识别到具体授权业务域。"
        return f"我是企业数字助理。{domains}我会按你的角色、部门和业务域权限回答。"
    return "我是企业数字助理。你可以查询当前会话、本人相关事项和已授权公开知识。"
