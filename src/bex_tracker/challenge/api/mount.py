from pathlib import Path

from pydantic import validate_call
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

# The bait page miners' scripts run against. Served by the app itself so a round
# reaches it over loopback; it must be http, not file:// - web_accessible_resources
# declare `matches: ["http://*/*", "https://*/*"]`, so from a file:// page every
# WAR probe is a false negative no matter which extensions are loaded.
_BAIT_DIR = Path(__file__).resolve().parent.parent / "templates"


@validate_call(config={"arbitrary_types_allowed": True})
def add_mounts(app: FastAPI) -> None:
    """Add mounts to FastAPI app.

    Args:
        app (FastAPI): FastAPI app instance.
    """

    app.mount("/_web", StaticFiles(directory=str(_BAIT_DIR)), name="bait")
    # Add mounts here

    return


__all__ = ["add_mounts"]
