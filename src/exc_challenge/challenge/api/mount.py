from pathlib import Path

from pydantic import validate_call
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

# The bait page miners' scripts run against. Served by the app itself so a round
# reaches it over loopback; it must be http, not file:// - web_accessible_resources
# declare `matches: ["http://*/*", "https://*/*"]`, so from a file:// page every
# WAR probe is a false negative no matter which extensions are loaded.
_BAIT_DIR = Path(__file__).resolve().parent.parent / "templates"


# During a run this directory holds the submitting miner's code, and Chrome
# reaches it from inside the container. Left open on the published port, a rival
# could read that submission and copy it - which the similarity gate then
# penalises for both. Loopback only.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})


class _LoopbackOnly:
    """Serve the wrapped app only to clients on the loopback interface."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        _client = scope.get("client")
        _host = _client[0] if _client else None
        if _host not in _LOOPBACK_HOSTS:
            # 404, not 403 - do not confirm the path exists.
            await PlainTextResponse("Not Found", status_code=404)(
                scope, receive, send
            )
            return

        await self.app(scope, receive, send)


@validate_call(config={"arbitrary_types_allowed": True})
def add_mounts(app: FastAPI) -> None:
    """Add mounts to FastAPI app.

    Args:
        app (FastAPI): FastAPI app instance.
    """

    app.mount(
        "/_web",
        _LoopbackOnly(StaticFiles(directory=str(_BAIT_DIR))),
        name="bait",
    )
    # Add mounts here

    return


__all__ = ["add_mounts"]
