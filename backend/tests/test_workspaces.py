import asyncio
from datetime import UTC, datetime, timedelta

import psycopg

from tests.conftest import plain_dsn
from tests.helpers import create_workspace, invite, join, signed_in


def _sql(pg, query, *params):
    with psycopg.connect(plain_dsn(pg["app"])) as conn:
        cursor = conn.execute(query, params)
        return cursor.fetchall() if cursor.description else None


async def _roles(client, workspace_id) -> dict[str, str]:
    members = (await client.get(f"/api/workspaces/{workspace_id}/members")).json()["members"]
    return {m["user_id"]: m["role"] for m in members}


async def test_creating_a_workspace_makes_the_creator_its_only_owner(settings):
    async with signed_in(settings) as owner:
        workspace_id = await create_workspace(owner)
        assert await _roles(owner, workspace_id) == {owner.user_id: "owner"}


async def test_admins_invite_members_but_only_the_owner_invites_admins(settings):
    async with signed_in(settings) as owner, signed_in(settings) as admin:
        ws = await create_workspace(owner)
        await join(owner, ws, admin, "admin")
        member_invite = await admin.post(f"/api/workspaces/{ws}/invites", json={"role": "member"})
        admin_invite = await admin.post(f"/api/workspaces/{ws}/invites", json={"role": "admin"})
        assert member_invite.status_code == 201
        assert admin_invite.status_code == 404
        assert (
            await owner.post(f"/api/workspaces/{ws}/invites", json={"role": "admin"})
        ).status_code == 201


async def test_invite_lifetimes_depend_on_the_role(settings):
    async with signed_in(settings) as owner:
        ws = await create_workspace(owner)
        now = datetime.now(UTC)
        for role, expected in (("member", timedelta(days=7)), ("admin", timedelta(hours=24))):
            body = (await owner.post(f"/api/workspaces/{ws}/invites", json={"role": role})).json()
            expires = datetime.fromisoformat(body["expires_at"])
            assert abs((expires - now) - expected) < timedelta(minutes=1)
            assert "#" in body["invite_url"]  # the secret travels in the fragment


async def test_an_invite_used_concurrently_admits_one_person(settings, pg):
    async with (
        signed_in(settings) as owner,
        signed_in(settings) as first,
        signed_in(settings) as second,
    ):
        ws = await create_workspace(owner)
        token = await invite(owner, ws)
        results = await asyncio.gather(
            first.post("/api/invites/accept", json={"token": token}),
            second.post("/api/invites/accept", json={"token": token}),
        )
        assert sorted(r.status_code for r in results) == [200, 404]
        assert len(await _roles(owner, ws)) == 2


async def test_expired_revoked_or_used_invites_are_refused(settings, pg):
    async with signed_in(settings) as owner, signed_in(settings) as a, signed_in(settings) as b:
        ws = await create_workspace(owner)
        used = await invite(owner, ws)
        assert (await a.post("/api/invites/accept", json={"token": used})).status_code == 200
        assert (await b.post("/api/invites/accept", json={"token": used})).status_code == 404

        expired = await invite(owner, ws)
        prefix = expired.split("_")[2]
        _sql(
            pg,
            "UPDATE invites SET expires_at = now() - interval '1 minute' WHERE prefix = %s",
            prefix,
        )
        assert (await b.post("/api/invites/accept", json={"token": expired})).status_code == 404
        refusals = (
            await owner.get(f"/api/workspaces/{ws}/audit", params={"action": "accept_invite"})
        ).json()["events"]
        assert {"accept_invite.used", "accept_invite.expired"} <= {e["rule_id"] for e in refusals}

        revoked = await invite(owner, ws)
        pending = (await owner.get(f"/api/workspaces/{ws}/invites")).json()["invites"]
        invite_id = next(i["id"] for i in pending if i["prefix"] == revoked.split("_")[2])
        assert (await owner.delete(f"/api/workspaces/{ws}/invites/{invite_id}")).status_code == 204
        assert (await b.post("/api/invites/accept", json={"token": revoked})).status_code == 404


async def test_an_invite_stops_working_when_its_creator_loses_the_authority(settings):
    async with (
        signed_in(settings) as owner,
        signed_in(settings) as admin,
        signed_in(settings) as newcomer,
    ):
        ws = await create_workspace(owner)
        await join(owner, ws, admin, "admin")
        member_link = await invite(admin, ws, "member")
        admin_link = await invite(owner, ws, "admin")
        # The admin is demoted, and the owner hands ownership to someone else.
        assert (
            await owner.patch(
                f"/api/workspaces/{ws}/members/{admin.user_id}", json={"role": "member"}
            )
        ).status_code == 200
        for link in (member_link,):
            assert (
                await newcomer.post("/api/invites/accept", json={"token": link})
            ).status_code == 404
        await owner.patch(f"/api/workspaces/{ws}/members/{admin.user_id}", json={"role": "admin"})
        assert (
            await owner.post(f"/api/workspaces/{ws}/transfer", json={"user_id": admin.user_id})
        ).status_code == 200
        # The former owner is now an admin and can no longer issue admin invites.
        assert (
            await newcomer.post("/api/invites/accept", json={"token": admin_link})
        ).status_code == 404


async def test_an_existing_member_cannot_use_an_invite_and_it_stays_valid(settings):
    async with (
        signed_in(settings) as owner,
        signed_in(settings) as member,
        signed_in(settings) as newcomer,
    ):
        ws = await create_workspace(owner)
        await join(owner, ws, member)
        token = await invite(owner, ws, "member")
        assert (await member.post("/api/invites/accept", json={"token": token})).status_code == 404
        assert (await owner.post("/api/invites/accept", json={"token": token})).status_code == 404
        assert (
            await newcomer.post("/api/invites/accept", json={"token": token})
        ).status_code == 200


async def test_group_membership_changes_take_effect_immediately(settings, pg):
    async with signed_in(settings) as owner, signed_in(settings) as member:
        ws = await create_workspace(owner)
        await join(owner, ws, member)
        group = (await owner.post(f"/api/workspaces/{ws}/groups", json={"name": "Platform"})).json()
        url = f"/api/workspaces/{ws}/groups/{group['id']}/members/{member.user_id}"
        assert (await owner.put(url)).status_code == 204
        mine = (await member.get(f"/api/workspaces/{ws}/groups")).json()["groups"]
        assert [g["name"] for g in mine] == ["Platform"]
        assert (await owner.delete(url)).status_code == 204
        assert (await member.get(f"/api/workspaces/{ws}/groups")).json()["groups"] == []
        assert _sql(pg, "SELECT 1 FROM group_members WHERE group_id = %s", group["id"]) == []


async def test_members_see_their_own_groups_and_managers_see_all(settings):
    async with signed_in(settings) as owner, signed_in(settings) as member:
        ws = await create_workspace(owner)
        await join(owner, ws, member)
        for name in ("Mine", "Theirs"):
            await owner.post(f"/api/workspaces/{ws}/groups", json={"name": name})
        groups = {
            g["name"]: g for g in (await owner.get(f"/api/workspaces/{ws}/groups")).json()["groups"]
        }
        await owner.put(
            f"/api/workspaces/{ws}/groups/{groups['Mine']['id']}/members/{member.user_id}"
        )
        member_view = (await member.get(f"/api/workspaces/{ws}/groups")).json()["groups"]
        assert [g["name"] for g in member_view] == ["Mine"]
        assert "members" not in member_view[0]
        assert (await member.get(f"/api/workspaces/{ws}/members")).status_code == 404
        duplicate = await owner.post(f"/api/workspaces/{ws}/groups", json={"name": "mine"})
        assert duplicate.status_code == 409


async def test_demoting_an_admin_changes_only_their_role(settings, pg):
    async with signed_in(settings) as owner, signed_in(settings) as admin:
        ws = await create_workspace(owner)
        await join(owner, ws, admin, "admin")
        _sql(
            pg,
            "INSERT INTO access_tokens (workspace_id, user_id, name, prefix, verifier, "
            "expires_at) VALUES (%s, %s, 't', md5(random()::text), 'x', now() + interval '1 day')",
            ws,
            admin.user_id,
        )
        await owner.patch(f"/api/workspaces/{ws}/members/{admin.user_id}", json={"role": "member"})
        assert (await _roles(owner, ws))[admin.user_id] == "member"
        assert _sql(
            pg, "SELECT revoked_at FROM access_tokens WHERE user_id = %s", admin.user_id
        ) == [(None,)]


async def test_removing_a_member_deletes_group_memberships_and_revokes_tokens(settings, pg):
    async with signed_in(settings) as owner, signed_in(settings) as member:
        ws = await create_workspace(owner)
        await join(owner, ws, member)
        group = (await owner.post(f"/api/workspaces/{ws}/groups", json={"name": "G"})).json()
        await owner.put(f"/api/workspaces/{ws}/groups/{group['id']}/members/{member.user_id}")
        _sql(
            pg,
            "INSERT INTO access_tokens (workspace_id, user_id, name, prefix, verifier, "
            "expires_at) VALUES (%s, %s, 't', md5(random()::text), 'x', now() + interval '1 day')",
            ws,
            member.user_id,
        )
        assert (
            await owner.delete(f"/api/workspaces/{ws}/members/{member.user_id}")
        ).status_code == 204
        assert _sql(pg, "SELECT 1 FROM group_members WHERE user_id = %s", member.user_id) == []
        revoked = _sql(
            pg, "SELECT revoked_at FROM access_tokens WHERE user_id = %s", member.user_id
        )
        assert revoked[0][0] is not None
        memberships = (await member.get("/api/auth/me")).json()["memberships"]
        assert memberships == []


async def test_admins_remove_members_but_only_the_owner_removes_admins(settings):
    async with (
        signed_in(settings) as owner,
        signed_in(settings) as admin,
        signed_in(settings) as admin2,
        signed_in(settings) as member,
    ):
        ws = await create_workspace(owner)
        await join(owner, ws, admin, "admin")
        await join(owner, ws, admin2, "admin")
        await join(owner, ws, member)
        assert (
            await admin.delete(f"/api/workspaces/{ws}/members/{admin2.user_id}")
        ).status_code == 404
        assert (
            await admin.delete(f"/api/workspaces/{ws}/members/{owner.user_id}")
        ).status_code == 404
        assert (
            await admin.delete(f"/api/workspaces/{ws}/members/{member.user_id}")
        ).status_code == 204
        assert (
            await owner.delete(f"/api/workspaces/{ws}/members/{admin2.user_id}")
        ).status_code == 204


async def test_ownership_moves_only_to_an_admin_and_leaves_one_owner(settings):
    async with (
        signed_in(settings) as owner,
        signed_in(settings) as admin,
        signed_in(settings) as member,
    ):
        ws = await create_workspace(owner)
        await join(owner, ws, admin, "admin")
        await join(owner, ws, member)
        to_member = await owner.post(
            f"/api/workspaces/{ws}/transfer", json={"user_id": member.user_id}
        )
        assert to_member.status_code == 404
        to_admin = await owner.post(
            f"/api/workspaces/{ws}/transfer", json={"user_id": admin.user_id}
        )
        assert to_admin.status_code == 200
        roles = await _roles(admin, ws)
        assert roles[admin.user_id] == "owner"
        assert roles[owner.user_id] == "admin"
        assert list(roles.values()).count("owner") == 1


async def test_deleting_a_group_removes_its_grants(settings, pg):
    async with signed_in(settings) as owner:
        ws = await create_workspace(owner)
        group = (await owner.post(f"/api/workspaces/{ws}/groups", json={"name": "G"})).json()
        with psycopg.connect(plain_dsn(pg["app"])) as conn:
            doc = conn.execute(
                "INSERT INTO documents (workspace_id, author_id) VALUES (%s, %s) RETURNING id",
                (ws, owner.user_id),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO document_grants (workspace_id, document_id, group_id) "
                "VALUES (%s, %s, %s)",
                (ws, doc, group["id"]),
            )
        response = await owner.delete(f"/api/workspaces/{ws}/groups/{group['id']}")
        assert response.status_code == 200
        assert response.json()["grants_removed"] == 1
        assert _sql(pg, "SELECT 1 FROM document_grants WHERE document_id = %s", doc) == []
