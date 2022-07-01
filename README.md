# asgi-lifespan-middleware

ASGI middlewate to support ASGI lifespans using a simple async context manager interface.

This middleware accepts an ASGI application to wrap and an async context manager lifespan.
It will run both the lifespan it was handed directly and that of the ASGI app (if the wrapped ASGI app supports lifespans).

## Example (Starlette)

Starlette apps already support lifespans so we'll just be using the TestClient against a plain ASGI app that does nothing.

```python
from contextlib import asynccontextmanager
from typing import AsyncIterator

from starlette.testclient import TestClient
from starlette.types import ASGIApp, Scope, Send, Receive

from asgi_lifespan_middleware import LifespanMiddleware

@asynccontextmanager
async def lifespan(
    # you'll get the wrapped app injected
    app: ASGIApp,
) -> AsyncIterator[None]:
    print("setup")
    yield
    print("teardown")


async def app(scope: Scope, receive: Receive, send: Send) -> None:
    ...  # do nothing


wrapped_app = LifespanMiddleware(
    app,
    lifespan=lifespan,
)

with TestClient(wrapped_app):
    pass
```
