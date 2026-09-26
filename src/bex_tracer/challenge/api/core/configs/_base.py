from typing import Any

from pydantic_settings import (
    BaseSettings,
    SettingsConfigDict,
    PydanticBaseSettingsSource,
    CliSettingsSource,
    NestedSecretsSettingsSource,
)

from api.core import utils


class BaseConfig(BaseSettings):
    model_config = SettingsConfigDict(
        extra="allow",
        env_file=".env",
        validate_default=True,
        validate_assignment=True,
        arbitrary_types_allowed=True,
    )

    def declared_dump(self) -> dict[str, Any]:
        """`model_dump()` narrowed to the fields this model actually declares.

        `extra="allow"` plus `env_file` means every subclass also absorbs the
        unrelated keys in `.env` - `ENV`, `DEBUG`, other components' settings,
        the API key. Splatting a plain `model_dump()` into a constructor passes
        those on as unexpected kwargs and carries the secrets with them, so any
        `**config.<x>.model_dump()` call site wants this instead.
        """

        return self.model_dump(include=set(type(self).model_fields))


class FrozenBaseConfig(BaseConfig):
    model_config = SettingsConfigDict(frozen=True)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            NestedSecretsSettingsSource(file_secret_settings),
            dotenv_settings,
            env_settings,
            init_settings,
        )


class BaseMainConfig(FrozenBaseConfig):
    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        _sources = []
        if not utils.is_running_bin():
            _sources.append(CliSettingsSource(settings_cls, cli_parse_args=True))
        _sources.extend(
            [
                NestedSecretsSettingsSource(file_secret_settings),
                dotenv_settings,
                env_settings,
                init_settings,
            ]
        )
        return tuple(_sources)


__all__ = [
    "BaseConfig",
    "FrozenBaseConfig",
    "BaseMainConfig",
]
