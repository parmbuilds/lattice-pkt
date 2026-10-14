from urllib.parse import urlsplit

from mcp.server import MCPServer
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings

from app.config import Settings


class StaticTokenVerifier(TokenVerifier):
    """U1 spike only: accepts one fixed bearer token. Replaced by the PAT verifier in U9."""

    def __init__(self, token: str):
        self._token = token

    async def verify_token(self, token: str) -> AccessToken | None:
        if token != self._token:
            return None
        return AccessToken(token=token, client_id="spike", scopes=[])


def transport_security(settings: Settings) -> TransportSecuritySettings:
    host = urlsplit(settings.public_origin).netloc
    return TransportSecuritySettings(
        allowed_hosts=[host, "localhost:*", "127.0.0.1:*"],
        allowed_origins=[settings.public_origin, "http://localhost:*", "http://127.0.0.1:*"],
    )


def auth_settings(settings: Settings) -> AuthSettings:
    # Demo adapter (KTD6): no OAuth authorization server exists, so no protected-resource
    # metadata is advertised (resource_server_url=None).
    return AuthSettings(issuer_url=settings.public_origin, resource_server_url=None)


def build_spike_mcp(settings: Settings, token: str) -> MCPServer:
    mcp = MCPServer(
        "Lattice", token_verifier=StaticTokenVerifier(token), auth=auth_settings(settings)
    )

    @mcp.tool()
    async def spike_ping() -> str:
        return "pong"

    return mcp
