from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def make_engine(url: str) -> AsyncEngine:
    return create_async_engine(url, pool_pre_ping=True)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)


class CommitThenRaise(Exception):
    """Raise to end a request with an error while still committing its transaction.

    Used for denials, whose audit row must be kept (KTD8, R29).
    """

    def __init__(self, error: Exception):
        self.error = error


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """One transaction per request; it commits before the response is sent (KTD8)."""
    async with request.app.state.sessionmaker() as session:
        transaction = await session.begin()
        try:
            yield session
        except CommitThenRaise as signal:
            await transaction.commit()
            raise signal.error from None
        except BaseException:
            await transaction.rollback()
            raise
        else:
            await transaction.commit()


# scope="function" closes the transaction before FastAPI sends the response.
DB = Annotated[AsyncSession, Depends(get_db, scope="function")]
