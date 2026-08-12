from fastapi.testclient import TestClient

from app.main import create_app
from app.models import Base, User
from app.security import hash_password


def login(client: TestClient, username: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        headers={"Origin": "https://testserver"},
        json={"username": username, "password": "approval officer password"},
    )
    assert response.status_code == 204
    return {"X-CSRF-Token": client.cookies["icrm_csrf"]}


def test_officer_creates_corporate_and_individual_applications_idempotently() -> None:
    app = create_app("sqlite+pysqlite:///:memory:", cookie_secure=True)
    Base.metadata.create_all(app.state.database.engine)
    with app.state.database.session() as db:
        for username in ("officer-one", "officer-two"):
            db.add(
                User(
                    username=username,
                    password_hash=hash_password("approval officer password"),
                    role="approval_officer",
                    enabled=True,
                )
            )
        db.commit()

    corporate = {
        "primary_borrower": {"type": "corporate", "name": "示例企业"},
        "product": "经营贷",
        "application_date": "2026-08-07",
        "proposed_signing_date": "2026-08-20",
    }
    individual = {
        "primary_borrower": {"type": "individual", "name": "示例个人"},
        "product": "个人经营贷",
        "application_date": "2026-08-07",
        "proposed_signing_date": None,
    }

    with TestClient(app, base_url="https://testserver") as owner:
        csrf = login(owner, "officer-one")
        first = owner.post(
            "/api/v1/applications",
            headers={**csrf, "Idempotency-Key": "corporate-1"},
            json=corporate,
        )
        assert first.status_code == 201
        assert first.json()["owner_id"]
        assert first.json()["lifecycle_state"] == "draft"
        assert first.json()["version"] == 1

        replay = owner.post(
            "/api/v1/applications",
            headers={**csrf, "Idempotency-Key": "corporate-1"},
            json=corporate,
        )
        assert replay.status_code == 200
        assert replay.json()["id"] == first.json()["id"]
        assert (
            owner.post(
                "/api/v1/applications",
                headers={**csrf, "Idempotency-Key": "corporate-1"},
                json=individual,
            ).status_code
            == 409
        )

        second = owner.post(
            "/api/v1/applications",
            headers={**csrf, "Idempotency-Key": "individual-1"},
            json=individual,
        )
        assert second.status_code == 201
        assert [
            item["primary_borrower"]["type"] for item in owner.get("/api/v1/applications").json()
        ] == [
            "individual",
            "corporate",
        ]

        missing_borrower = owner.post(
            "/api/v1/applications",
            headers={**csrf, "Idempotency-Key": "invalid-1"},
            json={key: value for key, value in corporate.items() if key != "primary_borrower"},
        )
        assert missing_borrower.status_code == 422

        updated_payload = {**corporate, "product": "流动资金贷", "version": 1}
        updated = owner.put(
            f"/api/v1/applications/{first.json()['id']}", headers=csrf, json=updated_payload
        )
        assert updated.status_code == 200
        assert updated.json()["version"] == 2
        assert (
            owner.put(
                f"/api/v1/applications/{first.json()['id']}", headers=csrf, json=updated_payload
            ).status_code
            == 409
        )

    with TestClient(app, base_url="https://testserver") as other:
        login(other, "officer-two")
        assert other.get("/api/v1/applications").json() == []
        assert other.get(f"/api/v1/applications/{first.json()['id']}").status_code == 404
