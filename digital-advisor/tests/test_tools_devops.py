from types import SimpleNamespace

from app.services.tools.base import ToolRequest
from app.services.tools.providers.devops import execute_devops_tool


def test_feishu_cli_status_uses_fixed_read_only_cli_commands(monkeypatch) -> None:
    calls = []

    def fake_run(command, *, capture_output, check, text, timeout):
        calls.append(command)
        assert capture_output is True
        assert check is False
        assert text is True
        assert timeout == 5
        if command[1] == "--version":
            return SimpleNamespace(stdout="lark-cli version 1.0.52\n", stderr="", returncode=0)
        return SimpleNamespace(
            stdout=(
                "lark-cli\n\n"
                "Available Commands:\n"
                "  api         Generic Lark API requests\n"
                "  approval    Approval instance, and task management\n"
                "  attendance  attendance record query\n"
                "  base        Table, field, record, view, dashboard, workflow, form, role & permission management\n"
                "  calendar    Calendar, event, and attendee management\n"
                "  contact     Contacts operations\n"
                "  docs        Document and content operations\n"
                "  drive       File, comment, permission, and upload management\n"
                "  event       Consume and manage real-time events\n"
                "  im          Message and group chat management\n"
                "  mail        Email, draft, folder, and contacts management\n"
                "  minutes     Minutes content and metadata retrieval\n"
                "  okr         Lark OKR objectives, key results, alignments, indicators, progresses\n"
                "  task        Task, task list, and subtask management\n"
                "  vc          Video conference and meeting note management\n"
                "  wiki        Wiki space and node management\n\n"
            ),
            stderr="",
            returncode=0,
        )

    monkeypatch.setattr("app.services.tools.providers.devops.shutil.which", lambda name: "/opt/homebrew/bin/lark-cli")
    monkeypatch.setattr("app.services.tools.providers.devops.subprocess.run", fake_run)

    answer = execute_devops_tool(
        None,
        ToolRequest(
            tool_name="feishu_cli_status",
            question="飞书 CLI 状态",
            normalized_command="飞书 CLI 状态",
            params={"command": "api DELETE /open-apis/unsafe"},
        ),
    )

    assert "飞书 CLI 可用" in answer
    assert "版本: lark-cli version 1.0.52" in answer
    assert "版本命令退出码: 0" in answer
    assert "帮助命令退出码: 0" in answer
    assert (
        "关键命令: api=ok、approval=ok、attendance=ok、base=ok、calendar=ok、contact=ok、docs=ok、drive=ok、"
        "event=ok、im=ok、mail=ok、minutes=ok、okr=ok、task=ok、vc=ok、wiki=ok"
    ) in answer
    assert "可用命令: api、approval、attendance、base、calendar、contact、docs、drive、event、im、mail、minutes" in answer
    assert calls == [["/opt/homebrew/bin/lark-cli", "--version"], ["/opt/homebrew/bin/lark-cli", "--help"]]


def test_feishu_cli_status_reports_missing_cli(monkeypatch) -> None:
    monkeypatch.setattr("app.services.tools.providers.devops.shutil.which", lambda name: None)

    answer = execute_devops_tool(
        None,
        ToolRequest(tool_name="feishu_cli_status", question="飞书 CLI 状态", normalized_command="飞书 CLI 状态"),
    )

    assert "未安装" in answer


def test_feishu_cli_doctor_uses_fixed_offline_health_check(monkeypatch) -> None:
    calls = []

    def fake_run(command, *, capture_output, check, text, timeout):
        calls.append(command)
        assert capture_output is True
        assert check is False
        assert text is True
        assert timeout == 5
        return SimpleNamespace(
            stdout=(
                '{"checks":[{"name":"cli_version","status":"pass","message":"1.0.52"},'
                '{"name":"config_file","status":"pass","message":"config.json found"},'
                '{"name":"endpoint_open","status":"skip","message":"skipped (--offline)"}],'
                '"ok":true,"workspace":"local"}'
            ),
            stderr="",
            returncode=0,
        )

    monkeypatch.setattr("app.services.tools.providers.devops.shutil.which", lambda name: "/opt/homebrew/bin/lark-cli")
    monkeypatch.setattr("app.services.tools.providers.devops.subprocess.run", fake_run)

    answer = execute_devops_tool(
        None,
        ToolRequest(
            tool_name="feishu_cli_doctor",
            question="飞书 CLI 健康检查",
            normalized_command="飞书 CLI 健康检查",
            params={"command": "doctor"},
        ),
    )

    assert "飞书 CLI 离线健康检查完成" in answer
    assert "退出码: 0" in answer
    assert "ok=true; cli_version=pass (1.0.52)" in answer
    assert "config_file=pass (config.json found)" in answer
    assert "endpoint_open=skip (skipped (--offline))" in answer
    assert "workspace=local" in answer
    assert calls == [["/opt/homebrew/bin/lark-cli", "doctor", "--offline"]]
