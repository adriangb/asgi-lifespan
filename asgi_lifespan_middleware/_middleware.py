from contextlib import AsyncExitStack
import traceback
from typing import AsyncContextManager, Callable, TypeVar

from asgi_lifespan import LifespanManager, LifespanNotSupported

from asgi_lifespan_middleware._types import ASGIApp, Receive, Scope, Send

WrappedApp = TypeVar("WrappedApp", bound=ASGIApp)


Lifespan = Callable[[WrappedApp], AsyncContextManager[None]]


class LifespanMiddleware:
    __slots__ = ("_app", "_lifespan")

    def __init__(self, app: WrappedApp, *, lifespan: Lifespan[WrappedApp]) -> None:
        self._app = app
        self._lifespan = lifespan

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "lifespan":  # pragma: no cover
            await self._app(scope, receive, send)
            return

        started = False
        await receive()
        try:
            async with AsyncExitStack() as stack:
                await stack.enter_async_context(self._lifespan(self._app))
                try:
                    await stack.enter_async_context(LifespanManager(self._app))  # type: ignore
                except LifespanNotSupported:
                    pass
                await send({"type": "lifespan.startup.complete"})
                started = True
                await receive()
        except BaseException:
            exc_text = traceback.format_exc()
            if started:
                await send({"type": "lifespan.shutdown.failed", "message": exc_text})
            else:
                await send({"type": "lifespan.startup.failed", "message": exc_text})
            raise
        else:
            await send({"type": "lifespan.shutdown.complete"})
