"""Tests for Settings config field parsing."""

from typing import Any

import pytest

from rounds.config import Settings


def _settings(**kwargs: Any) -> Settings:
    """Create a Settings instance isolated from any .env file."""
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        diagnosis_backend="openai",
        openai_api_key="sk-test",
        **kwargs,
    )


class TestGetServiceNames:
    """Tests for Settings.get_service_names()."""

    def test_empty_string_returns_empty_list(self):
        s = _settings(service_filter="")
        assert s.get_service_names() == []

    def test_single_service(self):
        s = _settings(service_filter="dr-cli")
        assert s.get_service_names() == ["dr-cli"]

    def test_comma_separated(self):
        s = _settings(service_filter="svc-a,svc-b,svc-c")
        assert s.get_service_names() == ["svc-a", "svc-b", "svc-c"]

    def test_comma_separated_with_spaces(self):
        s = _settings(service_filter=" svc-a , svc-b ")
        assert s.get_service_names() == ["svc-a", "svc-b"]

    def test_default_returns_empty_list(self):
        s = _settings()
        assert s.get_service_names() == []

    def test_whitespace_only_returns_empty_list(self):
        s = _settings(service_filter="  ")
        assert s.get_service_names() == []


class TestGetServiceHostMap:
    """Tests for Settings.get_service_host_map()."""

    def test_empty_string_returns_empty_dict(self):
        s = _settings(service_host_map="")
        assert s.get_service_host_map() == {}

    def test_json_string(self):
        s = _settings(service_host_map='{"my-api": "t5610", "worker": "petit-cochon"}')
        assert s.get_service_host_map() == {"my-api": "t5610", "worker": "petit-cochon"}

    def test_default_returns_empty_dict(self):
        s = _settings()
        assert s.get_service_host_map() == {}

    def test_json_array_raises_value_error_at_construction(self):
        with pytest.raises(Exception, match="SERVICE_HOST_MAP must be a JSON object"):
            _settings(service_host_map="[1, 2, 3]")

    def test_malformed_json_raises_error_at_construction(self):
        with pytest.raises(Exception, match="SERVICE_HOST_MAP must be valid JSON"):
            _settings(service_host_map="not json")

    def test_non_string_values_raise_value_error_at_construction(self):
        with pytest.raises(Exception, match="SERVICE_HOST_MAP values must be strings"):
            _settings(service_host_map='{"svc": 42}')
