from app.services.feishu import approval_formatters
from app.services.feishu.approval_attachments import ApprovalAttachmentReadResult


def test_select_approval_item_matches_index_and_instance_code() -> None:
    items = [
        {"serial_number": "A-001", "approval_name": "付款审批"},
        {"serial_number": "A-002", "approval_name": "报销审批"},
    ]

    assert approval_formatters.select_approval_item(items, "通过第2条") == items[1]
    assert approval_formatters.select_approval_item(items, "通过单号A-001") == items[0]


def test_format_attachment_read_results_uses_preview_and_error() -> None:
    lines = approval_formatters.format_attachment_read_results(
        [
            ApprovalAttachmentReadResult(name="合同.pdf", token="file_1", text_preview="合同金额 10000 元"),
            ApprovalAttachmentReadResult(name="发票.pdf", token="file_2", error="无权限"),
        ]
    )

    assert "合同金额 10000 元" in "\n".join(lines)
    assert "读取失败，无权限" in "\n".join(lines)


def test_format_approval_detail_lines_includes_key_fields_and_attachment_state() -> None:
    item = {
        "approval_name": "Payment Request",
        "serial_number": "A-001",
        "initiator_names": ["王东升"],
        "instance_detail": {
            "form": '[{"name":"付款金额","value":3000},{"name":"付款事由","value":"客户项目采购"},{"name":"合同附件","value":[{"file_token":"file_1","name":"合同.pdf"}]}]'
        },
    }

    text = "\n".join(approval_formatters.format_approval_detail_lines(item))

    assert "付款审批｜王东升" in text
    assert "单号：A-001" in text
    assert "付款金额" in text
    assert "附件正文：已识别附件引用；尚未执行读取解析。" in text


def test_format_pending_approval_tasks_uses_attachment_summary() -> None:
    form = (
        '[{"name":"合同附件","type":"attachmentV2","value":['
        '{"file_token":"file_123","name":"居间服务协议.pdf","type":"pdf"}'
        ']},{"name":"用章事由","value":"居间服务协议盖章"}]'
    )

    lines = approval_formatters.format_pending_approval_tasks(
        [
            {
                "approval_name": "用章用印申请",
                "initiator_names": ["余莲莲"],
                "instance_detail": {"form": form},
                "_attachment_results": [
                    ApprovalAttachmentReadResult(
                        name="居间服务协议.pdf",
                        token="file_123",
                        storage_key="approval_attachments/demo.pdf",
                        text_preview="甲方：固势（苏州）科技有限公司；服务内容：居间服务。",
                    )
                ],
            }
        ],
        limit=8,
    )

    text = "\n".join(lines)
    assert "老板，待你审批 1 个" in text
    assert "附件已读1/1" in text
    assert "附件：已读摘要 1 个" in text
    assert "回复：通过第N条 / 拒绝第N条 / 展开第N条" in text


def test_readable_approval_name_localizes_known_english_names_and_skips_ids() -> None:
    item = {
        "approval_name": "approval_123",
        "definition_name": "Payment Request",
    }

    assert approval_formatters.readable_approval_name(item) == "付款审批"


def test_format_approval_form_summary_prioritizes_business_fields() -> None:
    item = {
        "instance_detail": {
            "form": [
                {"name": "说明 1", "value": "widget raw"},
                {"name": "申请金额", "value": "50000"},
                {"name": "付款事由", "value": "项目采购"},
            ]
        }
    }

    lines = approval_formatters.format_approval_form_summary(item, max_fields=2)

    assert lines == ["申请金额：50000", "付款事由：项目采购"]


def test_approval_field_display_value_maps_option_labels() -> None:
    field = {
        "value": ["opt_1", "opt_2"],
        "option": [
            {"id": "opt_1", "text": {"zh_cn": "华东"}},
            {"id": "opt_2", "name": "华南"},
        ],
    }

    assert approval_formatters.approval_field_display_value(field) == "华东、华南"


def test_stringify_approval_field_value_prefers_nested_display_fields() -> None:
    assert approval_formatters.stringify_approval_field_value({"display_name": "张三", "id": "ou_1"}) == "张三"
