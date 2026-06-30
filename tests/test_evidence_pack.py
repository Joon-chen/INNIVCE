from app.services.cognitive.evidence_pack import (
    EVIDENCE_PACK_VERSION,
    build_evidence_pack_from_extraction,
    build_evidence_pack_from_text,
    evidence_pack_payload,
)
from app.shared.file_intelligence import ExtractionResult


def test_evidence_pack_is_generic_and_traceable() -> None:
    pack = build_evidence_pack_from_text(
        "固势（苏州）科技有限公司 GAUSTEK SRI 全系列产品手册，让测试更简单，让实验更高效。",
        filename="固势宣传册.pdf",
        source_system="registered_resource",
        source_object_id="file_pdf",
        source_object_type="pdf",
        organization_binding={"company_id": "company-1"},
        visibility_binding={"scope": "company"},
    )
    payload = evidence_pack_payload(pack)

    assert payload["pack_version"] == EVIDENCE_PACK_VERSION
    assert payload["source_ref"]["source_object_id"] == "file_pdf"
    assert payload["content_profile"]["document_type"] == "pdf"
    assert payload["key_claims"]
    assert payload["entities"]
    assert payload["evidence_spans"]
    assert "company_products" not in payload
    assert "company_customers" not in payload


def test_evidence_pack_uses_page_level_extraction_spans() -> None:
    pack = build_evidence_pack_from_extraction(
        ExtractionResult(
            success=True,
            text="公司介绍 固势（苏州）科技有限公司\n产品系列 GAUSTEK SRI",
            filename="固势宣传册.pdf",
            mime_type="application/pdf",
            extractor="pdf_embedded_ocr",
            page_count=2,
            metadata={
                "page_texts": [
                    {"page": 1, "text": "公司介绍 固势（苏州）科技有限公司", "source": "embedded"},
                    {"page": 2, "text": "产品系列 GAUSTEK SRI", "source": "ocr"},
                ]
            },
        ),
        source_system="registered_resource",
        source_object_id="file_pdf",
        source_object_type="pdf",
        organization_binding={"company_id": "company-1"},
        visibility_binding={"scope": "company"},
    )
    payload = evidence_pack_payload(pack)

    assert payload["evidence_spans"][0] == {
        "span_id": "p1s1",
        "page": 1,
        "text": "公司介绍 固势（苏州）科技有限公司",
        "source": "embedded",
    }
    assert payload["evidence_spans"][1]["span_id"] == "p2s1"
    assert payload["outline"][0]["page_refs"] == [1]
    assert payload["key_claims"][0]["evidence_refs"][0]["span_id"].startswith("p")
