import asyncio
import contextlib
import os
import re
from collections.abc import AsyncGenerator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from potato_util.io import async_create_dir
from potato_util.crypto import asymmetric as asymmetric_utils
from potato_util.crypto import ssl as ssl_utils

from api.__version__ import __version__
from api.config import config
from api.mount import BAIT_INDEX
from api.core.dependencies.auth import is_well_formed
from api.endpoints.challenge._browser import BAIT_HOST, BAIT_TLS_PORT
from api.endpoints.challenge._pool import load_pool_groups
from api.endpoints.challenge.utils import reset_detections_dir
from api.logger import logger


def _check_ssl_certs() -> None:
    """Check if SSL certificates exist when SSL is enabled or set to be generated.

    Raises:
        SystemExit: If SSL certificates are missing or cannot be created.
    """

    if config.api.security.ssl.generate:
        ssl_utils.create_ssl_certs(
            ssl_dir=config.api.paths.ssl_dir,
            key_fname=config.api.security.ssl.key_fname,
            cert_fname=config.api.security.ssl.cert_fname,
            key_size=config.api.security.ssl.key_size,
            x509_attrs=config.api.security.ssl.x509_attrs.model_dump(),
        )

    if config.api.security.ssl.enabled:
        _ssl_keyfile_path = os.path.join(
            config.api.paths.ssl_dir, config.api.security.ssl.key_fname
        )
        _ssl_certfile_path = os.path.join(
            config.api.paths.ssl_dir, config.api.security.ssl.cert_fname
        )

        if (not os.path.isfile(_ssl_keyfile_path)) or (
            not os.path.isfile(_ssl_certfile_path)
        ):
            logger.error("SSL key or certificate file not found!")
            raise SystemExit(1)

    return


def _check_api_key() -> None:
    """Refuse to start without a usable API key.

    Raises:
        SystemExit: If the key is unset, or has a shape `auth_api_key` rejects.
    """

    _key = config.challenge.api_key
    _var = f"{config.challenge.model_config['env_prefix']}API_KEY"
    if _key is None or not _key.get_secret_value():
        logger.error(f"{_var} is not set - refusing to start without an API key.")
        raise SystemExit(1)
    if not is_well_formed(_key.get_secret_value()):
        logger.error(
            f"{_var} must be 9-128 characters of letters, digits and hyphens; "
            f"every request would be rejected."
        )
        raise SystemExit(1)

    return


def _check_bait_page_assets() -> None:
    """Refuse to start when the bait page references a file that is missing.

    A missing bundle renders an empty page and zeroes every miner. That is a
    property of the image, so it is checked once here - not inferred per
    round from page state, which the submission can fake (see
    `_browser.PageInfraError`).

    Raises:
        SystemExit: If a local `./static/...` asset of index.html is missing.
    """

    _html = BAIT_INDEX.read_text(encoding="utf-8")
    _assets = re.findall(r'(?:src|href)="\.?/?(static/(?:js|css)/[^"]+)"', _html)
    _missing = [_a for _a in _assets if not (BAIT_INDEX.parent / _a).is_file()]
    if not _assets or _missing:
        logger.error(
            f"Bait page {BAIT_INDEX} is broken - missing assets: "
            f"{_missing or 'no script bundle referenced'}"
        )
        raise SystemExit(1)

    return


def pre_init() -> None:
    """Pre-initialization tasks before creating FastAPI application."""

    _check_api_key()
    _check_bait_page_assets()
    _check_ssl_certs()
    # Add more pre-initialization tasks here...

    return


async def _async_create_dirs() -> None:
    """Create directories before starting FastAPI application.

    Raises:
        SystemExit: If failed to create directories.
    """

    try:
        await async_create_dir(config.api.paths.data_dir)
        # Add directories that need to be created here...
    except Exception:
        logger.exception("Failed to create directories:")
        raise SystemExit(1)

    return


class _BaitServer(uvicorn.Server):
    """The https listener for the bait page. The main server owns process
    signals; without this, a second `serve()` swaps the handlers out from
    under it and Ctrl+C or a container stop no longer reaches the app."""

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


def _start_bait_server(app: FastAPI) -> tuple[_BaitServer, asyncio.Task]:
    """Serve this same app over https on loopback, for Chrome only.

    Loopback-only and never published, so the validator keeps its plain http
    port and nothing about its contract changes. A fresh self-signed
    certificate per start: Chrome is told to accept it, and it has no other
    page to trust it for - the container has no network. `lifespan="off"`
    because this app's lifespan is already running - it is what starts us.
    """
    _tls_dir = Path(config.challenge.browser.scratch_dir) / "tls"
    ssl_utils.create_ssl_certs(
        ssl_dir=str(_tls_dir),
        key_fname="bait.key",
        cert_fname="bait.crt",
        key_size=2048,
        x509_attrs={"CN": BAIT_HOST, "DNS": BAIT_HOST},
        force=True,
    )
    _server = _BaitServer(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=BAIT_TLS_PORT,
            ssl_keyfile=str(_tls_dir / "bait.key"),
            ssl_certfile=str(_tls_dir / "bait.crt"),
            lifespan="off",
            log_config=None,
            # The loopback check must see the real peer, never a header.
            proxy_headers=False,
        )
    )
    return _server, asyncio.create_task(_server.serve())


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Lifespan context manager for FastAPI application.
    Startup and shutdown events are logged.

    Args:
        app (FastAPI, required): FastAPI application instance.
    """

    logger.info("Preparing to startup...")
    # await _async_create_dirs()
    if config.api.security.asymmetric.generate:
        await asymmetric_utils.async_create_keys(
            asymmetric_keys_dir=config.api.paths.asymmetric_keys_dir,
            key_size=config.api.security.asymmetric.key_size,
            private_key_fname=config.api.security.asymmetric.private_key_fname,
            public_key_fname=config.api.security.asymmetric.public_key_fname,
        )

    # Start from clean detector stubs, whatever a crashed run or a hand-copied
    # file left behind - see `reset_detections_dir`.
    _changed = reset_detections_dir(list(load_pool_groups()))
    if _changed:
        logger.warning(f"Reset stale detection files at startup: {_changed}")

    _bait_server, _bait_task = _start_bait_server(app)
    logger.info(f"Bait page served at https://{BAIT_HOST}:{BAIT_TLS_PORT} (loopback only)")

    logger.success("Finished preparation to startup.")
    logger.opt(colors=True).info(f"Version: <c>{__version__}</c>")
    logger.opt(colors=True).info(f"API version: <c>{config.api.version}</c>")
    logger.opt(colors=True).info(f"API prefix: <c>{config.api.prefix}</c>")
    logger.opt(colors=True).info(
        f"Listening on: <c>{config.api.http_scheme}://{config.api.bind_host}:{config.api.port}</c>"
    )

    yield

    logger.info("Praparing to shutdown...")
    _bait_server.should_exit = True
    await _bait_task
    logger.success("Finished preparation to shutdown.")


__all__ = [
    "pre_init",
    "lifespan",
]
