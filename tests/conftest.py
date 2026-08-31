"""Shared fixtures for the Hermes Conversation test suite."""

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load custom_components/ for every test."""
    return
