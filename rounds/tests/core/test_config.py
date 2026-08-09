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

    def test_empty_string_returns_empty_list(self) -> None:
        s = _settings(service_filter="")
        assert s.get_service_names() == []

    def test_single_service(self) -> None:
        s = _settings(service_filter="dr-cli")
        assert s.get_service_names() == ["dr-cli"]

    def test_comma_separated(self) -> None:
        s = _settings(service_filter="svc-a,svc-b,svc-c")
        assert s.get_service_names() == ["svc-a", "svc-b", "svc-c"]

    def test_comma_separated_with_spaces(self) -> None:
        s = _settings(service_filter=" svc-a , svc-b ")
        assert s.get_service_names() == ["svc-a", "svc-b"]

    def test_default_returns_empty_list(self) -> None:
        s = _settings()
        assert s.get_service_names() == []

    def test_whitespace_only_returns_empty_list(self) -> None:
        s = _settings(service_filter="  ")
        assert s.get_service_names() == []


class TestGetServiceHostMap:
    """Tests for Settings.get_service_host_map()."""

    def test_empty_string_returns_empty_dict(self) -> None:
        s = _settings(service_host_map="")
        assert s.get_service_host_map() == {}

    def test_json_string(self) -> None:
        s = _settings(service_host_map='{"my-api": "t5610", "worker": "petit-cochon"}')
        assert s.get_service_host_map() == {"my-api": "t5610", "worker": "petit-cochon"}

    def test_default_returns_empty_dict(self) -> None:
        s = _settings()
        assert s.get_service_host_map() == {}

    def test_json_array_raises_value_error_at_construction(self) -> None:
        with pytest.raises(Exception, match="SERVICE_HOST_MAP must be a JSON object"):
            _settings(service_host_map="[1, 2, 3]")

    def test_malformed_json_raises_error_at_construction(self) -> None:
        with pytest.raises(Exception, match="SERVICE_HOST_MAP must be valid JSON"):
            _settings(service_host_map="not json")

    def test_non_string_values_raise_value_error_at_construction(self) -> None:
        with pytest.raises(Exception, match="SERVICE_HOST_MAP values must be strings"):
            _settings(service_host_map='{"svc": 42}')


class TestGetServiceBudgetMap:
    """Tests for Settings.get_service_budget_map()."""

    def test_empty_string_returns_empty_dict(self) -> None:
        s = _settings(service_budget_map="")
        assert s.get_service_budget_map() == {}

    def test_default_returns_empty_dict(self) -> None:
        s = _settings()
        assert s.get_service_budget_map() == {}

    def test_json_string(self) -> None:
        s = _settings(service_budget_map='{"my-api": 25.0, "worker": 10}')
        assert s.get_service_budget_map() == {"my-api": 25.0, "worker": 10.0}

    def test_json_array_raises_value_error_at_construction(self) -> None:
        with pytest.raises(Exception, match="SERVICE_BUDGET_MAP must be a JSON object"):
            _settings(service_budget_map="[1, 2, 3]")

    def test_malformed_json_raises_error_at_construction(self) -> None:
        with pytest.raises(Exception, match="SERVICE_BUDGET_MAP must be valid JSON"):
            _settings(service_budget_map="not json")

    def test_negative_values_raise_value_error_at_construction(self) -> None:
        with pytest.raises(Exception, match="SERVICE_BUDGET_MAP values must be non-negative"):
            _settings(service_budget_map='{"my-api": -5.0}')

    def test_non_numeric_values_raise_value_error_at_construction(self) -> None:
        with pytest.raises(Exception, match="SERVICE_BUDGET_MAP values must be non-negative"):
            _settings(service_budget_map='{"my-api": "expensive"}')

    def test_boolean_values_raise_value_error_at_construction(self) -> None:
        with pytest.raises(Exception, match="SERVICE_BUDGET_MAP values must be non-negative"):
            _settings(service_budget_map='{"my-api": true}')

    def test_zero_is_a_valid_cap(self) -> None:
        s = _settings(service_budget_map='{"my-api": 0}')
        assert s.get_service_budget_map() == {"my-api": 0.0}


class TestGetAgentNodeServiceMap:
    """Tests for Settings.get_agent_node_service_map()."""

    def test_empty_string_returns_empty_dict(self) -> None:
        s = _settings(agent_node_service_map="")
        assert s.get_agent_node_service_map() == {}

    def test_default_returns_empty_dict(self) -> None:
        s = _settings()
        assert s.get_agent_node_service_map() == {}

    def test_single_entry(self) -> None:
        s = _settings(agent_node_service_map="my-api:node1:workspace-a")
        assert s.get_agent_node_service_map() == {"my-api": ("node1", "workspace-a")}

    def test_multiple_entries(self) -> None:
        s = _settings(
            agent_node_service_map="my-api:node1:workspace-a,worker:node2:workspace-b"
        )
        assert s.get_agent_node_service_map() == {
            "my-api": ("node1", "workspace-a"),
            "worker": ("node2", "workspace-b"),
        }

    def test_entries_with_surrounding_whitespace(self) -> None:
        s = _settings(
            agent_node_service_map=" my-api : node1 : workspace-a , worker:node2:workspace-b "
        )
        assert s.get_agent_node_service_map() == {
            "my-api": ("node1", "workspace-a"),
            "worker": ("node2", "workspace-b"),
        }

    def test_malformed_entry_too_few_components_raises_at_construction(self) -> None:
        with pytest.raises(Exception, match="AGENT_NODE_SERVICE_MAP entries must be"):
            _settings(agent_node_service_map="my-api:node1")

    def test_malformed_entry_too_many_components_raises_at_construction(self) -> None:
        with pytest.raises(Exception, match="AGENT_NODE_SERVICE_MAP entries must be"):
            _settings(agent_node_service_map="my-api:node1:workspace-a:extra")

    def test_malformed_entry_empty_component_raises_at_construction(self) -> None:
        with pytest.raises(Exception, match="AGENT_NODE_SERVICE_MAP entries must be"):
            _settings(agent_node_service_map="my-api::workspace-a")

    def test_one_bad_entry_among_valid_ones_raises_at_construction(self) -> None:
        with pytest.raises(Exception, match="AGENT_NODE_SERVICE_MAP entries must be"):
            _settings(agent_node_service_map="my-api:node1:workspace-a,bad-entry")


class TestGetAgentNodeHostMap:
    """Tests for Settings.get_agent_node_host_map()."""

    def test_empty_string_returns_empty_dict(self) -> None:
        s = _settings(agent_node_host_map="")
        assert s.get_agent_node_host_map() == {}

    def test_json_string(self) -> None:
        s = _settings(agent_node_host_map='{"node1": "host-a", "node2": "host-b"}')
        assert s.get_agent_node_host_map() == {"node1": "host-a", "node2": "host-b"}

    def test_default_returns_empty_dict(self) -> None:
        s = _settings()
        assert s.get_agent_node_host_map() == {}

    def test_json_array_raises_value_error_at_construction(self) -> None:
        with pytest.raises(Exception, match="AGENT_NODE_HOST_MAP must be a JSON object"):
            _settings(agent_node_host_map="[1, 2, 3]")

    def test_malformed_json_raises_error_at_construction(self) -> None:
        with pytest.raises(Exception, match="AGENT_NODE_HOST_MAP must be valid JSON"):
            _settings(agent_node_host_map="not json")

    def test_non_string_values_raise_value_error_at_construction(self) -> None:
        with pytest.raises(Exception, match="AGENT_NODE_HOST_MAP values must be strings"):
            _settings(agent_node_host_map='{"node1": 42}')


class TestGetEffectiveServiceMap:
    """Tests for Settings.get_effective_service_map() (BA Req 5 precedence rule)."""

    def test_empty_maps_return_empty_dict(self) -> None:
        s = _settings(service_host_map="", agent_node_service_map="")
        assert s.get_effective_service_map() == {}

    def test_service_host_map_only_uses_host_as_mcp_key_and_codebase_path_as_workspace(
        self,
    ) -> None:
        s = _settings(
            service_host_map='{"my-api": "t5610"}',
            codebase_path="/srv/target",
        )
        assert s.get_effective_service_map() == {"my-api": ("t5610", "/srv/target")}

    def test_agent_node_service_map_only_is_passed_through(self) -> None:
        s = _settings(agent_node_service_map="my-api:node1:workspace-a")
        assert s.get_effective_service_map() == {"my-api": ("node1", "workspace-a")}

    def test_no_overlap_merges_both_maps(self) -> None:
        s = _settings(
            service_host_map='{"worker": "petit-cochon"}',
            agent_node_service_map="my-api:node1:workspace-a",
            codebase_path="/srv/target",
        )
        assert s.get_effective_service_map() == {
            "worker": ("petit-cochon", "/srv/target"),
            "my-api": ("node1", "workspace-a"),
        }

    def test_overlap_agent_node_service_map_takes_precedence(self) -> None:
        s = _settings(
            service_host_map='{"my-api": "t5610"}',
            agent_node_service_map="my-api:node1:workspace-a",
            codebase_path="/srv/target",
        )
        assert s.get_effective_service_map() == {"my-api": ("node1", "workspace-a")}


class TestGetEffectiveAgentNodeHostMap:
    """Tests for Settings.get_effective_agent_node_host_map()."""

    def test_empty_maps_return_empty_dict(self) -> None:
        s = _settings(service_host_map="", agent_node_host_map="")
        assert s.get_effective_agent_node_host_map() == {}

    def test_service_host_map_hosts_become_identity_entries(self) -> None:
        s = _settings(service_host_map='{"my-api": "t5610"}')
        assert s.get_effective_agent_node_host_map() == {"t5610": "t5610"}

    def test_agent_node_host_map_entries_are_included(self) -> None:
        s = _settings(agent_node_host_map='{"node1": "host-a"}')
        assert s.get_effective_agent_node_host_map() == {"node1": "host-a"}

    def test_explicit_agent_node_host_map_entry_overrides_identity_entry(self) -> None:
        s = _settings(
            service_host_map='{"my-api": "t5610"}',
            agent_node_host_map='{"t5610": "renamed-host"}',
        )
        assert s.get_effective_agent_node_host_map() == {"t5610": "renamed-host"}


class TestAgentNodeBudget:
    """Tests for Settings.agent_node_budget_usd validation."""

    def test_default_budget(self) -> None:
        s = _settings()
        assert s.agent_node_budget_usd == 2.0

    def test_negative_budget_raises_at_construction(self) -> None:
        with pytest.raises(Exception, match="agent_node_budget_usd must be non-negative"):
            _settings(agent_node_budget_usd=-1.0)


class TestValidateBackendDependencies:
    """Tests for the agent_node branch of Settings.validate_backend_dependencies()."""

    def test_agent_node_backend_requires_service_map(self) -> None:
        with pytest.raises(Exception, match="agent_node_service_map must be set"):
            Settings(  # type: ignore[call-arg]
                _env_file=None,
                diagnosis_backend="agent_node",
                agent_node_service_map="",
            )

    def test_agent_node_backend_requires_host_map(self) -> None:
        with pytest.raises(Exception, match="agent_node_host_map must be set"):
            Settings(  # type: ignore[call-arg]
                _env_file=None,
                diagnosis_backend="agent_node",
                agent_node_service_map="my-api:node1:workspace-a",
                agent_node_host_map="",
                openai_api_key="sk-test",
            )

    def test_agent_node_backend_requires_openai_api_key_for_fallback(self) -> None:
        with pytest.raises(Exception, match="openai_api_key must be set"):
            Settings(  # type: ignore[call-arg]
                _env_file=None,
                diagnosis_backend="agent_node",
                agent_node_service_map="my-api:node1:workspace-a",
                agent_node_host_map='{"node1": "host-a"}',
                openai_api_key="",
            )

    def test_agent_node_backend_with_full_config_succeeds(self) -> None:
        s = _settings(
            diagnosis_backend="agent_node",
            agent_node_service_map="my-api:node1:workspace-a",
            agent_node_host_map='{"node1": "host-a"}',
        )
        assert s.get_agent_node_service_map() == {"my-api": ("node1", "workspace-a")}
        assert s.get_agent_node_host_map() == {"node1": "host-a"}

    def test_github_issue_backend_requires_github_token(self) -> None:
        with pytest.raises(Exception, match="github_token must be set"):
            _settings(
                notification_backend="github_issue",
                github_token="",
                github_repo_owner="myorg",
                service_repo_map='{"my-api": "myorg/my-api"}',
            )

    def test_github_issue_backend_requires_github_repo_owner(self) -> None:
        with pytest.raises(Exception, match="github_repo_owner must be set"):
            _settings(
                notification_backend="github_issue",
                github_token="ghp_test",
                github_repo_owner="",
                service_repo_map='{"my-api": "myorg/my-api"}',
            )

    def test_github_issue_backend_requires_service_repo_map(self) -> None:
        with pytest.raises(Exception, match="service_repo_map must be set"):
            _settings(
                notification_backend="github_issue",
                github_token="ghp_test",
                github_repo_owner="myorg",
                service_repo_map="",
            )

    def test_github_issue_backend_with_documented_config_succeeds(self) -> None:
        """Mirrors the env vars .env.rounds.template documents for github_issue:
        GITHUB_TOKEN, GITHUB_REPO_OWNER, and SERVICE_REPO_MAP, with no GITHUB_REPO.
        """
        s = _settings(
            notification_backend="github_issue",
            github_token="ghp_test",
            github_repo_owner="myorg",
            service_repo_map='{"my-api": "myorg/my-api"}',
        )
        assert s.notification_backend == "github_issue"


class TestGetServiceRepoMap:
    """Tests for Settings.get_service_repo_map()."""

    def test_empty_string_returns_empty_dict(self) -> None:
        s = _settings(service_repo_map="")
        assert s.get_service_repo_map() == {}

    def test_default_returns_empty_dict(self) -> None:
        s = _settings()
        assert s.get_service_repo_map() == {}

    def test_json_string(self) -> None:
        s = _settings(
            service_repo_map='{"my-api": "myorg/my-api", "worker": "myorg/worker-svc"}'
        )
        assert s.get_service_repo_map() == {
            "my-api": "myorg/my-api",
            "worker": "myorg/worker-svc",
        }

    def test_json_array_raises_value_error_at_construction(self) -> None:
        with pytest.raises(Exception, match="SERVICE_REPO_MAP must be a JSON object"):
            _settings(service_repo_map="[1, 2, 3]")

    def test_malformed_json_raises_error_at_construction(self) -> None:
        with pytest.raises(Exception, match="SERVICE_REPO_MAP must be valid JSON"):
            _settings(service_repo_map="not json")

    def test_non_owner_repo_shaped_values_raise_value_error_at_construction(self) -> None:
        with pytest.raises(Exception, match="SERVICE_REPO_MAP values must be"):
            _settings(service_repo_map='{"my-api": "not-owner-slash-repo"}')

    def test_non_string_values_raise_value_error_at_construction(self) -> None:
        with pytest.raises(Exception, match="SERVICE_REPO_MAP values must be"):
            _settings(service_repo_map='{"my-api": 42}')


class TestPhoneHomeSettings:
    """Tests for phone-home configuration fields and validation."""

    def test_defaults(self) -> None:
        s = _settings()
        assert s.phone_home_endpoint_url == ""
        assert s.phone_home_auth_token.get_secret_value() == ""
        assert s.get_phone_home_severity_gate() == ["ERROR", "FATAL"]
        assert s.phone_home_cooldown_hours == 24

    def test_severity_gate_parses_comma_separated_levels(self) -> None:
        s = _settings(phone_home_severity_gate="warn, error , fatal")
        assert s.get_phone_home_severity_gate() == ["WARN", "ERROR", "FATAL"]

    def test_empty_severity_gate_disables_phone_home(self) -> None:
        s = _settings(phone_home_severity_gate="")
        assert s.get_phone_home_severity_gate() == []

    def test_invalid_severity_gate_level_raises_at_construction(self) -> None:
        with pytest.raises(Exception, match="invalid severity levels"):
            _settings(phone_home_severity_gate="ERROR,CATASTROPHIC")

    def test_non_positive_cooldown_raises_at_construction(self) -> None:
        with pytest.raises(Exception, match="phone_home_cooldown_hours must be positive"):
            _settings(phone_home_cooldown_hours=0)
