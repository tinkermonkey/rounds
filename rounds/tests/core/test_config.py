"""Tests for Settings config field parsing."""

from typing import Any

import pytest

from rounds.config import Settings


def _settings(**kwargs: Any) -> Settings:
    """Create a Settings instance isolated from any .env file."""
    defaults: dict[str, Any] = {
        "diagnosis_backend": "openai",
        "openai_api_key": "sk-test",
    }
    defaults.update(kwargs)
    return Settings(_env_file=None, **defaults)  # type: ignore[call-arg]


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


class TestGetAgentNodeServiceMap:
    """Tests for Settings.get_agent_node_service_map()."""

    def test_empty_string_returns_empty_dict(self):
        s = _settings(agent_node_service_map="")
        assert s.get_agent_node_service_map() == {}

    def test_default_returns_empty_dict(self):
        s = _settings()
        assert s.get_agent_node_service_map() == {}

    def test_single_entry(self):
        s = _settings(agent_node_service_map="my-api:node1:workspace-a")
        assert s.get_agent_node_service_map() == {"my-api": ("node1", "workspace-a")}

    def test_multiple_entries(self):
        s = _settings(
            agent_node_service_map="my-api:node1:workspace-a,worker:node2:workspace-b"
        )
        assert s.get_agent_node_service_map() == {
            "my-api": ("node1", "workspace-a"),
            "worker": ("node2", "workspace-b"),
        }

    def test_entries_with_surrounding_whitespace(self):
        s = _settings(
            agent_node_service_map=" my-api : node1 : workspace-a , worker:node2:workspace-b "
        )
        assert s.get_agent_node_service_map() == {
            "my-api": ("node1", "workspace-a"),
            "worker": ("node2", "workspace-b"),
        }

    def test_malformed_entry_too_few_components_raises_at_construction(self):
        with pytest.raises(Exception, match="AGENT_NODE_SERVICE_MAP entries must be"):
            _settings(agent_node_service_map="my-api:node1")

    def test_malformed_entry_too_many_components_raises_at_construction(self):
        with pytest.raises(Exception, match="AGENT_NODE_SERVICE_MAP entries must be"):
            _settings(agent_node_service_map="my-api:node1:workspace-a:extra")

    def test_malformed_entry_empty_component_raises_at_construction(self):
        with pytest.raises(Exception, match="AGENT_NODE_SERVICE_MAP entries must be"):
            _settings(agent_node_service_map="my-api::workspace-a")

    def test_one_bad_entry_among_valid_ones_raises_at_construction(self):
        with pytest.raises(Exception, match="AGENT_NODE_SERVICE_MAP entries must be"):
            _settings(agent_node_service_map="my-api:node1:workspace-a,bad-entry")


class TestValidateBackendDependencies:
    """Tests for the agent_node branch of Settings.validate_backend_dependencies()."""

    def test_agent_node_backend_requires_service_map(self):
        with pytest.raises(Exception, match="agent_node_service_map must be set"):
            Settings(  # type: ignore[call-arg]
                _env_file=None,
                diagnosis_backend="agent_node",
                agent_node_service_map="",
            )

    def test_agent_node_backend_with_service_map_succeeds(self):
        s = _settings(
            diagnosis_backend="agent_node",
            agent_node_service_map="my-api:node1:workspace-a",
        )
        assert s.get_agent_node_service_map() == {"my-api": ("node1", "workspace-a")}
