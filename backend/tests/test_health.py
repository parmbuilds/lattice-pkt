import httpx

from app.main import create_app


async def test_health_reports_ok_against_the_database(settings):
    app = create_app(settings)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000"
    ) as client:
        response = await client.get("/api/health")
    await app.state.engine.dispose()
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
