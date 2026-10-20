import asyncio
import json
import logging

import httpx
import pytest

from app.ai.provider import (
    FakeProvider,
    OpenAICompatibleProvider,
    ProviderError,
    make_http_client,
    make_provider,
)
from app.config import AiModel, AiProvider, Settings

KEY = "sk-live-secret-123"
MODELS = [AiModel(id="m1", label="M1", context_tokens=8000, max_output_tokens=500)]
OPENAI = AiProvider(id="acme", label="Acme", base_url="https://ai.acme.test/v1", models=MODELS)
AZURE = OPENAI.model_copy(update={"auth": "api-key"})
FAKE = AiProvider(id="fake", label="Fake", kind="fake", models=MODELS)
MESSAGES = [{"role": "user", "content": "Where are the keys?"}]


def sse(*payloads: object, done: bool = True) -> bytes:
    lines = [f"data: {json.dumps(p)}\n\n" for p in payloads]
    return ("".join(lines) + ("data: [DONE]\n\n" if done else "")).encode()


def delta(text: str | None) -> dict:
    return {"choices": [{"delta": {"content": text}}]}


def provider_for(handler, entry: AiProvider = OPENAI, **client_args) -> OpenAICompatibleProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), **client_args)
    provider = make_provider(entry, "m1", KEY, client)
    assert isinstance(provider, OpenAICompatibleProvider)
    return provider


async def collect(provider, messages=MESSAGES) -> str:
    return "".join([chunk async for chunk in provider.stream(messages, max_tokens=100)])


async def expect_error(provider, kind: str, caplog) -> None:
    with caplog.at_level(logging.DEBUG), pytest.raises(ProviderError) as raised:
        await collect(provider)
    assert raised.value.kind == kind
    assert KEY not in str(raised.value) and KEY not in repr(raised.value)
    assert KEY not in caplog.text


async def test_a_stream_in_three_chunks_yields_the_full_text_in_order():
    body = sse({"choices": []}, delta(None), delta("Keys are "), delta("in the "), delta("safe."))
    provider = provider_for(lambda request: httpx.Response(200, content=body))
    assert [c async for c in provider.stream(MESSAGES, max_tokens=100)] == [
        "Keys are ",
        "in the ",
        "safe.",
    ]


@pytest.mark.parametrize(
    ("entry", "header", "value"),
    [(OPENAI, "authorization", f"Bearer {KEY}"), (AZURE, "api-key", KEY)],
    ids=["bearer", "api-key"],
)
async def test_the_request_goes_to_the_entry_with_its_auth_header_and_no_tools(
    entry, header, value
):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=sse(delta("ok")))

    await collect(provider_for(handler, entry))
    (request,) = seen
    assert str(request.url) == "https://ai.acme.test/v1/chat/completions"
    assert request.method == "POST"
    assert request.headers[header] == value
    other = "api-key" if header == "authorization" else "authorization"
    assert other not in request.headers
    body = json.loads(request.content)
    assert body == {
        "model": "m1",
        "messages": MESSAGES,
        "max_completion_tokens": 100,
        "stream": True,
    }


async def test_complete_returns_one_non_streamed_answer():
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "Pong."}}]})

    assert await provider_for(handler).complete(MESSAGES, max_tokens=5) == "Pong."
    assert seen[0]["stream"] is False and "tools" not in seen[0]


async def test_a_redirect_is_not_followed_and_the_key_is_not_resent(caplog):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(302, headers={"location": "https://evil.test/steal"})

    # Even a client that would follow redirects must not, for a provider request.
    await expect_error(provider_for(handler, follow_redirects=True), "failed", caplog)
    assert [r.url.host for r in seen] == ["ai.acme.test"]


async def test_a_401_is_key_rejected(caplog):
    provider = provider_for(lambda request: httpx.Response(401, json={"error": KEY}))
    await expect_error(provider, "key_rejected", caplog)


@pytest.mark.parametrize("status", [429, 500])
async def test_other_http_errors_are_provider_failed(status, caplog):
    await expect_error(provider_for(lambda request: httpx.Response(status)), "failed", caplog)


@pytest.mark.parametrize(
    "error",
    [httpx.ConnectError("refused"), httpx.ReadTimeout("slow")],
    ids=["connection-error", "read-timeout"],
)
async def test_transport_errors_and_timeouts_are_provider_failed(error, caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    await expect_error(provider_for(handler), "failed", caplog)


@pytest.mark.parametrize(
    "body",
    [sse(delta("Keys are "), done=False), b"data: {not json\n\n", sse({"choices": "x"})],
    ids=["no-done", "bad-json", "bad-shape"],
)
async def test_a_truncated_or_malformed_stream_is_provider_failed(body, caplog):
    provider = provider_for(lambda request: httpx.Response(200, content=body))
    await expect_error(provider, "failed", caplog)


async def test_complete_maps_failures_the_same_way():
    provider = provider_for(lambda request: httpx.Response(401))
    with pytest.raises(ProviderError) as raised:
        await provider.complete(MESSAGES, max_tokens=5)
    assert raised.value.kind == "key_rejected"


def test_the_shared_client_has_explicit_timeouts_and_never_follows_redirects():
    client = make_http_client(Settings(ai_connect_timeout_seconds=3, ai_read_timeout_seconds=20))
    assert client.follow_redirects is False
    assert client.timeout == httpx.Timeout(3, read=20)


# The fake provider.

PASSAGES = (
    "Answer from these records.\n"
    '<passage id="P1" title="Keys">\nKeys are in the safe. Ask Ana for access.\n</passage>\n'
    '<passage id="P2" title="Badges">\nBadges expire yearly! Renew online.\n</passage>'
)


def fake(read_timeout: float = 5.0) -> FakeProvider:
    client = httpx.AsyncClient(timeout=httpx.Timeout(5.0, read=read_timeout))
    provider = make_provider(FAKE, "m1", "", client)
    assert isinstance(provider, FakeProvider)
    return provider


def asking(question: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": "Rules."},
        {"role": "user", "content": PASSAGES},
        {"role": "user", "content": question},
    ]


async def test_the_fake_cites_only_the_passages_it_was_given():
    chunks = [c async for c in fake().stream(asking("Where?"), max_tokens=100)]
    assert len(chunks) > 2
    assert "".join(chunks) == "Keys are in the safe. [P1]\nBadges expire yearly! [P2]\n"


async def test_the_fake_can_emit_an_unsupported_sentence():
    text = await collect(fake(), asking("Where? #unsupported"))
    assert text.endswith("The moon is made of cheese. [P1]\n")


async def test_the_fake_can_fail_after_the_first_line():
    seen: list[str] = []
    with pytest.raises(ProviderError) as raised:
        async for chunk in fake().stream(asking("#fail"), max_tokens=100):
            seen.append(chunk)
    assert raised.value.kind == "failed"
    assert "".join(seen) == "Keys are in the safe. [P1]\n"


async def test_the_fake_can_reject_the_key_before_any_output():
    seen: list[str] = []
    with pytest.raises(ProviderError) as raised:
        async for chunk in fake().stream(asking("#reject"), max_tokens=100):
            seen.append(chunk)
    assert raised.value.kind == "key_rejected" and seen == []


async def test_the_fake_stalls_for_the_read_timeout_before_the_second_line():
    stream = fake(read_timeout=60).stream(asking("#stall"), max_tokens=100)
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.2):
            first = ""
            async for chunk in stream:
                first += chunk
    assert first == "Keys are in the safe. [P1]\n"


async def test_the_fake_hooks_read_only_the_last_user_message():
    messages = [*asking("Where?"), {"role": "assistant", "content": "#reject"}]
    assert (await collect(fake(), messages)).startswith("Keys are in the safe.")


async def test_the_fake_completes_with_a_fixed_summary():
    assert await fake().complete(asking("#reject"), max_tokens=10) == "Summary of earlier turns."
