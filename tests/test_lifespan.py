from contextlib import asynccontextmanager
from typing import Any, AsyncContextManager, AsyncIterator, Union

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient
from starlette.types import ASGIApp, Receive, Scope, Send

from asgi_lifespan._middleware import LifespanMiddleware


class TrackingLifespan(AsyncContextManager[None]):
    def __init__(self) -> None:
        self.setup_called = False
        self.teardown_called = False

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
    assert inner_lifespan.teardown_called


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
