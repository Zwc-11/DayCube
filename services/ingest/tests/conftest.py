import sys

import pytest
from fastapi.testclient import TestClient
from ingest.api import create_app
from ingest.config import PROJECT_ROOT, Settings
from ingest.vault import Vault

sys.path.insert(0, str(PROJECT_ROOT / "services/capture-win"))


@pytest.fixture
def vault(tmp_path):
    store = Vault(tmp_path / "vault")
    store.initialize()
    store.seed()
    return store


@pytest.fixture
def client(vault):
    settings = Settings(vault_root=vault.root, _env_file=None)
    with TestClient(create_app(settings)) as connection:
        yield connection


@pytest.fixture
def auth(vault):
    return {"Authorization": f"Bearer {vault.token}"}
