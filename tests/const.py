"""Shared constants for the test suite."""

BASE_URL = "http://hermes.test:8642"
PROFILE = "home-assist"
API_KEY = "sk-test-key"
MODELS_URL = f"{BASE_URL}/p/{PROFILE}/v1/models"
COMPLETIONS_URL = f"{BASE_URL}/p/{PROFILE}/v1/chat/completions"
DEFAULT_MODELS = {"data": [{"id": "hermes-agent"}, {"id": "home-assist"}]}
