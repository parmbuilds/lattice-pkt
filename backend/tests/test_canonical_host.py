import httpx

from app.main import create_app


async def test_ui_on_another_host_redirects_to_the_public_origin(settings, tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html><title>Lattice</title>")
    app = create_app(settings.model_copy(update={"static_dir": str(tmp_path)}))
    origin = settings.public_origin
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client:
        alias = await client.get("http://localhost:8000/signin?next=x")
        public = await client.get(f"{origin}/signin")
        api = await client.get("http://localhost:8000/api/health")
    await app.state.engine.dispose()
    assert alias.status_code == 307
    assert alias.headers["location"] == f"{origin}/signin?next=x"
    assert public.status_code == 200
    assert api.status_code == 200  # the API itself is not redirected
