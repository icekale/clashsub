from pathlib import Path
import logging

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from clashsub.app import create_app
from clashsub.config import Settings
from clashsub.settings import RuntimeSettings


@pytest.fixture(autouse=True)
def _reset_clashsub_logger_handlers():
    logger = logging.getLogger("clashsub")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    yield
    for handler in list(logger.handlers):
        logger.removeHandler(handler)


@pytest.fixture
def app_settings(tmp_path: Path):
    return Settings(
        data_dir=tmp_path / "data",
        frontend_dir=tmp_path / "frontend",
        upstream_url=SecretStr("https://provider.invalid/sub?token=hidden"),
        initial_username=SecretStr("initial-user"),
        initial_password=SecretStr("initial-password"),
        airport_api_base_url="https://panel.example.test/api/v1",
        airport_email=SecretStr("member@example.test"),
        airport_password=SecretStr("airport-pass"),
    )


@pytest.fixture
def client(app_settings):
    with TestClient(
        create_app(app_settings, start_scheduler=False),
        client=("127.0.0.1", 50000),
    ) as value:
        value.app.state.services.runtime_settings.update(RuntimeSettings(lan_base_url="http://testserver"))
        yield value
