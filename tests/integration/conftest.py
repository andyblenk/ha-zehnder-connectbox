"""Fixtures of the Home Assistant test harness for the setup tests."""

import sys
from pathlib import Path

import pytest

# The offline tests' helpers build the gateway messages used here.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load the integration from custom_components."""
    return
