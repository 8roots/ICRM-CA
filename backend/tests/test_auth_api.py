from fastapi.testclient import TestClient

from app.main import create_app
from app.models import Base, User
from app.security import hash_password


def make_client() -> TestClient:
    app = create_app("sqlite+pysqlite:///:memory:", cookie_secure=True)
    Base.metadata.create_all(app.state.database.engine)
    with app.state.database.session() as db:
        db.add(
            User(
                username="officer",
                password_hash=hash_password("correct horse battery staple"),
                role="approval_officer",
                enabled=True,
            )
        )
        db.commit()
    return TestClient(app, base_url="https://testserver")


def test_login_current_user_and_logout_require_csrf() -> None:
    with make_client() as client:
        response = client.post(
            "/api/v1/auth/login",
            headers={"Origin": "https://testserver"},
            json={"username": "officer", "password": "correct horse battery staple"},
        )
        assert response.status_code == 204
        cookies = response.headers.get_list("set-cookie")
        session_cookie = next(cookie for cookie in cookies if cookie.startswith("icrm_session="))
        csrf_cookie = next(cookie for cookie in cookies if cookie.startswith("icrm_csrf="))
        assert "HttpOnly" in session_cookie
        assert "HttpOnly" not in csrf_cookie
        for cookie in cookies:
            assert "Secure" in cookie
            assert "SameSite=strict" in cookie

        me = client.get("/api/v1/auth/me")
        assert me.status_code == 200
        assert me.json() == {
            "id": me.json()["id"],
            "username": "officer",
            "role": "approval_officer",
        }

        assert client.post("/api/v1/auth/logout").status_code == 403
        csrf = client.cookies["icrm_csrf"]
        assert client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 204
        assert client.get("/api/v1/auth/me").status_code == 401


def test_login_rejects_bad_password_and_disabled_user() -> None:
    with make_client() as client:
        assert (
            client.post(
                "/api/v1/auth/login",
                headers={"Origin": "https://testserver"},
                json={"username": "officer", "password": "wrong password"},
            ).status_code
            == 401
        )

        with client.app.state.database.session() as db:
            user = db.query(User).filter_by(username="officer").one()
            user.enabled = False
            db.commit()

        assert (
            client.post(
                "/api/v1/auth/login",
                headers={"Origin": "https://testserver"},
                json={"username": "officer", "password": "correct horse battery staple"},
            ).status_code
            == 401
        )


def test_login_rejects_cross_origin_request() -> None:
    with make_client() as client:
        response = client.post(
            "/api/v1/auth/login",
            headers={"Origin": "https://attacker.example"},
            json={"username": "officer", "password": "correct horse battery staple"},
        )
        assert response.status_code == 403
        assert "icrm_session" not in client.cookies
