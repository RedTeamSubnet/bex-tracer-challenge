"""Production does not read the compose-mounted config file, so the code defaults
ARE the production values. If the config file says something different, local
runs test one challenge and production runs another - which happened: the file
said 2 rounds of 10 while production would have run the old defaults, 20 of 5.
"""

import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src/exc_challenge/challenge"))

from api.core.configs._challenge import ChallengeConfig  # noqa: E402

_CONFIG_FILES = (
    REPO / "volumes/configs/rest-exc-challenge/challenge.yml",
    REPO / "templates/configs/challenge/challenge.yml",
)
_ROUND_SETTINGS = (
    "n_rounds",
    "k",
    "coverage_bias",
    "max_parallel_rounds",
    "settle_seconds",
    "script_budget_sec",
    "submission_max_lines",
)


@pytest.mark.parametrize("path", _CONFIG_FILES, ids=lambda p: p.parent.name)
def test_config_file_matches_the_production_defaults(path):
    written = yaml.safe_load(path.read_text())["challenge"]
    for name in _ROUND_SETTINGS:
        if name in written:
            default = ChallengeConfig.model_fields[name].default
            assert written[name] == default, (
                f"{path.name} sets {name}={written[name]}, but production uses the "
                f"code default {default}. Change the default in _challenge.py."
            )
