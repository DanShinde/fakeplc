"""pytest plugin: the ``fake_plc`` and ``fake_plc_factory`` fixtures.

Choose the configuration with, in order of priority:

* ``@pytest.mark.fakeplc("plc.yaml")`` on a test, class or module
  (a path relative to the test file, or a dict),
* ``--fakeplc plc.yaml`` on the command line,
* ``fakeplc_config = plc.yaml`` in the pytest ini file (relative to rootdir).

One PLC per configuration is started for the whole session and reset
before every test that uses it. Ports are picked free, unless the marker
says ``keep_ports=True``. Scans only run when a test calls ``scan()``,
unless the marker says ``autoscan=True``.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from .plc import FakePLC

_POOL_KEY = pytest.StashKey[dict[str, FakePLC]]()


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("fakeplc")
    group.addoption(
        "--fakeplc",
        dest="fakeplc_config",
        default=None,
        metavar="FILE",
        help="YAML file for the fake_plc fixture",
    )
    parser.addini("fakeplc_config", "YAML file for the fake_plc fixture, relative to rootdir", default="")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "fakeplc(config, *, autoscan=False, keep_ports=False): configuration for the fake_plc fixture",
    )
    config.stash[_POOL_KEY] = {}


def pytest_unconfigure(config: pytest.Config) -> None:
    pool = config.stash.get(_POOL_KEY, {})
    for plc in pool.values():
        plc.stop()
    pool.clear()


def _resolve(source: Any, base: Path, rootdir: Path) -> Any:
    if isinstance(source, dict) or (isinstance(source, str) and "\n" in source):
        return source
    path = Path(source)
    if path.is_absolute():
        return path
    for candidate in (base / path, rootdir / path):
        if candidate.exists():
            return candidate.resolve()
    return (rootdir / path).resolve()


def _start(source: Any, autoscan: bool, keep_ports: bool) -> FakePLC:
    kwargs: dict[str, Any] = {"autoscan": autoscan}
    if not keep_ports:
        kwargs.update(opcua_port=0, modbus_port=0)
    return FakePLC(source, **kwargs).start()


@pytest.fixture
def fake_plc(request: pytest.FixtureRequest) -> Iterator[FakePLC]:
    """A running :class:`~fakeplc.FakePLC`, reset to its start values."""
    marker = request.node.get_closest_marker("fakeplc")
    rootdir = Path(str(request.config.rootpath))
    options: dict[str, Any] = {}
    if marker is not None and marker.args:
        source = _resolve(marker.args[0], Path(str(request.path)).parent, rootdir)
        options = dict(marker.kwargs)
    else:
        raw = request.config.getoption("fakeplc_config") or request.config.getini("fakeplc_config")
        if not raw:
            pytest.fail(
                "fake_plc needs a configuration: add @pytest.mark.fakeplc('plc.yaml'), "
                "pass --fakeplc plc.yaml, or set fakeplc_config in the pytest ini file",
                pytrace=False,
            )
        source = _resolve(raw, Path.cwd(), rootdir)
        if marker is not None:
            options = dict(marker.kwargs)
    unknown = set(options) - {"autoscan", "keep_ports"}
    if unknown:
        pytest.fail(f"unknown fakeplc marker option {sorted(unknown)[0]!r}", pytrace=False)
    autoscan = bool(options.get("autoscan", False))
    keep_ports = bool(options.get("keep_ports", False))
    key = json.dumps([source, autoscan, keep_ports], sort_keys=True, default=str)
    pool = request.config.stash[_POOL_KEY]
    plc = pool.get(key)
    if plc is None:
        plc = pool[key] = _start(source, autoscan, keep_ports)
    else:
        plc.reset()
    yield plc


@pytest.fixture
def fake_plc_factory() -> Iterator[Callable[..., FakePLC]]:
    """Start extra PLCs for one test: ``fake_plc_factory("other.yaml")``.

    Keyword arguments go to :class:`~fakeplc.FakePLC`; ports default to free
    ones and scans to manual. Every PLC is stopped when the test ends.
    """
    started: list[FakePLC] = []

    def make(config: Any, **kwargs: Any) -> FakePLC:
        kwargs.setdefault("autoscan", False)
        kwargs.setdefault("opcua_port", 0)
        kwargs.setdefault("modbus_port", 0)
        plc = FakePLC(config, **kwargs).start()
        started.append(plc)
        return plc

    yield make
    for plc in started:
        plc.stop()
