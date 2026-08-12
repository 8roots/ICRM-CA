import io
import zipfile

import pytest

from app.models import Document
from app.validation import ValidationError, validate


def archive(*names: str) -> bytes:
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w") as package:
        for name in names:
            package.writestr(name, "content")
    return result.getvalue()


class BoundedReads(io.BytesIO):
    def read(self, size: int = -1) -> bytes:
        assert 0 <= size <= 1024 * 1024
        return super().read(size)


def document(filename: str, mime: str) -> Document:
    return Document(
        application_id="application",
        filename=filename,
        extension="." + filename.rsplit(".", 1)[-1],
        declared_mime=mime,
        size_bytes=1,
        sha256="a" * 64,
        object_key="object",
    )


@pytest.mark.parametrize(
    ("filename", "mime", "content"),
    [
        ("a.pdf", "application/pdf", b"%PDF-1.7"),
        (
            "a.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            archive("[Content_Types].xml", "word/document.xml"),
        ),
        (
            "a.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            archive("[Content_Types].xml", "xl/workbook.xml"),
        ),
        ("a.csv", "text/csv", b"column\nvalue"),
        ("a.md", "text/markdown", b"# title"),
        ("a.png", "image/png", b"\x89PNG\r\n\x1a\n"),
        ("a.jpeg", "image/jpeg", b"\xff\xd8\xff"),
        ("a.tiff", "image/tiff", b"II*\x00"),
    ],
)
def test_supported_signatures(filename: str, mime: str, content: bytes) -> None:
    validate(document(filename, mime), io.BytesIO(content))


def test_text_validation_reads_incrementally() -> None:
    validate(document("a.csv", "text/csv"), BoundedReads(b"column,value\n1,2"))


def test_pdf_encryption_marker_split_across_chunks_is_detected() -> None:
    content = b"%PDF-1.7" + b" " * (1024 * 1024 - 11) + b"trailer << /En" + b"crypt 4 0 R >>"
    with pytest.raises(ValidationError) as caught:
        validate(document("a.pdf", "application/pdf"), io.BytesIO(content))
    assert caught.value.code == "encrypted_input"


def test_pdf_xref_stream_encryption_is_detected() -> None:
    content = b"%PDF-1.7\n8 0 obj << /Type /XRef /Encrypt 4 0 R /Root 1 0 R >> stream"
    with pytest.raises(ValidationError) as caught:
        validate(document("a.pdf", "application/pdf"), io.BytesIO(content))
    assert caught.value.code == "encrypted_input"


def test_pdf_encrypt_text_outside_trailer_is_not_encryption() -> None:
    validate(
        document("a.pdf", "application/pdf"),
        io.BytesIO(
            b"%PDF-1.7\nstream /Encrypt is ordinary text endstream\ntrailer << /Root 1 0 R >>"
        ),
    )


@pytest.mark.parametrize(
    ("filename", "mime", "content", "code", "manual"),
    [
        ("a.pdf", "text/plain", b"%PDF-1.7", "mime_mismatch", False),
        ("a.pdf", "application/pdf", b"not pdf", "signature_mismatch", False),
        (
            "a.pdf",
            "application/pdf",
            b"%PDF-1.7" + b" " * 9000 + b"trailer << /Encrypt 4 0 R >>",
            "encrypted_input",
            True,
        ),
        (
            "a.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            b"\xd0\xcf\x11\xe0",
            "encrypted_input",
            True,
        ),
        (
            "a.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            archive("anything.txt"),
            "signature_mismatch",
            False,
        ),
        (
            "a.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            archive("[Content_Types].xml", "word/document.xml", "word/vbaProject.bin"),
            "unsupported_macro",
            True,
        ),
        ("a.csv", "text/csv", b"\x00\x01\x02", "signature_mismatch", False),
        ("a.md", "text/markdown", b"\xff\xfe\xfd", "signature_mismatch", False),
    ],
)
def test_mismatch_and_encryption_outcomes(
    filename: str, mime: str, content: bytes, code: str, manual: bool
) -> None:
    with pytest.raises(ValidationError) as caught:
        validate(document(filename, mime), io.BytesIO(content))
    assert caught.value.code == code
    assert caught.value.manual_handling is manual
