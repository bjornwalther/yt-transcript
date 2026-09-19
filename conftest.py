"""Shared pytest configuration.

- Resets the process-wide client health state around every test.
- Live tests (marker `live`) call the real YouTube and are skipped unless the run
  is started with --live. Run them rarely: every request counts against the
  caller's IP reputation.
"""

import pytest

import innertube


def pytest_addoption(parser):
    parser.addoption("--live", action="store_true", default=False,
                     help="run tests that call the real YouTube (slow, rate-limited)")


def pytest_configure(config):
    config.addinivalue_line("markers", "live: calls the real YouTube; opt in with --live")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--live"):
        return
    skip_live = pytest.mark.skip(reason="live test: run with --live")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


@pytest.fixture(autouse=True)
def _fresh_client_health():
    innertube.HEALTH.reset()
    yield
    innertube.HEALTH.reset()
