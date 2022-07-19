from contextlib import asynccontextmanager
from typing import Any, AsyncContextManager, AsyncIterator, List, Union

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from asgi_lifespan_middleware import LifespanMiddleware


class TrackingLifespan(AsyncContextManager[None]):
    def __init__(self) -> None:
        self.setup_called: bool = False
        self.teardown_called: bool = False

    def __call__(self, *args: Any) -> AsyncContextManager[None]:
        assert len(args) == 1
        return self

    async def __aenter__(self) -> None:
        self.setup_called = True

    async def __aexit__(self, *args: Any) -> Union[bool, None]:
        self.teardown_called = True
        return None


def test_single_lifespan_application_supports_lifespan() -> None:
    # we should call the wrapped app's lifespan
    outer_lifespan = TrackingLifespan()
    inner_lifespan = TrackingLifespan()

    app = LifespanMiddleware(
        app=Starlette(lifespan=inner_lifespan), lifespan=outer_lifespan
    )

    with TestClient(app):
        assert outer_lifespan.setup_called
        assert inner_lifespan.setup_called
        assert not outer_lifespan.teardown_called
        assert not inner_lifespan.teardown_called

    assert outer_lifespan.teardown_called
    assert inner_lifespan.teardown_called  # type: ignore #  pragma: no cover


async def no_lifespan_app_does_nothing(
    scope: Scope, receive: Receive, send: Send
) -> None:
    pass


async def no_lifespan_app_raises_exception(
    scope: Scope, receive: Receive, send: Send
) -> None:
    raise Exception


@pytest.mark.parametrize(
    "app", (no_lifespan_app_does_nothing, no_lifespan_app_raises_exception)
)
def test_single_lifespan_application_does_not_support_lifespan(app: ASGIApp) -> None:
    # if the wrapped application does nothing with the lifespan events
    # or if it raises an exception when they are sent we should
    # stop sending asgi events and still execute our lifespan

    lifespan = TrackingLifespan()

    app = LifespanMiddleware(app=app, lifespan=lifespan)

    with TestClient(app):
        assert lifespan.setup_called
        assert not lifespan.teardown_called

    assert lifespan.teardown_called


def test_lifespan_execution_order() -> None:
    # should execute like an onion / nested cms
    outer_lifespan = TrackingLifespan()

    @asynccontextmanager
    async def inner_lifespan_probe(app: Starlette) -> AsyncIterator[None]:
        # setup on outer lifespan called before us
        assert outer_lifespan.setup_called
        yield
        # our teardown gets called before the outer teardown
        assert not outer_lifespan.teardown_called

    app = LifespanMiddleware(
        app=Starlette(lifespan=inner_lifespan_probe), lifespan=outer_lifespan
    )

    with TestClient(app):
        assert outer_lifespan.setup_called
        assert not outer_lifespan.teardown_called

    assert outer_lifespan.teardown_called


@pytest.mark.anyio
async def test_lifespan_startup_failure() -> None:
    # handle the case where the lifespan raises an exception
    # during it's setup
    # we should send the "lifespan.startup.failed" message
    # we also choose to re-raise the exception, but that is not required

    class MyException(Exception):
        pass

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        raise MyException
        yield  # type: ignore #  pragma: no cover

    app = LifespanMiddleware(app=Starlette(), lifespan=lifespan)

    scope = {"type": "lifespan"}

    sent_messages: List[str] = []

    async def rcv() -> Message:
        return {"type": "lifespan.startup"}

    async def send(message: Message) -> None:
        sent_messages.append(message["type"])

    # not again that we don't _have_ to re-raise the exception
    # it's up to our implementation, and we decide to do it
    # so we'll check that we do
    with pytest.raises(MyException):
        await app(scope, rcv, send)

    assert sent_messages == ["lifespan.startup.failed"]


@pytest.mark.anyio
async def test_lifespan_teardown_failure() -> None:
    # handle the case where the lifespan raises an exception
    # during it's teardown
    # we should send the "lifespan.shutdown.failed" message
    # we also choose to re-raise the exception, but that is not required

    class MyException(Exception):
        pass

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        yield
        raise MyException

    app = LifespanMiddleware(app=Starlette(), lifespan=lifespan)

    scope = {"type": "lifespan"}

    sent_messages: List[str] = []

    async def rcv_gen() -> AsyncIterator[Message]:
        yield {"type": "lifespan.startup"}
        yield {"type": "lifespan.shutdown"}

    async def send(message: Message) -> None:
        sent_messages.append(message["type"])

    rcv = rcv_gen()

    # not again that we don't _have_ to re-raise the exception
    # it's up to our implementation, and we decide to do it
    # so we'll check that we do
    with pytest.raises(MyException):
        await app(scope, rcv.__anext__, send)

    assert sent_messages == ["lifespan.startup.complete", "lifespan.shutdown.failed"]


def test_application_lifespan_fails_with_exception_during_setup() -> None:
    # the application's lifespan fails to run and raises an exception during it's setup

    class MyException(Exception):
        pass

    lifespan = TrackingLifespan()

    @asynccontextmanager
    async def bad_lifespan(app: Starlette) -> AsyncIterator[None]:
        raise MyException
        yield  # type: ignore #  pragma: no cover

    app = LifespanMiddleware(app=Starlette(lifespan=bad_lifespan), lifespan=lifespan)

    with pytest.raises(MyException):
        with TestClient(app):
            assert lifespan.setup_called
            assert not lifespan.teardown_called

    assert lifespan.teardown_called


@pytest.mark.xfail(reason="TestClient errors out, need to investigate")
def test_application_lifespan_fails_without_exception_during_setup() -> None:
    # the application's lifespan fails to run and doesn't raise anything

    async def bad_app(scope: Scope, receive: Receive, send: Send) -> None:
        await receive()
        await send({"type": "lifespan.startup.failed"})
        return

    lifespan = TrackingLifespan()

    app = LifespanMiddleware(app=bad_app, lifespan=lifespan)

    with TestClient(app):
        assert lifespan.setup_called
        assert not lifespan.teardown_called


def test_application_lifespan_fails_with_exception_during_teardown() -> None:
    # the application's lifespan fails to run and raises
    # an exception during it's teardown

    class MyException(Exception):
        pass

    lifespan = TrackingLifespan()

    @asynccontextmanager
    async def bad_lifespan(app: Starlette) -> AsyncIterator[None]:
        yield
        raise MyException

    app = LifespanMiddleware(app=Starlette(lifespan=bad_lifespan), lifespan=lifespan)

    with pytest.raises(MyException):
        with TestClient(app):
            assert lifespan.setup_called
            assert not lifespan.teardown_called

    assert lifespan.teardown_called
