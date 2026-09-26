import os
from typing import TypeVar, Any

from pydantic import validate_call

from potato_util.io import read_all_configs

from api.core.constants import ENV_PREFIX_API, API_SLUG
from api.core.configs import MainConfig
from api.logger import logger

ConfigType = TypeVar("ConfigType", bound=MainConfig)


@validate_call
def load_config(
    configs_dir: str = os.path.join("/etc", API_SLUG),
    env_name: str = f"{ENV_PREFIX_API}CONFIGS_DIR",
    config_schema: type[ConfigType] = MainConfig,
) -> ConfigType:
    _configs_dir_env = os.getenv(env_name, "")
    if _configs_dir_env:
        configs_dir = _configs_dir_env

    _config_dict: dict[str, Any] = {}
    if os.path.isdir(configs_dir):
        _config_dict = read_all_configs(configs_dir=configs_dir)

    _config: ConfigType | None = None
    try:
        _config = config_schema(**_config_dict)
    except Exception:
        logger.exception("Failed to load config:")
        raise SystemExit(1)

    _warn_unknown_keys(_config_dict, _config)
    return _config


def _warn_unknown_keys(raw: dict[str, Any], loaded: Any, _path: str = "") -> None:
    """Warn about config keys the model does not declare.

    `extra="allow"` is required (`env_file` makes every subclass absorb
    unrelated `.env` keys), so `k_value: 5` instead of `k: 5` is silent - the
    default stands and the run is misconfigured. Warn, never raise: a typo
    should not be an outage.
    """
    _fields = getattr(type(loaded), "model_fields", None)
    if not _fields:
        return

    for _key, _value in raw.items():
        _where = f"{_path}{_key}"
        if _key not in _fields:
            logger.warning(
                f"Config key '{_where}' is not a declared setting - it is being "
                f"ignored. Check for a typo."
            )
            continue
        if isinstance(_value, dict):
            _warn_unknown_keys(_value, getattr(loaded, _key), _path=f"{_where}.")


config = load_config()


__all__ = [
    "MainConfig",
    "load_config",
    "config",
]
