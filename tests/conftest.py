from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from dhcpv6_api.kea import KeaConfigRejected, KeaError
from dhcpv6_api.main import create_app
from dhcpv6_api.settings import Settings

REPO = Path(__file__).resolve().parents[1]
API_KEY = "test-key-0123456789"


class FakeKea:
    """Stands in for the Kea control socket and records what it was asked to do."""

    def __init__(self) -> None:
        self.tested: list[dict[str, Any]] = []
        self.reloads = 0
        self.reject_with: str | None = None
        self.fail_reloads = 0

    def config_test(self, config: dict[str, Any]) -> None:
        self.tested.append(config)
        if self.reject_with:
            raise KeaConfigRejected(f"Kea rejected the configuration: {self.reject_with}")

    def config_reload(self) -> dict[str, Any]:
        self.reloads += 1
        if self.fail_reloads:
            self.fail_reloads -= 1
            raise KeaError("Kea failed to reload its configuration: boom")
        return {"result": 0, "text": "Configuration successful."}

    def status(self) -> dict[str, Any]:
        return {"pid": 42, "uptime": 120}


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    # The repo's annotated config, comments and all
    path = tmp_path / "kea-dhcp6.conf"
    shutil.copy(REPO / "config" / "kea-dhcp6.conf", path)
    return path


@pytest.fixture
def leases_path(tmp_path: Path) -> Path:
    return tmp_path / "kea-leases6.csv"


@pytest.fixture
def fake_kea() -> FakeKea:
    return FakeKea()


@pytest.fixture
def make_client(config_path: Path, leases_path: Path):
    def make(kea: Any = None) -> TestClient:
        settings = Settings(
            api_key=API_KEY,
            kea_config_path=config_path,
            kea_leases_file=leases_path,
            kea_control_socket=config_path.parent / "missing.sock",
            kea_socket_timeout=0.5,
        )
        return TestClient(create_app(settings, kea=kea), headers={"X-API-Key": API_KEY})

    return make


@pytest.fixture
def client(make_client, fake_kea: FakeKea) -> TestClient:
    return make_client(fake_kea)
