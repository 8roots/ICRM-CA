"""Rule/LPR lifecycle, rule-context confirmation, formal redline runs, gates."""

import io
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import (
    Application,
    Base,
    LprImport,
    LprImportStatus,
    RulePackage,
    RuleStatus,
    User,
)
from app.security import hash_password


class MemoryObjects:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put(self, key: str, stream, length: int) -> None:
        chunks = []
        remaining = length
        while remaining != 0:
            chunk = stream.read(65536 if remaining < 0 else min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            if remaining > 0:
                remaining -= len(chunk)
        self.objects[key] = b"".join(chunks)

    def open(self, key: str):
        return io.BytesIO(self.objects[key])

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)


def setup(*, production: bool = False) -> TestClient:
    app = create_app(
        "sqlite+pysqlite:///:memory:",
        cookie_secure=True,
        production=production,
        object_store=MemoryObjects(),
    )
    Base.metadata.create_all(app.state.database.engine)
    with app.state.database.session() as db:
        admin = User(
            username="admin",
            password_hash=hash_password("administrator password"),
            role="administrator",
            enabled=True,
        )
        owner = User(
            username="owner",
            password_hash=hash_password("approval officer password"),
            role="approval_officer",
            enabled=True,
        )
        other = User(
            username="other",
            password_hash=hash_password("approval officer password"),
            role="approval_officer",
            enabled=True,
        )
        db.add_all([admin, owner, other])
        db.flush()
        for username, borrower_type in (("owner", "corporate"), ("other", "corporate")):
            db.add(
                Application(
                    borrower_type=borrower_type,
                    borrower_name=f"{username}企业",
                    product="经营贷",
                    application_date=date(2026, 8, 7),
                    proposed_signing_date=date(2026, 8, 20),
                    owner_id=owner.id if username == "owner" else other.id,
                )
            )
        db.commit()
    return TestClient(app, base_url="https://testserver")


def login(client: TestClient, username: str, password: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        headers={"Origin": "https://testserver"},
        json={"username": username, "password": password},
    )
    assert response.status_code == 204
    return {"X-CSRF-Token": client.cookies["icrm_csrf"]}


@pytest.fixture
def client() -> TestClient:
    client = setup()
    with client:
        yield client


def owner_application_id(client: TestClient, name: str = "owner企业") -> str:
    with client.app.state.database.session() as db:
        return db.query(Application).filter_by(borrower_name=name).one().id


def confirm_context(
    client: TestClient, csrf: dict[str, str], application_id: str, context: str = "全国"
) -> None:
    response = client.post(
        f"/api/v1/applications/{application_id}/rule-context",
        headers={**csrf, "Idempotency-Key": "ctx-1"},
        json={"context": context},
    )
    assert response.status_code == 201


def confirm_all_critical_inputs(
    client: TestClient, csrf: dict[str, str], application_id: str
) -> None:
    inputs = {
        "loan_amount": ("100000", "贷款金额 100000 元"),
        "loan_term": ("12", "期限 12 个月"),
        "interest_rate": ("12%", "年利率 12%"),
        "repayment_method": ("等额本息", "还款方式等额本息"),
        "loan_fees": ("0", "无必要费用"),
        "overdue_interest_rate": ("18%", "罚息利率 18%"),
    }
    for index, (field_key, (value, reason)) in enumerate(inputs.items()):
        response = client.post(
            f"/api/v1/applications/{application_id}/resolutions",
            headers={**csrf, "Idempotency-Key": f"res-{field_key}"},
            json={
                "resolution_type": "manual",
                "field_key": field_key,
                "value": value,
                "reason": reason,
            },
        )
        assert response.status_code == 201, (field_key, response.text)


# ---------------------------------------------------------------------------
# Demo seeding
# ---------------------------------------------------------------------------


def test_demo_rules_and_lpr_are_seeded_in_development(client: TestClient) -> None:
    with client.app.state.database.session() as db:
        rules = db.query(RulePackage).all()
        assert {rule.code for rule in rules} == {
            "DEMO-EFFECTIVE-COST-36",
            "DEMO-LPR-4X",
            "DEMO-RATE-24",
        }
        assert all(rule.status == RuleStatus.APPROVED for rule in rules)
        assert all(rule.demo_only for rule in rules)
        hard = next(rule for rule in rules if rule.kind == "hard")
        assert hard.calc_type == "effective_cost_limit"
        batches = db.query(LprImport).filter_by(demo_only=True).all()
        assert batches and all(batch.status == LprImportStatus.PUBLISHED for batch in batches)


def test_production_skips_demo_seeding() -> None:
    client = setup(production=True)
    with client:
        with client.app.state.database.session() as db:
            assert db.query(RulePackage).count() == 0
            assert db.query(LprImport).count() == 0


# ---------------------------------------------------------------------------
# Admin rule lifecycle
# ---------------------------------------------------------------------------


RULE_PAYLOAD = {
    "code": "CORP-EFFECTIVE-COST-2026",
    "name": "企业综合年化成本红线",
    "kind": "hard",
    "lender_qualification": "small_loan_company",
    "rule_context": "某省",
    "product": "流动资金贷",
    "effective_from": "2026-01-01",
    "effective_until": None,
    "calc_type": "effective_cost_limit",
    "params": {"threshold_pct": "36", "overdue_days": 90},
    "legal_basis": "机构法务批准的内部风险政策（示例）",
    "reviewer": "法务张三",
    "reviewed_at": "2026-06-01",
    "demo_only": False,
}


def test_admin_rule_lifecycle_and_immutability(client: TestClient) -> None:
    csrf = login(client, "admin", "administrator password")
    created = client.post("/api/v1/admin/rule-packages", headers=csrf, json=RULE_PAYLOAD)
    assert created.status_code == 201
    rule_id = created.json()["id"]
    assert created.json()["status"] == "draft"
    assert created.json()["version"] == 1
    assert created.json()["content_hash"]
    assert created.json()["calc_type_label"] == "综合年化成本上限"

    approved = client.post(f"/api/v1/admin/rule-packages/{rule_id}/approve", headers=csrf)
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert approved.json()["approved_at"]

    # approved versions are immutable: no PUT
    no_update = client.put(
        f"/api/v1/admin/rule-packages/{rule_id}",
        headers=csrf,
        json={
            "name": "篡改",
            "effective_from": "2026-01-01",
            "params": {"threshold_pct": "30", "overdue_days": 90},
            "legal_basis": "x",
            "reviewer": "y",
            "reviewed_at": "2026-06-01",
        },
    )
    assert no_update.status_code == 409

    # a second approve is rejected
    assert (
        client.post(f"/api/v1/admin/rule-packages/{rule_id}/approve", headers=csrf).status_code
        == 409
    )

    # copy-to-change creates a new draft version
    copied = client.post(f"/api/v1/admin/rule-packages/{rule_id}/copy", headers=csrf)
    assert copied.status_code == 201
    assert copied.json()["code"] == "CORP-EFFECTIVE-COST-2026"
    assert copied.json()["version"] == 2
    assert copied.json()["status"] == "draft"
    edited = client.put(
        f"/api/v1/admin/rule-packages/{copied.json()['id']}",
        headers=csrf,
        json={
            "name": "企业综合年化成本红线（2027 修订）",
            "effective_from": "2026-01-01",
            "params": {"threshold_pct": "30", "overdue_days": 90},
            "legal_basis": "机构法务批准的内部风险政策（2027 修订）",
            "reviewer": "法务张三",
            "reviewed_at": "2026-06-01",
        },
    )
    assert edited.status_code == 200
    assert edited.json()["name"] == "企业综合年化成本红线（2027 修订）"
    assert edited.json()["params"]["threshold_pct"] == "30"

    retired = client.post(f"/api/v1/admin/rule-packages/{rule_id}/retire", headers=csrf)
    assert retired.status_code == 200
    assert retired.json()["status"] == "retired"
    assert (
        client.post(
            f"/api/v1/admin/rule-packages/{copied.json()['id']}/retire", headers=csrf
        ).status_code
        == 409
    )


def test_rule_content_hash_is_stable_across_lifecycle(client: TestClient) -> None:
    """The content hash fingerprints rule content, not lifecycle status."""
    from app.redline import rule_content_hash, rule_content_payload

    csrf = login(client, "admin", "administrator password")
    created = client.post(
        "/api/v1/admin/rule-packages", headers=csrf, json=RULE_PAYLOAD
    )
    assert created.status_code == 201
    draft_hash = created.json()["content_hash"]
    approved = client.post(
        f"/api/v1/admin/rule-packages/{created.json()['id']}/approve", headers=csrf
    )
    assert approved.status_code == 200
    assert approved.json()["content_hash"] == draft_hash
    with client.app.state.database.session() as db:
        from app.models import RulePackage

        rule = db.get(RulePackage, created.json()["id"])
        assert rule_content_hash(rule_content_payload(rule)) == draft_hash


def test_admin_cannot_enter_arbitrary_formulas(client: TestClient) -> None:
    csrf = login(client, "admin", "administrator password")
    payload = dict(RULE_PAYLOAD)
    payload["code"] = "BAD-CALC"
    payload["calc_type"] = "arbitrary_formula"
    payload["params"] = {"expression": "rate * 2 + 1"}
    assert client.post("/api/v1/admin/rule-packages", headers=csrf, json=payload).status_code == 422
    payload = dict(RULE_PAYLOAD)
    payload["code"] = "BAD-PARAMS"
    payload["params"] = {"threshold_pct": "-1", "overdue_days": 90}
    assert client.post("/api/v1/admin/rule-packages", headers=csrf, json=payload).status_code == 422
    payload = dict(RULE_PAYLOAD)
    payload["code"] = "BAD-INTERVAL"
    payload["effective_from"] = "2026-06-01"
    payload["effective_until"] = "2026-01-01"
    assert client.post("/api/v1/admin/rule-packages", headers=csrf, json=payload).status_code == 422


def test_approval_rejects_overlapping_hard_rules(client: TestClient) -> None:
    csrf = login(client, "admin", "administrator password")
    created = client.post("/api/v1/admin/rule-packages", headers=csrf, json=RULE_PAYLOAD)
    assert created.status_code == 201
    assert (
        client.post(
            f"/api/v1/admin/rule-packages/{created.json()['id']}/approve", headers=csrf
        ).status_code
        == 200
    )
    # same scope with an overlapping interval must be rejected at approval
    overlap = client.post(
        "/api/v1/admin/rule-packages",
        headers=csrf,
        json={
            **RULE_PAYLOAD,
            "code": "CORP-EFFECTIVE-COST-OTHER",
            "effective_from": "2026-06-01",
            "effective_until": "2027-12-31",
        },
    )
    assert overlap.status_code == 201
    rejected = client.post(
        f"/api/v1/admin/rule-packages/{overlap.json()['id']}/approve", headers=csrf
    )
    assert rejected.status_code == 409
    assert "ambiguous" in rejected.json()["detail"]


def test_officer_cannot_manage_rules_or_lpr(client: TestClient) -> None:
    csrf = login(client, "owner", "approval officer password")
    assert client.get("/api/v1/admin/rule-packages").status_code == 403
    assert (
        client.post("/api/v1/admin/rule-packages", headers=csrf, json=RULE_PAYLOAD).status_code
        == 403
    )
    assert client.get("/api/v1/admin/lpr-imports").status_code == 403


# ---------------------------------------------------------------------------
# LPR CSV import / publish
# ---------------------------------------------------------------------------

LPR_CSV = """effective_date,tenor,value,publication_date,source_url
2026-05-25,1Y,3.15,2026-05-20,https://example.com/lpr
2026-06-20,1Y,3.10,2026-06-15,https://example.com/lpr
2026-06-20,5Y,3.60,2026-06-15,https://example.com/lpr
"""


def import_lpr(client: TestClient, csrf: dict[str, str], content: str = LPR_CSV) -> dict:
    response = client.post(
        "/api/v1/admin/lpr-imports",
        headers=csrf,
        data={"source_authority": "全国银行间同业拆借中心"},
        files={"file": ("lpr.csv", content.encode("utf-8"), "text/csv")},
    )
    assert response.status_code == 201
    return response.json()


def test_lpr_import_validates_and_publishes(client: TestClient) -> None:
    csrf = login(client, "admin", "administrator password")
    imported = import_lpr(client, csrf)
    assert imported["status"] == "draft"
    assert imported["row_count"] == 3
    assert imported["entries"][0]["effective_date"] == "2026-05-25"
    assert imported["entries"][0]["value"] == "3.15"

    published = client.post(f"/api/v1/admin/lpr-imports/{imported['id']}/publish", headers=csrf)
    assert published.status_code == 200
    assert published.json()["status"] == "published"
    assert published.json()["published_at"]
    # a second publish is rejected
    assert (
        client.post(f"/api/v1/admin/lpr-imports/{imported['id']}/publish", headers=csrf).status_code
        == 409
    )


def test_lpr_import_rejects_bad_csv(client: TestClient) -> None:
    csrf = login(client, "admin", "administrator password")
    bad_cases = [
        ("missing headers", "a,b,c\n1,2,3\n"),
        (
            "bad tenor",
            "effective_date,tenor,value,publication_date,source_url\n2026-01-01,2Y,3.1,2026-01-01,https://x\n",
        ),
        (
            "non numeric",
            "effective_date,tenor,value,publication_date,source_url\n2026-01-01,1Y,abc,2026-01-01,https://x\n",
        ),
        (
            "negative",
            "effective_date,tenor,value,publication_date,source_url\n2026-01-01,1Y,-1,2026-01-01,https://x\n",
        ),
        (
            "future publication",
            "effective_date,tenor,value,publication_date,source_url\n2026-01-01,1Y,3.1,2026-02-01,https://x\n",
        ),
        (
            "bad date",
            "effective_date,tenor,value,publication_date,source_url\n2026-13-40,1Y,3.1,2026-01-01,https://x\n",
        ),
        (
            "duplicate",
            "effective_date,tenor,value,publication_date,source_url\n2026-01-01,1Y,3.1,2026-01-01,https://x\n2026-01-01,1Y,3.0,2026-01-01,https://x\n",
        ),
    ]
    for label, content in bad_cases:
        response = client.post(
            "/api/v1/admin/lpr-imports",
            headers=csrf,
            data={"source_authority": "x"},
            files={"file": ("bad.csv", content.encode("utf-8"), "text/csv")},
        )
        assert response.status_code == 422, label


# ---------------------------------------------------------------------------
# Rule context confirmation and live preview
# ---------------------------------------------------------------------------


def test_redline_preview_without_context_is_indeterminate(client: TestClient) -> None:
    login(client, "owner", "approval officer password")
    application_id = owner_application_id(client)
    preview = client.get(f"/api/v1/applications/{application_id}/redline")
    assert preview.status_code == 200
    body = preview.json()
    assert body["rule_context"] is None
    assert body["selection"]["reason"] == "no_rule_context"
    assert body["selection"]["rule"] is None
    assert body["state"] == "indeterminate"
    assert body["critical"]["missing"] == []
    assert body["formal_run_blocked_reason"] is None


def test_redline_preview_unique_rule_with_missing_inputs(client: TestClient) -> None:
    csrf = login(client, "owner", "approval officer password")
    application_id = owner_application_id(client)
    confirm_context(client, csrf, application_id)
    preview = client.get(f"/api/v1/applications/{application_id}/redline").json()
    assert preview["selection"]["reason"] == "unique"
    assert preview["selection"]["rule"]["code"] == "DEMO-EFFECTIVE-COST-36"
    assert preview["state"] == "insufficient_data"
    assert set(preview["critical"]["missing"]) == {
        "loan_amount",
        "loan_term",
        "interest_rate",
        "repayment_method",
        "loan_fees",
        "overdue_interest_rate",
    }
    # demo LPR exists and is not provisional because proposed_signing_date is set
    assert preview["lpr"]["value"]
    assert preview["lpr"]["provisional"] is False
    # demo references are shown separately
    assert {ref["rule"]["code"] for ref in preview["references"]} == {
        "DEMO-LPR-4X",
        "DEMO-RATE-24",
    }


def test_redline_preview_lpr_provisional_without_signing_date(client: TestClient) -> None:
    csrf = login(client, "owner", "approval officer password")
    with client.app.state.database.session() as db:
        application = db.query(Application).filter_by(borrower_name="owner企业").one()
        application.proposed_signing_date = None
        db.commit()
        application_id = application.id
    confirm_context(client, csrf, application_id)
    preview = client.get(f"/api/v1/applications/{application_id}/redline").json()
    assert preview["lpr"]["value"]
    assert preview["lpr"]["provisional"] is True


def test_rule_context_confirmation_is_upsert_and_owner_scoped(client: TestClient) -> None:
    csrf = login(client, "owner", "approval officer password")
    application_id = owner_application_id(client)
    first = client.post(
        f"/api/v1/applications/{application_id}/rule-context",
        headers={**csrf, "Idempotency-Key": "ctx-1"},
        json={"context": "全国"},
    )
    assert first.status_code == 201
    second = client.post(
        f"/api/v1/applications/{application_id}/rule-context",
        headers={**csrf, "Idempotency-Key": "ctx-2"},
        json={"context": "某省"},
    )
    assert second.status_code == 201
    assert second.json()["context"] == "某省"
    preview = client.get(f"/api/v1/applications/{application_id}/redline").json()
    assert preview["rule_context"] == "某省"
    assert preview["selection"]["reason"] == "no_match"
    # replay with the same key and payload deduplicates; the resource keeps its
    # current (upserted) state, matching the existing confirmation semantics
    replay = client.post(
        f"/api/v1/applications/{application_id}/rule-context",
        headers={**csrf, "Idempotency-Key": "ctx-1"},
        json={"context": "全国"},
    )
    assert replay.status_code == 200
    assert replay.json()["context"] == "某省"


def test_redline_preview_is_owner_scoped(client: TestClient) -> None:
    login(client, "other", "approval officer password")
    owner_id = owner_application_id(client)
    other_id = owner_application_id(client, "other企业")
    assert client.get(f"/api/v1/applications/{owner_id}/redline").status_code == 404
    assert client.get(f"/api/v1/applications/{other_id}/redline").status_code == 200
    admin_csrf = login(client, "admin", "administrator password")
    assert (
        client.post(
            f"/api/v1/applications/{other_id}/rule-context",
            headers={**admin_csrf, "Idempotency-Key": "admin-ctx"},
            json={"context": "全国"},
        ).status_code
        == 404
    )


# ---------------------------------------------------------------------------
# Formal runs
# ---------------------------------------------------------------------------


def test_formal_redline_run_with_all_critical_inputs(client: TestClient) -> None:
    csrf = login(client, "owner", "approval officer password")
    application_id = owner_application_id(client)
    confirm_context(client, csrf, application_id)
    confirm_all_critical_inputs(client, csrf, application_id)

    run = client.post(
        f"/api/v1/applications/{application_id}/redline-runs",
        headers={**csrf, "Idempotency-Key": "redline-1"},
    )
    assert run.status_code == 201
    body = run.json()
    assert body["content_hash"]
    assert body["rule_snapshot"]["code"] == "DEMO-EFFECTIVE-COST-36"
    assert body["result_snapshot"]["state"] == "not_triggered"
    assert body["result_snapshot"]["primary"]["metrics"]["effective_cost_pct"] != ""
    assert body["stale"] is False
    assert body["input_snapshot"]["rule_context"] == "全国"
    assert body["input_snapshot"]["lpr"]["provisional"] is False

    # steps expose the full calculation
    step_labels = [step["label"] for step in body["result_snapshot"]["primary"]["steps"]]
    assert "实际可用本金" in step_labels
    assert "正常履约综合年化成本" in step_labels
    assert "逾期情景（独立计算）" in step_labels
    # references are present and distinct from the hard-rule outcome
    assert body["result_snapshot"]["references"][0]["rule"]["kind"] == "reference"

    # idempotent replay returns the same run
    replay = client.post(
        f"/api/v1/applications/{application_id}/redline-runs",
        headers={**csrf, "Idempotency-Key": "redline-1"},
    )
    assert replay.status_code == 200
    assert replay.json()["id"] == body["id"]


def test_formal_run_without_critical_inputs_is_insufficient(client: TestClient) -> None:
    csrf = login(client, "owner", "approval officer password")
    application_id = owner_application_id(client)
    confirm_context(client, csrf, application_id)
    run = client.post(
        f"/api/v1/applications/{application_id}/redline-runs",
        headers={**csrf, "Idempotency-Key": "redline-empty"},
    )
    assert run.status_code == 201
    body = run.json()
    assert body["result_snapshot"]["state"] == "insufficient_data"
    assert body["result_snapshot"]["primary"]["missing_inputs"]
    assert "not_triggered" not in body["result_snapshot"]["state"]


def test_formal_run_without_rule_context_is_indeterminate(client: TestClient) -> None:
    csrf = login(client, "owner", "approval officer password")
    application_id = owner_application_id(client)
    run = client.post(
        f"/api/v1/applications/{application_id}/redline-runs",
        headers={**csrf, "Idempotency-Key": "redline-nocontext"},
    )
    assert run.status_code == 201
    body = run.json()
    assert body["result_snapshot"]["state"] == "indeterminate"
    assert body["result_snapshot"]["selection"]["reason"] == "no_rule_context"
    assert body["rule_snapshot"] is None
    assert body["rule_id"] is None


def test_redline_snapshot_is_immutable_and_history_preserved(client: TestClient) -> None:
    csrf = login(client, "owner", "approval officer password")
    application_id = owner_application_id(client)
    confirm_context(client, csrf, application_id)
    run = client.post(
        f"/api/v1/applications/{application_id}/redline-runs",
        headers={**csrf, "Idempotency-Key": "r1"},
    )
    assert run.status_code == 201
    run_id = run.json()["id"]
    first = run.json()

    # a resolution change marks the run stale and a rerun preserves history
    confirm_all_critical_inputs(client, csrf, application_id)
    runs = client.get(f"/api/v1/applications/{application_id}/redline-runs").json()
    assert runs[0]["id"] == run_id
    assert runs[0]["stale"] is True
    assert runs[0]["stale_reason"] == "critical_input_change"

    rerun = client.post(
        f"/api/v1/applications/{application_id}/redline-runs",
        headers={**csrf, "Idempotency-Key": "r2"},
    )
    assert rerun.status_code == 201
    assert rerun.json()["id"] != run_id
    runs = client.get(f"/api/v1/applications/{application_id}/redline-runs").json()
    assert {item["id"] for item in runs} == {run_id, rerun.json()["id"]}
    assert runs[0]["stale"] is False

    # the frozen snapshots and hash never change
    detail = client.get(f"/api/v1/applications/{application_id}/redline-runs/{run_id}").json()
    assert detail["result_snapshot"] == first["result_snapshot"]
    assert detail["content_hash"] == first["content_hash"]
    assert detail["stale"] is True


def test_rule_context_change_marks_run_stale(client: TestClient) -> None:
    csrf = login(client, "owner", "approval officer password")
    application_id = owner_application_id(client)
    confirm_context(client, csrf, application_id, "全国")
    run = client.post(
        f"/api/v1/applications/{application_id}/redline-runs",
        headers={**csrf, "Idempotency-Key": "ctx-run"},
    )
    assert run.status_code == 201
    client.post(
        f"/api/v1/applications/{application_id}/rule-context",
        headers={**csrf, "Idempotency-Key": "ctx-2"},
        json={"context": "某省"},
    )
    runs = client.get(f"/api/v1/applications/{application_id}/redline-runs").json()
    assert runs[0]["stale"] is True
    assert runs[0]["stale_reason"] == "rule_context_change"


def test_new_applicable_rule_marks_run_stale(client: TestClient) -> None:
    csrf = login(client, "owner", "approval officer password")
    application_id = owner_application_id(client)
    confirm_context(client, csrf, application_id)
    run = client.post(
        f"/api/v1/applications/{application_id}/redline-runs",
        headers={**csrf, "Idempotency-Key": "rule-run"},
    )
    assert run.status_code == 201
    run_id = run.json()["id"]
    assert run.json()["result_snapshot"]["selection"]["reason"] == "unique"

    # retire the demo rule and approve a replacement with the same scope
    admin_csrf = login(client, "admin", "administrator password")
    with client.app.state.database.session() as db:
        demo = (
            db.query(RulePackage)
            .filter_by(code="DEMO-EFFECTIVE-COST-36", status=RuleStatus.APPROVED)
            .one()
        )
        demo_id = demo.id
        db.commit()
    assert (
        client.post(f"/api/v1/admin/rule-packages/{demo_id}/retire", headers=admin_csrf).status_code
        == 200
    )
    replacement = client.post(
        "/api/v1/admin/rule-packages",
        headers=admin_csrf,
        json={
            **RULE_PAYLOAD,
            "code": "REPLACEMENT-COST",
            "rule_context": "全国",
            "product": "经营贷",
            "effective_from": "2024-01-01",
        },
    )
    assert replacement.status_code == 201
    assert (
        client.post(
            f"/api/v1/admin/rule-packages/{replacement.json()['id']}/approve",
            headers=admin_csrf,
        ).status_code
        == 200
    )
    # back on the owner session for the read
    login(client, "owner", "approval officer password")
    runs = client.get(f"/api/v1/applications/{application_id}/redline-runs").json()
    assert runs[0]["id"] == run_id
    assert runs[0]["stale"] is True
    assert runs[0]["stale_reason"] == "rule_changed"


def test_lpr_change_marks_lpr_dependent_run_stale(client: TestClient) -> None:
    admin_csrf = login(client, "admin", "administrator password")
    created = client.post(
        "/api/v1/admin/rule-packages",
        headers=admin_csrf,
        json={
            **RULE_PAYLOAD,
            "code": "LPR-LIMIT-RULE",
            "calc_type": "lpr_multiple_limit",
            "params": {"multiplier": "4"},
        },
    )
    assert created.status_code == 201
    assert (
        client.post(
            f"/api/v1/admin/rule-packages/{created.json()['id']}/approve",
            headers=admin_csrf,
        ).status_code
        == 200
    )

    csrf = login(client, "owner", "approval officer password")
    application_id = owner_application_id(client)
    # context/product without the demo hard rule; proposed signing date fixed so
    # the run's LPR as-of date precedes the newer imported batch
    confirm_context(client, csrf, application_id, "某省")
    with client.app.state.database.session() as db:
        application = db.get(Application, application_id)
        application.product = "流动资金贷"
        application.proposed_signing_date = date(2026, 6, 10)
        db.commit()
    resolution = client.post(
        f"/api/v1/applications/{application_id}/resolutions",
        headers={**csrf, "Idempotency-Key": "res-rate"},
        json={
            "resolution_type": "manual",
            "field_key": "interest_rate",
            "value": "12%",
            "reason": "年利率 12%",
        },
    )
    assert resolution.status_code == 201
    run = client.post(
        f"/api/v1/applications/{application_id}/redline-runs",
        headers={**csrf, "Idempotency-Key": "lpr-run"},
    )
    assert run.status_code == 201
    assert run.json()["result_snapshot"]["state"] == "not_triggered"
    run_lpr_id = run.json()["input_snapshot"]["lpr"]["entry_id"]
    assert run_lpr_id

    # publish a newer LPR batch whose effective date still precedes the as-of
    # date so the selection changes
    admin_csrf = login(client, "admin", "administrator password")
    newer_csv = (
        "effective_date,tenor,value,publication_date,source_url\n"
        "2026-06-01,1Y,2.90,2026-05-28,https://example.com/lpr\n"
    )
    imported = import_lpr(client, admin_csrf, newer_csv)
    assert (
        client.post(
            f"/api/v1/admin/lpr-imports/{imported['id']}/publish", headers=admin_csrf
        ).status_code
        == 200
    )
    # back on the owner session for the read
    login(client, "owner", "approval officer password")
    runs = client.get(f"/api/v1/applications/{application_id}/redline-runs").json()
    assert runs[0]["stale"] is True
    assert runs[0]["stale_reason"] == "lpr_changed"


def test_printable_redline_report(client: TestClient) -> None:
    csrf = login(client, "owner", "approval officer password")
    application_id = owner_application_id(client)
    confirm_context(client, csrf, application_id)
    run = client.post(
        f"/api/v1/applications/{application_id}/redline-runs",
        headers={**csrf, "Idempotency-Key": "print-run"},
    )
    assert run.status_code == 201
    printable = client.get(
        f"/api/v1/applications/{application_id}/redline-runs/{run.json()['id']}/printable"
    )
    assert printable.status_code == 200
    html = printable.text
    assert "仅供审批辅助，需人工复核" in html
    assert "系统不认定" in html
    assert "DEMO-EFFECTIVE-COST-36" in html
    assert "司法风险参考线" in html
    assert run.json()["content_hash"] in html
    assert "法律依据" in html


# ---------------------------------------------------------------------------
# Production gate and auth boundaries
# ---------------------------------------------------------------------------


def test_production_rejects_demo_rule_for_formal_report() -> None:
    client = setup(production=True)
    with client:
        csrf = login(client, "admin", "administrator password")
        demo = client.post(
            "/api/v1/admin/rule-packages",
            headers=csrf,
            json={**RULE_PAYLOAD, "code": "DEMO-TEST-RULE", "demo_only": True},
        )
        assert demo.status_code == 201
        assert (
            client.post(
                f"/api/v1/admin/rule-packages/{demo.json()['id']}/approve", headers=csrf
            ).status_code
            == 200
        )
        owner_csrf = login(client, "owner", "approval officer password")
        application_id = owner_application_id(client)
        with client.app.state.database.session() as db:
            application = db.get(Application, application_id)
            application.product = "流动资金贷"
            db.commit()
        confirm_context(client, owner_csrf, application_id, "某省")
        preview = client.get(f"/api/v1/applications/{application_id}/redline").json()
        assert preview["formal_run_blocked_reason"] is not None
        run = client.post(
            f"/api/v1/applications/{application_id}/redline-runs",
            headers={**owner_csrf, "Idempotency-Key": "prod-demo"},
        )
        assert run.status_code == 422
        assert "Production mode rejects demo rules" in run.json()["detail"]


def test_production_allows_non_demo_rule_formal_report() -> None:
    client = setup(production=True)
    with client:
        csrf = login(client, "admin", "administrator password")
        created = client.post("/api/v1/admin/rule-packages", headers=csrf, json=RULE_PAYLOAD)
        assert created.status_code == 201
        assert (
            client.post(
                f"/api/v1/admin/rule-packages/{created.json()['id']}/approve", headers=csrf
            ).status_code
            == 200
        )
        imported = import_lpr(client, csrf)
        assert (
            client.post(
                f"/api/v1/admin/lpr-imports/{imported['id']}/publish", headers=csrf
            ).status_code
            == 200
        )
        owner_csrf = login(client, "owner", "approval officer password")
        application_id = owner_application_id(client)
        with client.app.state.database.session() as db:
            application = db.get(Application, application_id)
            application.product = "流动资金贷"
            db.commit()
        confirm_context(client, owner_csrf, application_id, "某省")
        resolution = client.post(
            f"/api/v1/applications/{application_id}/resolutions",
            headers={**owner_csrf, "Idempotency-Key": "prod-res"},
            json={
                "resolution_type": "manual",
                "field_key": "interest_rate",
                "value": "12%",
                "reason": "年利率 12%",
            },
        )
        assert resolution.status_code == 201
        run = client.post(
            f"/api/v1/applications/{application_id}/redline-runs",
            headers={**owner_csrf, "Idempotency-Key": "prod-run"},
        )
        assert run.status_code == 201
        # the demo rule is not selectable and the production rule is annual-rate
        # based, so the report records not triggered
        assert run.json()["rule_snapshot"]["code"] == "CORP-EFFECTIVE-COST-2026"


def test_redline_is_owner_scoped(client: TestClient) -> None:
    login(client, "other", "approval officer password")
    owner_id = owner_application_id(client)
    other_id = owner_application_id(client, "other企业")
    assert client.get(f"/api/v1/applications/{owner_id}/redline-runs").status_code == 404
    assert client.get(f"/api/v1/applications/{owner_id}/redline-runs/x").status_code == 404
    assert client.get(f"/api/v1/applications/{other_id}/redline-runs").status_code == 200
    admin_csrf = login(client, "admin", "administrator password")
    assert (
        client.post(
            f"/api/v1/applications/{other_id}/redline-runs",
            headers={**admin_csrf, "Idempotency-Key": "admin-run"},
        ).status_code
        == 404
    )


# ---------------------------------------------------------------------------
# Review-driven gate and staleness refinements
# ---------------------------------------------------------------------------


def test_production_rejects_demo_reference_in_formal_report() -> None:
    """A non-demo primary rule with a demo reference line is still gated."""
    client = setup(production=True)
    with client:
        csrf = login(client, "admin", "administrator password")
        primary = client.post(
            "/api/v1/admin/rule-packages", headers=csrf, json=RULE_PAYLOAD
        )
        assert primary.status_code == 201
        assert (
            client.post(
                f"/api/v1/admin/rule-packages/{primary.json()['id']}/approve",
                headers=csrf,
            ).status_code
            == 200
        )
        reference = client.post(
            "/api/v1/admin/rule-packages",
            headers=csrf,
            json={**RULE_PAYLOAD, "code": "DEMO-REF", "kind": "reference", "demo_only": True},
        )
        assert reference.status_code == 201
        assert (
            client.post(
                f"/api/v1/admin/rule-packages/{reference.json()['id']}/approve",
                headers=csrf,
            ).status_code
            == 200
        )
        owner_csrf = login(client, "owner", "approval officer password")
        application_id = owner_application_id(client)
        with client.app.state.database.session() as db:
            application = db.get(Application, application_id)
            application.product = "流动资金贷"
            db.commit()
        confirm_context(client, owner_csrf, application_id, "某省")
        preview = client.get(f"/api/v1/applications/{application_id}/redline").json()
        assert preview["formal_run_blocked_reason"] is not None
        run = client.post(
            f"/api/v1/applications/{application_id}/redline-runs",
            headers={**owner_csrf, "Idempotency-Key": "prod-ref-demo"},
        )
        assert run.status_code == 422


def test_production_never_consumes_demo_lpr(monkeypatch) -> None:
    """A promoted dev database's demo LPR must not feed production reports."""
    from app.config import settings as app_settings

    monkeypatch.setattr(app_settings, "production", True)
    from app.models import LprEntry, LprImport, LprImportStatus

    client = setup(production=True)
    with client:
        admin_csrf = login(client, "admin", "administrator password")
        created = client.post(
            "/api/v1/admin/rule-packages",
            headers=admin_csrf,
            json={
                **RULE_PAYLOAD,
                "code": "PROD-LPR-RULE",
                "calc_type": "lpr_multiple_limit",
                "params": {"multiplier": "4"},
            },
        )
        assert created.status_code == 201
        assert (
            client.post(
                f"/api/v1/admin/rule-packages/{created.json()['id']}/approve",
                headers=admin_csrf,
            ).status_code
            == 200
        )
        # simulate a promoted dev database: a published demo-only LPR import
        with client.app.state.database.session() as db:
            batch = LprImport(
                filename="dev-demo-lpr.csv",
                source_authority="演示数据（合成）",
                status=LprImportStatus.PUBLISHED,
                demo_only=True,
                row_count=1,
                actor_id=None,
            )
            batch.entries.append(
                LprEntry(
                    effective_date=date(2024, 1, 20),
                    tenor="1Y",
                    value="3.00",
                    publication_date=date(2024, 1, 20),
                    source_url="demo://x",
                )
            )
            db.add(batch)
            db.commit()
        owner_csrf = login(client, "owner", "approval officer password")
        application_id = owner_application_id(client)
        confirm_context(client, owner_csrf, application_id, "某省")
        with client.app.state.database.session() as db:
            application = db.get(Application, application_id)
            application.product = "流动资金贷"
            db.commit()
        client.post(
            f"/api/v1/applications/{application_id}/resolutions",
            headers={**owner_csrf, "Idempotency-Key": "prod-lpr-res"},
            json={
                "resolution_type": "manual",
                "field_key": "interest_rate",
                "value": "12%",
                "reason": "年利率 12%",
            },
        )
        preview = client.get(f"/api/v1/applications/{application_id}/redline").json()
        assert preview["lpr"]["value"] is None  # demo LPR excluded
        run = client.post(
            f"/api/v1/applications/{application_id}/redline-runs",
            headers={**owner_csrf, "Idempotency-Key": "prod-lpr-run"},
        )
        assert run.status_code == 201
        assert run.json()["result_snapshot"]["state"] == "insufficient_data"
        assert run.json()["result_snapshot"]["primary"]["missing_inputs"] == ["lpr"]


def test_non_primary_subject_resolution_never_feeds_redline(client: TestClient) -> None:
    csrf = login(client, "owner", "approval officer password")
    application_id = owner_application_id(client)
    confirm_context(client, csrf, application_id)
    resolution = client.post(
        f"/api/v1/applications/{application_id}/resolutions",
        headers={**csrf, "Idempotency-Key": "res-wrong-subject"},
        json={
            "resolution_type": "manual",
            "field_key": "interest_rate",
            "subject_role": "guarantor",
            "value": "12%",
            "reason": "保证人视角的利率（不应参与红线计算）",
        },
    )
    assert resolution.status_code == 201
    run = client.post(
        f"/api/v1/applications/{application_id}/redline-runs",
        headers={**csrf, "Idempotency-Key": "wrong-subject-run"},
    )
    assert run.status_code == 201
    body = run.json()
    assert body["result_snapshot"]["state"] == "insufficient_data"
    assert "interest_rate" in body["result_snapshot"]["primary"]["missing_inputs"]


def test_lpr_change_stales_run_when_only_reference_uses_lpr(client: TestClient) -> None:
    admin_csrf = login(client, "admin", "administrator password")
    primary = client.post(
        "/api/v1/admin/rule-packages",
        headers=admin_csrf,
        json={
            **RULE_PAYLOAD,
            "code": "RATE-LIMIT-RULE",
            "calc_type": "annual_rate_limit",
            "params": {"threshold_pct": "24"},
        },
    )
    assert primary.status_code == 201
    assert (
        client.post(
            f"/api/v1/admin/rule-packages/{primary.json()['id']}/approve", headers=admin_csrf
        ).status_code
        == 200
    )
    reference = client.post(
        "/api/v1/admin/rule-packages",
        headers=admin_csrf,
        json={
            **RULE_PAYLOAD,
            "code": "REF-LPR-RULE",
            "kind": "reference",
            "calc_type": "lpr_multiple_limit",
            "params": {"multiplier": "4"},
        },
    )
    assert reference.status_code == 201
    assert (
        client.post(
            f"/api/v1/admin/rule-packages/{reference.json()['id']}/approve",
            headers=admin_csrf,
        ).status_code
        == 200
    )

    csrf = login(client, "owner", "approval officer password")
    application_id = owner_application_id(client)
    confirm_context(client, csrf, application_id, "某省")
    with client.app.state.database.session() as db:
        application = db.get(Application, application_id)
        application.product = "流动资金贷"
        application.proposed_signing_date = date(2026, 6, 10)
        db.commit()
    client.post(
        f"/api/v1/applications/{application_id}/resolutions",
        headers={**csrf, "Idempotency-Key": "ref-rate-res"},
        json={
            "resolution_type": "manual",
            "field_key": "interest_rate",
            "value": "12%",
            "reason": "年利率 12%",
        },
    )
    run = client.post(
        f"/api/v1/applications/{application_id}/redline-runs",
        headers={**csrf, "Idempotency-Key": "ref-lpr-run"},
    )
    assert run.status_code == 201
    assert run.json()["result_snapshot"]["primary"]["state"] == "not_triggered"
    assert run.json()["result_snapshot"]["references"][0]["rule"]["code"] == "REF-LPR-RULE"

    newer_csv = (
        "effective_date,tenor,value,publication_date,source_url\n"
        "2026-06-01,1Y,2.90,2026-05-28,https://example.com/lpr\n"
    )
    admin_csrf = login(client, "admin", "administrator password")
    imported = import_lpr(client, admin_csrf, newer_csv)
    assert (
        client.post(
            f"/api/v1/admin/lpr-imports/{imported['id']}/publish", headers=admin_csrf
        ).status_code
        == 200
    )
    login(client, "owner", "approval officer password")
    runs = client.get(f"/api/v1/applications/{application_id}/redline-runs").json()
    assert runs[0]["stale"] is True
    assert runs[0]["stale_reason"] == "lpr_changed"


def test_printable_report_marks_stale_runs(client: TestClient) -> None:
    csrf = login(client, "owner", "approval officer password")
    application_id = owner_application_id(client)
    confirm_context(client, csrf, application_id)
    run = client.post(
        f"/api/v1/applications/{application_id}/redline-runs",
        headers={**csrf, "Idempotency-Key": "stale-print"},
    )
    assert run.status_code == 201
    run_id = run.json()["id"]
    # a rule-context change marks the run stale
    client.post(
        f"/api/v1/applications/{application_id}/rule-context",
        headers={**csrf, "Idempotency-Key": "ctx-change"},
        json={"context": "某省"},
    )
    printable = client.get(
        f"/api/v1/applications/{application_id}/redline-runs/{run_id}/printable"
    )
    assert printable.status_code == 200
    assert "已失效" in printable.text
    assert "rule_context_change" in printable.text
