"""The one seam code reaches an AI chat model through (KTD1, KTD9).

Every failure becomes a `ProviderError`: "key_rejected" for a 401, "failed" for anything
else. Neither the key nor any request header is ever put in a message or a log line.
"""

import asyncio
import json
import re
from collections.abc import AsyncIterator
from typing import Literal, Protocol

import httpx

from app.config import AiProvider, Settings


class ProviderError(Exception):
    def __init__(self, kind: Literal["key_rejected", "failed"], detail: str = ""):
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind


class ChatProvider(Protocol):
    def stream(self, messages: list[dict[str, str]], *, max_tokens: int) -> AsyncIterator[str]:
        """Yield the answer's text deltas in order."""
        ...

    async def complete(self, messages: list[dict[str, str]], *, max_tokens: int) -> str:
        """One short non-streamed answer, for condensing and the key check."""
        ...


def _check(response: httpx.Response) -> None:
    if response.status_code == 401:
        raise ProviderError("key_rejected", "HTTP 401")
    # A 3xx lands here too: it is never followed, so the key never reaches another host.
    if not response.is_success:
        raise ProviderError("failed", f"HTTP {response.status_code}")


# Malformed JSON or an unexpected shape in a provider response.
_MALFORMED = (ValueError, KeyError, IndexError, TypeError, AttributeError)


class OpenAICompatibleProvider:
    """OpenAI Chat Completions over httpx; one attempt, no tools, no redirects (KTD8, KTD9)."""

    def __init__(self, entry: AiProvider, model: str, api_key: str, client: httpx.AsyncClient):
        self._client = client
        self._url = entry.base_url.rstrip("/") + "/chat/completions"
        self._model = model
        self._headers = (
            {"Authorization": f"Bearer {api_key}"}
            if entry.auth == "bearer"
            else {"api-key": api_key}
        )

    def _body(self, messages: list[dict[str, str]], max_tokens: int, stream: bool) -> dict:
        return {
            "model": self._model,
            "messages": messages,
            "max_completion_tokens": max_tokens,
            "stream": stream,
        }

    async def stream(
        self, messages: list[dict[str, str]], *, max_tokens: int
    ) -> AsyncIterator[str]:
        try:
            async with self._client.stream(
                "POST",
                self._url,
                headers=self._headers,
                json=self._body(messages, max_tokens, stream=True),
                follow_redirects=False,
            ) as response:
                _check(response)
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue  # blank separators, SSE comments and other fields
                    data = line.removeprefix("data:").strip()
                    if data == "[DONE]":
                        return
                    # Some servers send chunks with no choices, or a delta with no content.
                    choices = json.loads(data)["choices"]
                    if choices and (text := choices[0]["delta"].get("content")):
                        yield text
        except httpx.HTTPError as error:
            raise ProviderError("failed", type(error).__name__) from error
        except _MALFORMED as error:
            raise ProviderError("failed", "malformed stream") from error
        raise ProviderError("failed", "stream ended before [DONE]")

    async def complete(self, messages: list[dict[str, str]], *, max_tokens: int) -> str:
        try:
            response = await self._client.post(
                self._url,
                headers=self._headers,
                json=self._body(messages, max_tokens, stream=False),
                follow_redirects=False,
            )
            _check(response)
            text = response.json()["choices"][0]["message"]["content"]
        except httpx.HTTPError as error:
            raise ProviderError("failed", type(error).__name__) from error
        except _MALFORMED as error:
            raise ProviderError("failed", "malformed response") from error
        if not isinstance(text, str):
            raise ProviderError("failed", "malformed response")
        return text


_PASSAGE = re.compile(r'<passage id="([^"]+)"[^>]*>\n(.*?)\n</passage>', re.DOTALL)
_FIRST_SENTENCE = re.compile(r".*?[.!?](?=\s|$)", re.DOTALL)


class FakeProvider:
    """Deterministic provider for tests, e2e and development: cites the passages it is given.

    The last user message steers it: `#unsupported` adds an uncited claim, `#fail` fails
    after the first line, `#reject` rejects the key, `#stall` waits out the read timeout.
    """

    def __init__(self, client: httpx.AsyncClient):
        self._stall_seconds = client.timeout.read or 0.0

    async def stream(
        self, messages: list[dict[str, str]], *, max_tokens: int
    ) -> AsyncIterator[str]:
        question = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        if "#reject" in question:
            raise ProviderError("key_rejected", "fake")
        lines = []
        for message in messages:
            for passage_id, text in _PASSAGE.findall(message["content"]):
                sentence = _FIRST_SENTENCE.match(text.strip())
                first = " ".join((sentence.group() if sentence else text).split())
                lines.append(f"{first} [{passage_id}]\n")
        if "#unsupported" in question:
            lines.append("The moon is made of cheese. [P1]\n")
        if "#fail" in question:
            lines = lines[:1]
        for index, line in enumerate(lines):
            if index == 1 and "#stall" in question:
                await asyncio.sleep(self._stall_seconds)
            for chunk in re.findall(r"\S+\s*", line):
                yield chunk
        if "#fail" in question:
            raise ProviderError("failed", "fake")

    async def complete(self, messages: list[dict[str, str]], *, max_tokens: int) -> str:
        return "Summary of earlier turns."


def make_http_client(settings: Settings) -> httpx.AsyncClient:
    """The shared client every provider call goes through; close it on shutdown."""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(
            settings.ai_connect_timeout_seconds, read=settings.ai_read_timeout_seconds
        ),
        # shortcut: a fixed pool of 20 provider calls; make it a setting if answers queue.
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=5),
        follow_redirects=False,
    )


def make_provider(
    entry: AiProvider, model: str, api_key: str, client: httpx.AsyncClient
) -> ChatProvider:
    if entry.kind == "fake":
        return FakeProvider(client)
    return OpenAICompatibleProvider(entry, model, api_key, client)
