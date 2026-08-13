"""Content-based material classification candidates."""

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.classification import (
    CATEGORY_KEYWORDS,
    MaterialCategory,
    classify_text,
    run_classification,
)
from app.models import Base, Document, MaterialClassificationCandidate, User


class BlockStub:
    def __init__(self, text: str) -> None:
        self.text = text
        self.cells = []


class PageStub:
    def __init__(self, text: str) -> None:
        self.blocks = [BlockStub(text)]


class OutputStub:
    def __init__(self, text: str) -> None:
        self.pages = [PageStub(text)]


def test_no_keywords_produce_no_candidates() -> None:
    assert classify_text("完全无关的普通文本，没有任何类别关键词") == []


def test_single_category_is_ranked_first() -> None:
    candidates = classify_text("企业名称：示例公司 统一社会信用代码：91330100MA27XW1234")
    assert candidates
    top_category, top_confidence = candidates[0]
    assert top_category == MaterialCategory.BASIC_INFO
    assert top_confidence == 1.0


def test_loan_application_keywords_win_over_weak_other_hits() -> None:
    text = "借款申请书 贷款申请 授信申请 统一社会信用代码：91330100MA27XW1234"
    categories = [category.value for category, _ in classify_text(text)]
    assert categories[0] == "loan_application"
    assert "basic_info" in categories


def test_candidates_are_capped_and_deterministic() -> None:
    text = " ".join(keyword for keywords in CATEGORY_KEYWORDS.values() for keyword in keywords)
    candidates = classify_text(text)
    assert len(candidates) <= 3
    assert classify_text(text) == candidates


def test_every_category_is_reachable() -> None:
    for category, keywords in CATEGORY_KEYWORDS.items():
        candidates = classify_text(keywords[0])
        assert any(candidate == category for candidate, _ in candidates), category.value


def _document(db: Session, filename: str, suffix: str) -> Document:
    document = Document(
        application_id="app",
        filename=filename,
        extension=".pdf",
        declared_mime="application/pdf",
        size_bytes=1,
        sha256=suffix * 64,
        object_key=suffix,
    )
    db.add(document)
    db.flush()
    return document


def test_run_classification_stores_candidates_once_per_document() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = User(username="owner", password_hash="hash", role="approval_officer")
        db.add(user)
        db.flush()
        document = _document(db, "材料.pdf", "a")
        run_classification(db, document, OutputStub("借款申请书 贷款申请 企业名称：示例公司"))
        run_classification(db, document, OutputStub("借款申请书 贷款申请 企业名称：示例公司"))
        rows = (
            db.query(MaterialClassificationCandidate)
            .filter_by(document_id=document.id)
            .all()
        )
        categories = {(row.category, row.method) for row in rows}
        assert ("loan_application", "content_keyword") in categories
        # each (category, method) stored exactly once
        assert len(rows) == len(categories)


def test_run_classification_stores_nothing_when_no_candidates() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = User(username="owner", password_hash="hash", role="approval_officer")
        db.add(user)
        db.flush()
        document = _document(db, "无类别.pdf", "b")
        run_classification(db, document, OutputStub("普通无关键词文本"))
        assert (
            db.query(MaterialClassificationCandidate)
            .filter_by(document_id=document.id)
            .count()
            == 0
        )
