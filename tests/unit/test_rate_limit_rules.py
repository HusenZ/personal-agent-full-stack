import pytest
from fastapi import Request

from app.api.rate_limiting import (
    ANALYSIS_PER_USER,
    LOGIN_PER_ACCOUNT,
    LOGIN_PER_IP,
    REGISTER_PER_IP,
    client_address,
)
from app.infrastructure.cache.rate_limiter import RateLimitRule


def build_request(client_host: str | None, headers: dict[str, str] | None = None) -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/",
        "headers": [
            (key.lower().encode(), value.encode()) for key, value in (headers or {}).items()
        ],
        "client": (client_host, 1234) if client_host else None,
    }
    return Request(scope)


class TestRuleValidation:
    @pytest.mark.parametrize("kwargs", [{"limit": 0}, {"window_seconds": 0}])
    def test_incoherent_rules_are_rejected(self, kwargs: dict[str, int]):
        defaults = {"name": "probe", "limit": 5, "window_seconds": 60}

        with pytest.raises(ValueError):
            RateLimitRule(**(defaults | kwargs))  # type: ignore[arg-type]

    def test_rule_names_are_distinct(self):
        """Two rules sharing a name would share a counter and limit each other."""
        rules = [REGISTER_PER_IP, LOGIN_PER_IP, LOGIN_PER_ACCOUNT, ANALYSIS_PER_USER]

        assert len({rule.name for rule in rules}) == len(rules)


class TestClientAddress:
    def test_the_socket_address_is_used_by_default(self):
        request = build_request("203.0.113.5")

        assert client_address(request, trust_proxy_headers=False) == "203.0.113.5"

    def test_a_forwarded_header_is_ignored_when_proxies_are_not_trusted(self):
        """The header is caller-controlled; honouring it would make the limit optional."""
        request = build_request("203.0.113.5", {"X-Forwarded-For": "1.1.1.1"})

        assert client_address(request, trust_proxy_headers=False) == "203.0.113.5"

    def test_a_forwarded_header_is_used_when_proxies_are_trusted(self):
        request = build_request("10.0.0.1", {"X-Forwarded-For": "203.0.113.5"})

        assert client_address(request, trust_proxy_headers=True) == "203.0.113.5"

    def test_the_original_client_is_taken_from_a_forwarded_chain(self):
        request = build_request("10.0.0.1", {"X-Forwarded-For": "203.0.113.5, 10.0.0.2"})

        assert client_address(request, trust_proxy_headers=True) == "203.0.113.5"

    def test_a_missing_client_does_not_raise(self):
        assert client_address(build_request(None), trust_proxy_headers=False) == "unknown"

    def test_trusted_proxies_fall_back_when_the_header_is_absent(self):
        request = build_request("10.0.0.1")

        assert client_address(request, trust_proxy_headers=True) == "10.0.0.1"
