"""Extraction in a child process (KTD11 step 4): timeout, address-space limit and a global cap.

The child is a fresh interpreter exec'd with an empty environment, so it inherits no secrets and
no parent memory. Bytes and limits go in over stdin; the result comes back as JSON, never pickle,
so a parser compromised by a hostile file cannot run code in the server.
"""

import asyncio
import dataclasses
import functools
import json
import logging
import os
import sys
from pathlib import Path

from app.ingest import Extraction, Limits, Rejected, extract

log = logging.getLogger(__name__)

_BACKEND = str(Path(__file__).resolve().parents[2])
# -I ignores PYTHON* variables, user site-packages and the working directory.
_CHILD = (
    "import sys; sys.path.insert(0, sys.argv[1]); "
    "from app.ingest.runner import child_main; child_main()"
)
_slots: asyncio.Semaphore | None = None


@functools.cache
def _warn_no_memory_limit() -> None:
    log.warning("RLIMIT_AS is not enforced on macOS; extraction runs without a memory limit")


async def extract_in_child(data: bytes, filename: str, limits: Limits) -> Extraction:
    """Run `extract` in a killable child; raises `Rejected`, including `timeout`."""
    global _slots
    if _slots is None:
        # shortcut: sized once from the first caller's limits; settings are fixed per process.
        _slots = asyncio.Semaphore(limits.concurrency)
    if sys.platform == "darwin":
        _warn_no_memory_limit()
        limits = dataclasses.replace(limits, memory_bytes=0)
    request = json.dumps({"filename": filename, "limits": dataclasses.asdict(limits)})
    async with _slots:
        child = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            "-c",
            _CHILD,
            _BACKEND,
            env={},
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            out, _ = await asyncio.wait_for(
                child.communicate(request.encode() + b"\n" + data), limits.timeout_seconds
            )
        except TimeoutError:
            raise Rejected("timeout") from None
        finally:
            if child.returncode is None:  # timed out or cancelled
                child.kill()
                await child.wait()
    try:
        result = json.loads(out)
        if "rejected" in result:
            status = 415 if result["rejected"]["status"] == 415 else 422
            raise Rejected(str(result["rejected"]["reason"]), status)
        return Extraction.from_dict(result["extraction"])
    except (ValueError, KeyError, TypeError):  # crashed, killed by its limit, or garbled
        raise Rejected("extraction_failed") from None


def child_main() -> None:
    result_out = os.fdopen(os.dup(1), "w")
    os.dup2(2, 1)  # anything a library prints goes to stderr, not into the result
    header, _, data = sys.stdin.buffer.read().partition(b"\n")
    request = json.loads(header)
    limits = Limits(**request["limits"])
    if limits.memory_bytes:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (limits.memory_bytes, limits.memory_bytes))
    try:
        result = {"extraction": extract(data, request["filename"], limits).to_dict()}
    except Rejected as rejected:
        result = {"rejected": {"reason": rejected.reason, "status": rejected.status}}
    except MemoryError:
        result = {"rejected": {"reason": "memory_limit", "status": 422}}
    result_out.write(json.dumps(result))
    result_out.close()
