"""Two instances with different AGENTIC_CAB_* values never share files or a port.

No model, no network, no API key. Limit: two apps built in one process still
share the module-level `_sessions` registry and `_bus` in web/app.py; real
instances are separate uvicorn processes (see README "Running two instances").
"""

from __future__ import annotations

from collections.abc import Iterator
from itertools import combinations
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from agentic_cab import __main__ as cli
from agentic_cab.core.settings import RuntimeSettings
from agentic_cab.web.app import _build_geocoder, _sessions, create_app

_LOOPBACK_BASE_URL = "http://127.0.0.1:8000"
_PAYLOAD = [{"display_name": "1 Test St", "lat": "1.0", "lon": "2.0", "importance": 0.9}]


@pytest.fixture(autouse=True)
def _isolated_sessions() -> Iterator[None]:
    _sessions.clear()
    yield
    _sessions.clear()


def _env(root: Path, port: int) -> dict[str, str]:
    return {
        "AGENTIC_CAB_DATA_DIR": str(root / "data"),
        "AGENTIC_CAB_LOG_DIR": str(root / "logs"),
        "AGENTIC_CAB_CHAT_DIR": str(root / "chat"),
        "AGENTIC_CAB_WEB_PORT": str(port),
    }


def _dirs(s: RuntimeSettings) -> list[Path]:
    return [s.data_dir, s.log_dir, s.chat_dir, s.geocode_cache_dir]


def _overlaps(a: Path, b: Path) -> bool:
    ra, rb = a.resolve(), b.resolve()
    return ra == rb or ra in rb.parents or rb in ra.parents


def _snapshot(d: Path) -> dict[str, int]:
    return {p.name: p.stat().st_mtime_ns for p in sorted(d.iterdir())}


def _files_under(root: Path) -> set[Path]:
    return {p for p in root.rglob("*") if p.is_file()}


@pytest.fixture
def instances(tmp_path: Path) -> tuple[RuntimeSettings, RuntimeSettings, Path]:
    fixtures = tmp_path / "shared-fixtures" / "geocode"
    fixtures.mkdir(parents=True)
    (fixtures / "seed.json").write_text("{}")
    a = RuntimeSettings.from_env(_env(tmp_path / "a", 8101))
    b = RuntimeSettings.from_env(_env(tmp_path / "b", 8102))
    return (
        a.with_overrides(geocode_fixtures_dir=fixtures),
        b.with_overrides(geocode_fixtures_dir=fixtures),
        fixtures,
    )


def test_resolved_dirs_and_ports_are_disjoint(
    instances: tuple[RuntimeSettings, RuntimeSettings, Path],
) -> None:
    a, b, _ = instances
    assert a.web_port != b.web_port  # noqa: S101 pytest assertion
    for da in _dirs(a):
        for db in _dirs(b):
            assert not _overlaps(da, db), (da, db)  # noqa: S101 pytest assertion
    for s in (a, b):
        for d1, d2 in combinations({s.data_dir, s.log_dir, s.chat_dir}, 2):
            assert not _overlaps(d1, d2)  # noqa: S101 pytest assertion


def test_each_app_writes_state_db_only_in_its_own_data_dir(
    tmp_path: Path, instances: tuple[RuntimeSettings, RuntimeSettings, Path]
) -> None:
    a, b, _ = instances
    with (
        TestClient(create_app(settings=a), base_url=_LOOPBACK_BASE_URL) as ca,
        TestClient(create_app(settings=b), base_url=_LOOPBACK_BASE_URL) as cb,
    ):
        assert ca.post("/sessions").status_code == 200  # noqa: S101 pytest assertion
        assert cb.post("/sessions").status_code == 200  # noqa: S101 pytest assertion
    dbs = {p for p in _files_under(tmp_path) if p.name == "state.db"}
    assert dbs == {a.data_dir / "state.db", b.data_dir / "state.db"}  # noqa: S101 pytest assertion


@pytest.mark.asyncio
async def test_each_geocoder_caches_only_under_its_own_data_dir(
    tmp_path: Path, instances: tuple[RuntimeSettings, RuntimeSettings, Path]
) -> None:
    a, b, fixtures = instances
    before = _snapshot(fixtures)

    def _http() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _r: httpx.Response(200, json=_PAYLOAD))
        )

    with patch("agentic_cab.adapters.geocoding.nominatim.asyncio.sleep", new=AsyncMock()):
        await _build_geocoder(a, http=_http()).geocode("query for a")
        await _build_geocoder(b, http=_http()).geocode("query for b")

    a_files = _files_under(tmp_path / "a")
    b_files = _files_under(tmp_path / "b")
    assert len(a_files) == 1  # noqa: S101 pytest assertion
    assert len(b_files) == 1  # noqa: S101 pytest assertion
    assert all(p.parent == a.geocode_cache_dir for p in a_files)  # noqa: S101 pytest assertion
    assert all(p.parent == b.geocode_cache_dir for p in b_files)  # noqa: S101 pytest assertion
    assert _snapshot(fixtures) == before  # noqa: S101 pytest assertion


def test_one_apps_cookie_sweep_leaves_the_other_apps_session_cookies_alone(
    instances: tuple[RuntimeSettings, RuntimeSettings, Path],
) -> None:
    """Browsers share cookies across ports, so tab B sends tab A's session cookie.

    Separate processes do not share `_sessions`; A's entry is set aside while B
    mints to model that, then restored to show A's tab still authorizes.
    """
    a, b, _ = instances
    with (
        TestClient(create_app(settings=a), base_url=_LOOPBACK_BASE_URL) as ca,
        TestClient(create_app(settings=b), base_url=_LOOPBACK_BASE_URL) as cb,
    ):
        sid_a = ca.post("/sessions").json()["session_id"]
        a_cookies = dict(ca.cookies.items())
        assert a_cookies  # noqa: S101 pytest assertion
        for name, value in a_cookies.items():
            cb.cookies.set(name, value)  # one browser jar, two ports
        state_a = _sessions.pop(sid_a)  # B's process cannot see A's sessions

        response = cb.post("/sessions")
        assert response.status_code == 200  # noqa: S101 pytest assertion
        swept = response.headers.get_list("set-cookie")
        assert not any(h.split("=", 1)[0] in a_cookies for h in swept), swept  # noqa: S101 pytest assertion
        assert {n: cb.cookies.get(n) for n in a_cookies} == a_cookies  # noqa: S101 pytest assertion

        _sessions[sid_a] = state_a
        ca.cookies.clear()
        for name, value in cb.cookies.items():
            ca.cookies.set(name, value)
        assert ca.get(f"/sessions/{sid_a}/log").status_code == 200  # noqa: S101 pytest assertion


def test_cli_web_port_per_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ports: list[int] = []

    def _fake_run(_app: Any, **kwargs: Any) -> None:
        ports.append(kwargs["port"])

    monkeypatch.setattr(cli, "create_app", lambda **_kw: "app")
    monkeypatch.setattr(cli.uvicorn, "run", _fake_run)
    runner = CliRunner()
    for root, port in ((tmp_path / "a", 8101), (tmp_path / "b", 8102)):
        result = runner.invoke(cli.app, ["web"], env=_env(root, port))
        assert result.exit_code == 0, result.output  # noqa: S101 pytest assertion
    assert ports == [8101, 8102]  # noqa: S101 pytest assertion
