import httpx
from mcp import Client
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

from app.main import create_app
from app.mcp_server import build_spike_mcp
from tests.conftest import live_server

TOKEN = "spike-token"


def _transport(base_url: str, token: str | None):
    headers = {"Authorization": f"Bearer {token}"} if token else None
    return streamable_http_client(
        f"{base_url}/mcp", http_client=create_mcp_http_client(headers=headers)
    )


async def test_mcp_lists_tools_over_http_with_bearer_token(settings):
    app = create_app(settings, mcp=build_spike_mcp(settings, TOKEN))
    with live_server(app) as base_url:
        async with Client(_transport(base_url, TOKEN)) as client:
            tools = await client.list_tools()
    assert [tool.name for tool in tools.tools] == ["spike_ping"]


async def test_mcp_refuses_calls_without_a_bearer_token(settings):
    app = create_app(settings, mcp=build_spike_mcp(settings, TOKEN))
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"},
        },
    }
    with live_server(app) as base_url:
        async with httpx.AsyncClient() as http:
            response = await http.post(
                f"{base_url}/mcp",
                json=initialize,
                headers={"Accept": "application/json, text/event-stream"},
            )
    assert response.status_code == 401
