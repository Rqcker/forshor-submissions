import pytest


def pytest_addoption(parser):
    parser.addoption("--runslow", action="store_true", help="run tests that price policies with the vendored engine")


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: prices policies with the engine (about a minute or more)")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--runslow"):
        return
    skip = pytest.mark.skip(reason="needs the engine; run with --runslow")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip)
