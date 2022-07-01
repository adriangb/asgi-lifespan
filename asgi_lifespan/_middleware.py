import traceback
from contextlib import AsyncExitStack, asynccontextmanager
from typing import AsyncContextManager, AsyncIterator, Callable, List

from asgi_lifespan._types import ASGIApp, Message, Receive, Scope, Send

Lifespan = Callable[[ASGIApp], AsyncContextManager[None]]


class LifespanMiddleware:
    __slots__ = ("_app", "_lifespan")

    def __init__(self, app: ASGIApp, *, lifespan: Lifespan) -> None:
        self._app = app
        self._lifespan = lifespan

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "lifespan":
            await self._app(scope, receive, send)
            return

        send_events: "List[str]" = []

        async def wrapped_send(message: Message) -> None:
            if message["type"] == "lifespan.startup.complete":
                # wrapped app lifespan is complete
                send_events.append("lifespan.startup.complete")
            await send(message)

        @asynccontextmanager
        async def cleanup() -> "AsyncIterator[None]":
            try:
                yield
            except BaseException:
                exc_text = traceback.format_exc()
                if "lifespan.startup.complete" in send_events:
                    await send(
                        {"type": "lifespan.shutdown.failed", "message": exc_text}
                    )
                else:
                    await send({"type": "lifespan.startup.failed", "message": exc_text})
                raise
            else:
                await send({"type": "lifespan.shutdown.complete"})

        async with AsyncExitStack() as stack:
            await stack.enter_async_context(cleanup())
            await stack.enter_async_context(self._lifespan(self._app))
            try:
                # one of 4 things will happen when we call the app:
                # 1. it supports lifespans. it will block until the server
                #    sends the shutdown signal, at which point we get control
                #    back and can run our own teardown
                # 2. it does nothing and returns. in this case we do the
                #    back and forth with the ASGI server ourselves
                # 3. it raises an exception. as per the spec, we can
                #    swallow the exception and just stop sending lifespan
                #    events (i.e. don't retry or anything like that)
                # 4. it supports lifespan events and it's lifespan fails
                #    (it sends a "lifespan.startup.failed" message)
                #    in this case we'll run our teardown and then return
                await self._app(scope, receive, wrapped_send)
            except Exception:
                # spec says just don't send any more lifespan events
                pass
            if "lifespan.startup.failed" in send_events:
                # the app tried to start and failed
                # we'll just run our teardown and exit
                return
            if "lifespan.startup.complete" not in send_events:
                # the app doesn't support lifespans, we'll have
                # to talk to the ASGI server ourselves for our lifespan
                await receive()
                await send({"type": "lifespan.startup.complete"})
                # we'll block here until the ASGI server shuts us down
                await receive()
