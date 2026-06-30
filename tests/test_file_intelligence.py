from io import BytesIO
from zipfile import ZipFile

from app.shared.file_intelligence import pdf as file_intelligence_pdf
from app.shared.file_intelligence import ExtractionResult, extract_file, extract_file_text


def test_extract_file_returns_contract_for_plain_text() -> None:
    result = extract_file("合同金额：10000".encode(), filename="合同.txt", mime_type="text/plain")

    assert isinstance(result, ExtractionResult)
    assert result.success is True
    assert result.text == "合同金额：10000"
    assert result.mime_type == "text/plain"
    assert result.filename == "合同.txt"
    assert result.extractor == "text"


def test_extract_file_reads_html_text() -> None:
    result = extract_file(
        "<html><body><h1>公司介绍</h1><script>hide()</script></body></html>".encode(),
        filename="profile.html",
    )

    assert result.success is True
    assert result.extractor == "html"
    assert result.text == "公司介绍"


def test_extract_file_reads_docx_text() -> None:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr(
            "word/document.xml",
            (
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                "<w:body><w:p><w:r><w:t>居间服务协议</w:t></w:r></w:p></w:body></w:document>"
            ),
        )

    result = extract_file(buffer.getvalue(), filename="协议.docx")

    assert result.success is True
    assert result.extractor == "docx"
    assert "居间服务协议" in result.text


def test_extract_file_reads_xlsx_text() -> None:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr(
            "xl/sharedStrings.xml",
            (
                '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                "<si><t>供应商</t></si><si><t>测试公司</t></si></sst>"
            ),
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            (
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                '<sheetData><row><c t="s"><v>0</v></c><c t="s"><v>1</v></c></row></sheetData></worksheet>'
            ),
        )

    result = extract_file(buffer.getvalue(), filename="明细.xlsx")

    assert result.success is True
    assert result.extractor == "xlsx"
    assert "供应商 | 测试公司" in result.text


def test_extract_file_reads_pptx_text() -> None:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr(
            "ppt/slides/slide1.xml",
            (
                '<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
                'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
                "<p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>产品路线图</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld>"
                "</p:sld>"
            ),
        )

    result = extract_file(buffer.getvalue(), filename="路线图.pptx")

    assert result.success is True
    assert result.extractor == "pptx"
    assert "产品路线图" in result.text


def test_extract_file_text_is_compatibility_convenience() -> None:
    assert extract_file_text(b"hello", filename="note.md") == "hello"


def test_extract_pdf_merges_short_embedded_text_with_ocr(monkeypatch) -> None:
    monkeypatch.setattr(
        file_intelligence_pdf,
        "extract_pdf_embedded_text",
        lambda data, *, filename, mime_type, max_chars: ExtractionResult(
            success=True,
            text="封面 公司介绍",
            mime_type=mime_type,
            filename=filename,
            extractor="pdf_embedded",
            page_count=8,
        ),
    )
    monkeypatch.setattr(
        file_intelligence_pdf,
        "extract_pdf_ocr_text",
        lambda data, *, filename, mime_type, max_chars: ExtractionResult(
            success=True,
            text="产品体系 GAUSTEK SRI\n应用场景 实验室 研发 工业",
            mime_type=mime_type,
            filename=filename,
            extractor="pdf_ocr",
            page_count=8,
        ),
    )

    result = file_intelligence_pdf.extract_pdf(b"pdf", filename="profile.pdf", mime_type="application/pdf", max_chars=2000)

    assert result.success is True
    assert result.extractor == "pdf_embedded_ocr"
    assert "封面 公司介绍" in result.text
    assert "产品体系 GAUSTEK SRI" in result.text
    assert result.metadata["embedded_text_chars"] > 0
    assert result.metadata["ocr_text_chars"] > 0


def test_extract_pdf_merges_page_level_metadata(monkeypatch) -> None:
    monkeypatch.setattr(
        file_intelligence_pdf,
        "extract_pdf_embedded_text",
        lambda data, *, filename, mime_type, max_chars: ExtractionResult(
            success=True,
            text="封面 公司介绍",
            mime_type=mime_type,
            filename=filename,
            extractor="pdf_embedded",
            page_count=2,
            metadata={"page_texts": [{"page": 1, "text": "封面 公司介绍", "source": "embedded"}]},
        ),
    )
    monkeypatch.setattr(
        file_intelligence_pdf,
        "extract_pdf_ocr_text",
        lambda data, *, filename, mime_type, max_chars: ExtractionResult(
            success=True,
            text="产品体系 GAUSTEK SRI",
            mime_type=mime_type,
            filename=filename,
            extractor="pdf_ocr",
            page_count=2,
            metadata={"page_texts": [{"page": 2, "text": "产品体系 GAUSTEK SRI", "source": "ocr"}]},
        ),
    )

    result = file_intelligence_pdf.extract_pdf(b"pdf", filename="profile.pdf", mime_type="application/pdf", max_chars=2000)

    assert result.metadata["page_texts"] == [
        {"page": 1, "text": "封面 公司介绍", "source": "embedded"},
        {"page": 2, "text": "产品体系 GAUSTEK SRI", "source": "ocr"},
    ]
