import logging
import os

import pytest

# The app refuses to start without an API key, and its config is read once at
# import - so this must be set before any test imports it. A real .env still
# wins in a dev checkout.
os.environ.setdefault("EXC_CHALLENGE_CHALLENGE_API_KEY", "pytest-only-api-key")

logger = logging.getLogger(__name__)


@pytest.fixture(scope="session", autouse=True)
def setup_and_teardown():
    # Equivalent of setUp
    logger.info("Setting up...")

    yield  # This is where the testing happens!

    # Equivalent of tearDown
    logger.info("Tearing down!")
