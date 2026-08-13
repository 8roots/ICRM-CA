"""Worker-level candidate extraction step: local-first, retryable cloud gaps."""

import io
from datetime import date

import pytest

from app.cloud_extraction import CloudCandidate, CloudExtractionError
from app.main import create_app
from app.models import (
    Application,
    Base,
    CandidateFact,
    Document,
    DocumentJob,
    DocumentOutput,
    ProcessingStep,
    User,
)
from app.worker import process_one

MATERIAL = """# 贷款申请材料

抵押物为杭州市西湖区某某路1号房产。
贷款金额：500万元。
身份证号：330102199001011234。
"""


class MaterialObjects:
    def open(self, key: str):
        return io.BytesIO(MATERIAL.encode())


class GoodCloud:
    enabled = True
    model = "deepseek-mock"
    extractor_version = "mock-client-1"

    def extract(self, slices):
        return [
            CloudCandidate(
                field_key="collateral_type",
                value="西湖区某某路1号房产",
                confidence=0.9,
                source_refs=[],
            )
        ]


class FailingCloud:
    enabled = True
    model = "deepseek-mock"
    extractor_version = "mock-client-1"

    def extract(self, slices):
        raise CloudExtractionError("deepseek_unavailable")


@pytest.fixture
def app():
    created = create_app("sqlite+pysqlite:///:memory:", object_store=MaterialObjects())
    Base.metadata.create_all(created.state.database.engine)
    with created.state.database.session() as db:
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
            size_bytes=len(MATERIAL),
            sha256="f" * 64,
            object_key="material",
        )
        db.add(document)
        db.flush()
        job = DocumentJob(document_id=document.id)
        for name in ("validation", "parsing_ocr", "candidate_extraction"):
            job.steps.append(ProcessingStep(name=name, status="waiting"))
        db.add(job)
        db.commit()
        created.state.application_id = application.id
        created.state.job_id = job.id
    return created


def test_worker_runs_candidate_extraction_after_parsing(app) -> None:
    assert process_one(app.state.database, MaterialObjects(), "worker", cloud_client=GoodCloud())
    with app.state.database.session() as db:
        job = db.get(DocumentJob, app.state.job_id)
        steps = {step.name: (step.status, step.error_code) for step in job.steps}
        assert steps["validation"] == ("success", None)
        assert steps["parsing_ocr"] == ("success", None)
        assert steps["candidate_extraction"] == ("success", None)
        assert job.status == "success"
        assert job.document.processing_status == "success"
        assert job.document.review_status == "pending_review"
        facts = db.query(CandidateFact).all()
        assert {fact.field_key for fact in facts} >= {"loan_amount", "id_card"}
        assert any(
            fact.field_key == "collateral_type" and fact.extractor == "deepseek" for fact in facts
        )


def test_cloud_outage_leaves_local_candidates_and_marks_step_retryable(app) -> None:
    assert process_one(app.state.database, MaterialObjects(), "worker", cloud_client=FailingCloud())
    with app.state.database.session() as db:
        job = db.get(DocumentJob, app.state.job_id)
        assert job.status == "partial_success"
        assert job.error_code == "deepseek_unavailable"
        steps = {step.name: (step.status, step.error_code) for step in job.steps}
        assert steps["candidate_extraction"] == ("partial_success", "deepseek_unavailable")
        facts = db.query(CandidateFact).all()
        assert {fact.field_key for fact in facts} >= {"loan_amount", "id_card"}
        assert not [fact for fact in facts if fact.extractor == "deepseek"]


def test_retry_reruns_only_candidate_extraction_after_cloud_outage(app) -> None:
    assert process_one(app.state.database, MaterialObjects(), "worker", cloud_client=FailingCloud())
    # The retry endpoint re-queues only the failed candidate_extraction step.
    with app.state.database.session() as db:
        job = db.get(DocumentJob, app.state.job_id)
        job.status = "waiting"
        job.error_code = None
        for step in job.steps:
            if step.name == "candidate_extraction":
                step.status = "waiting"
                step.error_code = None
        db.commit()
    # A second run with the cloud healthy completes the gap without re-parsing.
    assert process_one(app.state.database, MaterialObjects(), "worker", cloud_client=GoodCloud())
    with app.state.database.session() as db:
        job = db.get(DocumentJob, app.state.job_id)
        assert job.status == "success"
        steps = {step.name: (step.status, step.error_code) for step in job.steps}
        assert steps["candidate_extraction"] == ("success", None)
        assert steps["parsing_ocr"] == ("success", None)
        facts = db.query(CandidateFact).filter_by(extractor="deepseek").all()
        assert [fact.field_key for fact in facts] == ["collateral_type"]


def test_parse_rerun_produces_new_version_candidates_without_overwriting(app) -> None:
    assert process_one(app.state.database, MaterialObjects(), "worker", cloud_client=GoodCloud())
    with app.state.database.session() as db:
        first_version = db.query(DocumentOutput).one().version
        first_count = db.query(CandidateFact).count()
        job = db.get(DocumentJob, app.state.job_id)
        parse_step = next(step for step in job.steps if step.name == "parsing_ocr")
        extraction_step = next(step for step in job.steps if step.name == "candidate_extraction")
        # Rerunning the parser must also rerun extraction on the new output.
        parse_step.status = "waiting"
        extraction_step.status = "waiting"
        job.status = "waiting"
        db.commit()

    assert process_one(app.state.database, MaterialObjects(), "worker", cloud_client=GoodCloud())
    with app.state.database.session() as db:
        versions = [output.version for output in db.query(DocumentOutput).all()]
        assert versions == [first_version, first_version + 1]
        facts = db.query(CandidateFact).all()
        assert len(facts) > first_count
        output_ids = {fact.output_id for fact in facts}
        assert len(output_ids) == 2  # candidates from both output versions coexist
