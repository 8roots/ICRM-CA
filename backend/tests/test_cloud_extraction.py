"""Mocked DeepSeek contract and failure tests for the cloud extraction path."""

import io
from datetime import date

import pytest

from app.cloud_extraction import CloudCandidate, CloudExtractionError
from app.extraction_service import PROMPT_VERSION, run_candidate_extraction
from app.main import create_app
from app.models import (
    Application,
    Base,
    CandidateFact,
    CloudExtractionCall,
    Document,
    DocumentOutput,
    JobStatus,
    User,
)
from app.parsed_outputs import store_parsed_output
from app.redaction import RedactionError
from app.structured import parse_structured

CLOUD_MATERIAL = """# 贷款申请材料

抵押物为杭州市西湖区某某路1号房产。
贷款金额：500万元。
身份证号：330102199001011234，联系电话：13800138000。
"""


class MockCloud:
    enabled = True
    model = "deepseek-mock"
    extractor_version = "mock-client-1"

    def __init__(self, results=None, error=None) -> None:
        self.results = results or []
        self.error = error
        self.calls: list[list] = []

    def extract(self, slices):
        self.calls.append(slices)
        if self.error:
            raise CloudExtractionError(self.error)
        return [
            CloudCandidate(
                field_key=item["field_key"],
                value=item["value"],
                confidence=item.get("confidence", 0.8),
                source_refs=[],
            )
            for item in self.results
        ]


@pytest.fixture
def app():
    created = create_app("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(created.state.database.engine)
    return created


def build(app, text: str) -> tuple[DocumentOutput, str, str]:
    parsed = parse_structured("material.md", io.BytesIO(text.encode()))
    with app.state.database.session() as db:
        user = User(username="owner", password_hash="hash", role="approval_officer")
        db.add(user)
        db.flush()
        application = Application(
            borrower_type="corporate",
            borrower_name="示例企业",
            product="经营贷",
            application_date=date(2026, 8, 7),
            owner_id=user.id,
        )
        db.add(application)
        db.flush()
        document = Document(
            application_id=application.id,
            filename="material.md",
            extension=".md",
            declared_mime="text/markdown",
            size_bytes=len(text),
            sha256="c" * 64,
            object_key="material",
        )
        db.add(document)
        db.flush()
        output = store_parsed_output(db, document.id, parsed)
        db.commit()
        return output, application.id, document.id


def stored_facts(app, output_id: str) -> list[CandidateFact]:
    with app.state.database.session() as db:
        return db.query(CandidateFact).filter_by(output_id=output_id).all()


def audit_rows(app, application_id: str) -> list[CloudExtractionCall]:
    with app.state.database.session() as db:
        return db.query(CloudExtractionCall).filter_by(application_id=application_id).all()


def test_cloud_receives_only_redacted_slice_for_unresolved_field(app) -> None:
    output, application_id, document_id = build(app, CLOUD_MATERIAL)
    cloud = MockCloud(
        results=[
            {"field_key": "collateral_type", "value": "西湖区某某路1号房产", "confidence": 0.9}
        ]
    )
    with app.state.database.session() as db:
        result = run_candidate_extraction(
            db, db.get(Document, document_id), output, cloud
        )

    assert result.step_status == JobStatus.SUCCESS
    assert result.error_code is None
    assert len(cloud.calls) == 1
    slices = cloud.calls[0]
    assert [item.field_key for item in slices] == ["collateral_type"]
    sent = " ".join(item.text for item in slices)
    # Direct identifiers and the alias mapping are absent from what the cloud sees.
    assert "330102199001011234" not in sent
    assert "13800138000" not in sent
    assert "[身份证" in sent
    assert "抵押物" in sent

    facts = stored_facts(app, output.id)
    keys = {fact.field_key for fact in facts}
    assert "loan_amount" in keys  # local extraction always runs first
    deepseek = [fact for fact in facts if fact.extractor == "deepseek"]
    assert len(deepseek) == 1
    assert deepseek[0].field_key == "collateral_type"
    assert deepseek[0].model_version == "deepseek-mock"
    assert deepseek[0].prompt_version == PROMPT_VERSION
    assert deepseek[0].typed_value["value"] == "西湖区某某路1号房产"

    calls = audit_rows(app, application_id)
    assert len(calls) == 1
    assert calls[0].status == "success"
    assert calls[0].redacted_request["slices"][0]["text"] == slices[0].text
    assert calls[0].redacted_response["results"][0]["field_key"] == "collateral_type"


def test_local_candidates_remain_when_cloud_is_unavailable(app) -> None:
    output, application_id, document_id = build(app, CLOUD_MATERIAL)
    cloud = MockCloud(error="deepseek_unavailable")
    with app.state.database.session() as db:
        result = run_candidate_extraction(
            db, db.get(Document, document_id), output, cloud
        )

    assert result.step_status == JobStatus.PARTIAL_SUCCESS
    assert result.error_code == "deepseek_unavailable"
    facts = stored_facts(app, output.id)
    assert {fact.field_key for fact in facts} >= {"loan_amount", "id_card"}
    assert not [fact for fact in facts if fact.extractor == "deepseek"]
    calls = audit_rows(app, application_id)
    assert len(calls) == 1
    assert calls[0].status == "cloud_unavailable"
    assert calls[0].error_code == "deepseek_unavailable"


def test_redaction_failure_prevents_the_cloud_request(app, monkeypatch) -> None:
    output, application_id, document_id = build(app, CLOUD_MATERIAL)
    cloud = MockCloud(results=[{"field_key": "collateral_type", "value": "x"}])

    def failing_verify(text: str) -> None:
        raise RedactionError("unredacted_identifier")

    monkeypatch.setattr("app.extraction_service.verify_redaction", failing_verify)
    with app.state.database.session() as db:
        result = run_candidate_extraction(
            db, db.get(Document, document_id), output, cloud
        )

    assert result.step_status == JobStatus.PARTIAL_SUCCESS
    assert result.error_code == "redaction_failed"
    assert cloud.calls == []  # the cloud request was never attempted
    facts = stored_facts(app, output.id)
    assert {fact.field_key for fact in facts} >= {"loan_amount", "id_card"}
    calls = audit_rows(app, application_id)
    assert len(calls) == 1
    assert calls[0].status == "redaction_failed"
    assert calls[0].error_code == "redaction_failed"
    # Fail closed: the raw slice content is never persisted, not even in audit.
    assert calls[0].redacted_request["slices"] == []
    stored_text = str(calls[0].redacted_request)
    assert "330102199001011234" not in stored_text
    assert "13800138000" not in stored_text


def test_low_confidence_candidate_leaves_field_open_for_cloud(app) -> None:
    material = "# 材料\n\n抵押物：某房产（待核实）\n贷款金额：500万元\n"
    output, application_id, document_id = build(app, material)
    # Seed local candidates first (cloud disabled), then lower the confidence
    # below the ambiguity bar so the cloud path is still triggered.
    with app.state.database.session() as db:
        document = db.get(Document, document_id)
        run_candidate_extraction(db, document, output)
        local = (
            db.query(CandidateFact)
            .filter_by(field_key="collateral_type", extractor="local_rule")
            .one()
        )
        local.confidence = 0.4
        db.commit()
    cloud = MockCloud(
        results=[
            {"field_key": "collateral_type", "value": "某房产", "confidence": 0.95}
        ]
    )
    with app.state.database.session() as db:
        result = run_candidate_extraction(
            db, db.get(Document, document_id), output, cloud
        )

    assert result.step_status == JobStatus.SUCCESS
    assert [item.field_key for item in cloud.calls[0]] == ["collateral_type"]
    facts = stored_facts(app, output.id)
    deepseek = [fact for fact in facts if fact.extractor == "deepseek"]
    assert len(deepseek) == 1
    assert deepseek[0].typed_value["value"] == "某房产"


def test_disabled_cloud_keeps_local_candidates_without_audit_rows(app) -> None:
    output, application_id, document_id = build(app, CLOUD_MATERIAL)
    with app.state.database.session() as db:
        result = run_candidate_extraction(db, db.get(Document, document_id), output, None)

    assert result.step_status == JobStatus.SUCCESS
    assert audit_rows(app, application_id) == []
    facts = stored_facts(app, output.id)
    assert {fact.field_key for fact in facts} >= {"loan_amount", "id_card"}


def test_rerun_does_not_duplicate_identical_candidates(app) -> None:
    output, _, document_id = build(app, CLOUD_MATERIAL)
    cloud = MockCloud(
        results=[
            {"field_key": "collateral_type", "value": "西湖区某某路1号房产", "confidence": 0.9}
        ]
    )
    with app.state.database.session() as db:
        document = db.get(Document, document_id)
        first = run_candidate_extraction(db, document, output, cloud)
        second = run_candidate_extraction(db, document, output, cloud)
    assert first.step_status == JobStatus.SUCCESS
    assert second.step_status == JobStatus.SUCCESS
    facts = stored_facts(app, output.id)
    assert len(facts) == len({(f.field_key, f.raw_text, f.extractor) for f in facts})
