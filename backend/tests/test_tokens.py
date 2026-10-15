from tests.helpers import PASSWORD, create_workspace, signed_in


async def test_a_token_secret_is_shown_once(settings):
    async with signed_in(settings) as owner:
        ws = await create_workspace(owner)
        created = await owner.post(
            f"/api/workspaces/{ws}/tokens", json={"name": "Claude", "password": PASSWORD}
        )
        assert created.status_code == 201
        assert created.json()["token"].startswith("lat_pat_")
        listed = (await owner.get(f"/api/workspaces/{ws}/tokens")).json()["tokens"]
        assert [set(t) for t in listed] == [
            {"id", "name", "prefix", "created_at", "expires_at", "revoked", "last_used_at"}
        ]


async def test_creating_a_token_requires_the_password(settings):
    async with signed_in(settings) as owner:
        ws = await create_workspace(owner)
        response = await owner.post(
            f"/api/workspaces/{ws}/tokens", json={"name": "Claude", "password": "wrong-password"}
        )
        assert response.status_code == 403


async def test_people_only_see_and_revoke_their_own_tokens(settings):
    async with signed_in(settings) as owner, signed_in(settings) as other:
        ws = await create_workspace(owner)
        token = (
            await owner.post(
                f"/api/workspaces/{ws}/tokens", json={"name": "Mine", "password": PASSWORD}
            )
        ).json()
        assert (await other.delete(f"/api/workspaces/{ws}/tokens/{token['id']}")).status_code == 404
        assert (await owner.delete(f"/api/workspaces/{ws}/tokens/{token['id']}")).status_code == 204
