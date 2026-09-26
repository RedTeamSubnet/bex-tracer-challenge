import secrets
from typing import NoReturn

from fastapi import Security
from fastapi.security import APIKeyHeader

from potato_util.constants import ALPHANUM_HYPHEN_REGEX
from potato_util import validator

from api.core.constants import ErrorCodeEnum
from api.config import config
from api.core.exceptions import BaseHTTPException

_auth_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def _reject(message: str, reason: str) -> NoReturn:
    """Every rejection looks identical to the caller except for `reason`,
    which never says which check failed in a way that helps a guesser."""
    raise BaseHTTPException(
        error_enum=ErrorCodeEnum.UNAUTHORIZED,
        message=message,
        headers={"WWW-Authenticate": f'X-API-Key error="{reason}"'},
    )


def is_well_formed(api_key: str) -> bool:
    """The shape every key must have - checked on each request, and on the
    configured key at startup, so a key the app could never accept fails to
    boot instead of rejecting every request."""
    return 8 < len(api_key) <= 128 and validator.is_valid(
        val=api_key, pattern=ALPHANUM_HYPHEN_REGEX
    )


def auth_api_key(api_key: str | None = Security(_auth_header)) -> None:
    """Dependency function to authenticate a request by shared API key.

    `/score` is called by the validator, which has no user to identify, so a
    shared key is the whole of the authentication story here.

    Args:
        api_key (str, optional): 'X-API-Key: <api_key>' header value.

    Raises:
        BaseHTTPException: If the API key is missing, malformed or wrong.
    """

    if (not api_key) or (not isinstance(api_key, str)) or (not api_key.strip()):
        _reject("Not authenticated!", "missing_api_key")

    if not is_well_formed(api_key):
        _reject("Invalid API key!", "invalid_api_key")

    # `pre_init()` makes an unset key impossible in a running app; this only
    # keeps a misbuilt one closed rather than crashing.
    _expected = config.challenge.api_key
    if _expected is None:
        _reject("Invalid API key!", "invalid_api_key")

    # compare_digest, not `!=`: a short-circuiting comparison leaks the shared
    # key one character at a time to anyone who can measure the response.
    if not secrets.compare_digest(api_key, _expected.get_secret_value()):
        _reject("Invalid API key!", "invalid_api_key")

    return


__all__ = [
    "auth_api_key",
    "is_well_formed",
]
