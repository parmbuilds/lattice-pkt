import uuid

import psycopg

from tests.conftest import plain_dsn
from tests.helpers import api_client, sign_up_and_in


async def test_sign_in_sets_an_httponly_lax_session_cookie(settings):
    async with api_client(settings) as client:
        email = f"{uuid.uuid4()}@example.test"
        assert (await client.post("/api/auth/signup", json=_creds(email))).status_code == 201
        response = await client.post("/api/auth/signin", json=_creds(email))
    assert response.status_code == 200
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert response.json()["csrf_token"]


async def test_wrong_password_and_unknown_email_get_the_same_response(settings):
    async with api_client(settings) as client:
        email = f"{uuid.uuid4()}@example.test"
        await client.post("/api/auth/signup", json=_creds(email))
        wrong = await client.post(
            "/api/auth/signin", json={"email": email, "password": "not-the-password"}
        )
        unknown = await client.post("/api/auth/signin", json=_creds(f"{uuid.uuid4()}@example.test"))
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


async def test_existing_email_sign_up_looks_like_any_other_rejected_sign_up(settings):
    async with api_client(settings) as client:
        email = f"{uuid.uuid4()}@example.test"
        await client.post("/api/auth/signup", json=_creds(email))
        duplicate = await client.post("/api/auth/signup", json=_creds(email.upper()))
        too_short = await client.post(
            "/api/auth/signup",
            json={"email": f"{uuid.uuid4()}@example.test", "password": "short", "name": "x"},
        )
    assert duplicate.status_code == too_short.status_code == 400
    assert duplicate.json().keys() == too_short.json().keys()


async def test_unsafe_requests_need_the_csrf_token_and_our_origin(settings):
    async with api_client(settings) as client:
        csrf = await sign_up_and_in(client)
        no_token = await client.post("/api/auth/signout")
        foreign = await client.post(
            "/api/auth/signout",
            headers={"X-CSRF-Token": csrf, "Origin": "https://evil.example"},
        )
        no_origin = await client.post(
            "/api/auth/signout", headers={"X-CSRF-Token": csrf, "Origin": ""}
        )
        ok = await client.post("/api/auth/signout", headers={"X-CSRF-Token": csrf})
    assert no_token.status_code == 403
    assert foreign.status_code == 403
    assert no_origin.status_code == 403
    assert ok.status_code == 204


async def test_rest_routes_refuse_bearer_credentials(settings):
    async with api_client(settings) as client:
        await sign_up_and_in(client)
        response = await client.get(
            "/api/auth/me", headers={"Authorization": "Bearer lat_pat_anything"}
        )
    assert response.status_code == 401


async def test_sign_out_invalidates_the_session(settings):
    async with api_client(settings) as client:
        csrf = await sign_up_and_in(client)
        assert (await client.get("/api/auth/me")).status_code == 200
        await client.post("/api/auth/signout", headers={"X-CSRF-Token": csrf})
        assert (await client.get("/api/auth/me")).status_code == 401


async def test_a_role_change_by_another_request_shows_on_the_next_request(settings, pg):
    async with api_client(settings) as client:
        await sign_up_and_in(client)
        me = (await client.get("/api/auth/me")).json()
        with psycopg.connect(plain_dsn(pg["app"])) as conn:
            workspace_id = str(
                conn.execute("INSERT INTO workspaces (name) VALUES ('W') RETURNING id").fetchone()[
                    0
                ]
            )
            conn.execute(
                "INSERT INTO memberships (workspace_id, user_id, role) VALUES (%s, %s, 'member')",
                (workspace_id, me["user"]["id"]),
            )
            conn.commit()
            assert {
                m["role"] for m in (await client.get("/api/auth/me")).json()["memberships"]
            } == {"member"}
            conn.execute(
                "UPDATE memberships SET role = 'admin' WHERE workspace_id = %s AND user_id = %s",
                (workspace_id, me["user"]["id"]),
            )
        roles = {
            m["workspace_id"]: m["role"]
            for m in (await client.get("/api/auth/me")).json()["memberships"]
        }
    assert roles[workspace_id] == "admin"


async def test_sessions_expire_after_their_absolute_and_idle_lifetimes(settings, pg):
    for column in ("expires_at", "last_seen_at"):
        async with api_client(settings) as client:
            await sign_up_and_in(client)
            me = (await client.get("/api/auth/me")).json()
            with psycopg.connect(plain_dsn(pg["app"])) as conn:
                conn.execute(
                    f"UPDATE sessions SET {column} = now() - interval '30 days' "  # noqa: S608
                    "WHERE user_id = %s",
                    (me["user"]["id"],),
                )
            assert (await client.get("/api/auth/me")).status_code == 401, column


async def test_responses_carry_hardening_headers(settings):
    async with api_client(settings) as client:
        response = await client.get("/api/health")
    csp = response.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp
    assert "script-src 'self'" in csp
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"


def _creds(email: str) -> dict[str, str]:
    return {"email": email, "password": "correct horse battery", "name": "Test User"}
