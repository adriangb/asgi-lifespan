import traceback
from contextlib import AsyncExitStack, asynccontextmanager
from typing import AsyncContextManager, AsyncIterator, Callable, Dict, TypeVar

from asgi_lifespan_middleware._types import ASGIApp, Message, Receive, Scope, Send

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

        rcv_events: Dict[str, bool] = {}
        send_events: Dict[str, bool] = {}

        async def wrapped_rcv() -> Message:
            message = await receive()
            rcv_events[message["type"]] = True
            return message

        async def wrapped_send(message: Message) -> None:
            send_events[message["type"]] = True
            if message["type"] == "lifespan.shutdown.complete":
                # we want to send this one ourselves
                # once we are done
                return
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

        lifespan_cm = self._lifespan(self._app)

        async with AsyncExitStack() as stack:
            await stack.enter_async_context(cleanup())
            await stack.enter_async_context(lifespan_cm)
            try:
                # one of 4 things will happen when we call the app:
                # 1. it supports lifespans. it will block until the server
                #    sends the shutdown signal, at which point we get control
                #    back and can run our own teardown
                # 2. it does nothing and returns. in this case we do the
                #    back and forth with the ASGI server ourselves
                # 3. it raises an exception.
                #    a. before raising the exception it sent a
                #       "lifespan.startup.failed" message
                #       this means it supports lifespans, but it's lifespan
                #       errored out. we'll re-raise to trigger our teardown
                #    b. it did not send a "lifespan.startup.failed" message
                #       this app doesn't support lifespans, the spec says
                #       to just swallow the exception and proceed
                # 4. it supports lifespan events and it's lifespan fails
                #    (it sends a "lifespan.startup.failed" message)
                #    in this case we'll run our teardown and then return
                await self._app(scope, wrapped_rcv, wrapped_send)
            except BaseException:
                if (
                    "lifespan.startup.failed" in send_events
                    or "lifespan.shutdown.failed" in send_events
                ):
                    # the app tried to start and failed
                    # this app re-raises the exceptions (Starlette does this)
                    # re-raise so that our teardown is triggered
                    raise
                # the app doesn't support lifespans
                # the spec says to ignore these errors and just don't send
                # more lifespan events
            if "lifespan.startup.failed" in send_events:
                # the app supports lifespan events
                # but it failed to start
                # this app does not re-raise exceptions
                # so all we can do is run our teardown and exit
                return
            if "lifespan.startup.complete" not in send_events:
                # the app doesn't support lifespans at all
                # so we'll have to talk to the ASGI server ourselves
                await receive()
                await send({"type": "lifespan.startup.complete"})
                # we'll block here until the ASGI server shuts us down
                await receive()
        # even if the app sent this, we intercepted it and discarded it until we were done
        await send({"type": "lifespan.shutdown.complete"})
