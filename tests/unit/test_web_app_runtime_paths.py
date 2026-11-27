"""web/app.py honours RuntimeSettings: state under the data dir, live geocode cache too."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from agentic_cab.core.settings import RuntimeSettings
from agentic_cab.web.app import _build_geocoder, _sessions, create_app

_LOOPBACK_BASE_URL = "http://127.0.0.1:8000"

_NOMINATIM_PAYLOAD = [
    {"display_name": "1 Test St, Testville", "lat": "10.0", "lon": "20.0", "importance": 0.9}
]


@pytest.fixture(autouse=True)
def _isolated_sessions() -> Iterator[None]:
    _sessions.clear()
    yield
    _sessions.clear()


def _snapshot(d: Path) -> dict[str, int]:
    return {p.name: p.stat().st_mtime_ns for p in d.iterdir()} if d.exists() else {}


def mock_nominatim_http() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _req: httpx.Response(200, json=_NOMINATIM_PAYLOAD))
    )


def test_create_app_puts_state_db_under_env_data_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("AGENTIC_CAB_DATA_DIR", str(tmp_path / "env-data"))
    monkeypatch.setenv("AGENTIC_CAB_LOG_DIR", str(tmp_path / "env-logs"))

    with TestClient(create_app(), base_url=_LOOPBACK_BASE_URL) as client:
        assert client.post("/sessions").status_code == 200  # noqa: S101 pytest assertion

    assert (tmp_path / "env-data" / "state.db").exists()  # noqa: S101 pytest assertion
    assert (tmp_path / "env-logs").is_dir()  # noqa: S101 pytest assertion
    assert not (cwd / "data").exists()  # noqa: S101 pytest assertion
    assert not (cwd / "logs").exists()  # noqa: S101 pytest assertion


@pytest.mark.asyncio
async def test_build_geocoder_writes_cache_under_data_dir(tmp_path: Path) -> None:
    fixtures = tmp_path / "fixtures" / "geocode"
    fixtures.mkdir(parents=True)
    (fixtures / "seed.json").write_text("{}")
    before = _snapshot(fixtures)
    settings = RuntimeSettings.from_env({}).with_overrides(
        data_dir=tmp_path / "data", geocode_fixtures_dir=fixtures
    )

    geocoder = _build_geocoder(settings, http=mock_nominatim_http())
    with patch("agentic_cab.adapters.geocoding.nominatim.asyncio.sleep", new=AsyncMock()):
        ref = await geocoder.geocode("somewhere new")

    assert ref.formatted == "1 Test St, Testville"  # noqa: S101 pytest assertion
    assert len(list((tmp_path / "data" / "geocode").glob("*.json"))) == 1  # noqa: S101 pytest assertion
    assert _snapshot(fixtures) == before  # noqa: S101 pytest assertion


def test_explicit_kwargs_win_over_settings(tmp_path: Path) -> None:
    settings = RuntimeSettings.from_env({"AGENTIC_CAB_DATA_DIR": str(tmp_path / "settings-data")})
    app = create_app(settings=settings, data_dir=tmp_path / "kw-data", log_dir=tmp_path / "kw-logs")
    with TestClient(app, base_url=_LOOPBACK_BASE_URL) as client:
        assert client.post("/sessions").status_code == 200  # noqa: S101 pytest assertion
    assert (tmp_path / "kw-data" / "state.db").exists()  # noqa: S101 pytest assertion
    assert not (tmp_path / "settings-data").exists()  # noqa: S101 pytest assertion
    assert (tmp_path / "kw-logs").is_dir()  # noqa: S101 pytest assertion
