from app.services.agent.complex_task_matrix import should_allow_query_to_bitable_route


def test_query_to_bitable_matrix_allows_query_routes_for_non_complex_categories() -> None:
    for category in ("query", "action"):
        assert should_allow_query_to_bitable_route(route_hint="task_qa", execution_category=category) is True
        assert should_allow_query_to_bitable_route(route_hint="mail_qa", execution_category=category) is True
        assert should_allow_query_to_bitable_route(route_hint="chat_tasks", execution_category=category) is True


def test_query_to_bitable_matrix_blocks_analysis_and_decision_for_supported_routes() -> None:
    for category in ("analysis", "decision"):
        assert should_allow_query_to_bitable_route(route_hint="approval_qa", execution_category=category) is False
        assert should_allow_query_to_bitable_route(route_hint="personal_tasks", execution_category=category) is False
        assert should_allow_query_to_bitable_route(route_hint="feishu_approval_task_query", execution_category=category) is False


def test_query_to_bitable_matrix_always_allows_bitable_qa_route() -> None:
    for category in (None, "analysis", "decision", "action", "query"):
        assert should_allow_query_to_bitable_route(route_hint="bitable_qa", execution_category=category) is True


def test_query_to_bitable_matrix_only_applies_to_known_routes() -> None:
    for category in (None, "query", "analysis", "decision", "action"):
        assert should_allow_query_to_bitable_route(route_hint="calendar_qa", execution_category=category) is False


def test_query_to_bitable_matrix_blocks_decision_and_analysis_for_cross_domain_routes() -> None:
    for category in ("analysis", "decision"):
        for route_hint in ("task_qa", "personal_tasks", "mail_qa", "chat_tasks", "approval_qa", "feishu_approval_task_query"):
            assert should_allow_query_to_bitable_route(route_hint=route_hint, execution_category=category) is False
