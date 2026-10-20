from types import SimpleNamespace as NS

import pytest

from shiny_mcp_host import HostPolicy, PolicyError
from shiny_mcp_host.policy import sanitize_csp, sanitize_permissions, tool_visibility, validate_open_link

pytestmark = pytest.mark.security


def tool(visibility=None):
    ui = {"resourceUri": "ui://x"}
    if visibility is not None:
        ui["visibility"] = visibility
    return NS(meta={"ui": ui})


def check(policy, name, *, origin="show", t=tool(), view="s1", target="s1"):
    policy.check_app_tool_call(
        view_server=view, target_server=target, tool_name=name, origin_tool=origin, tool=t
    )


class TestAppToolPolicy:
    def test_origin_tool_allowed_by_default(self):
        check(HostPolicy(), "show", origin="show")

    def test_explicit_app_visibility_allowed(self):
        check(HostPolicy(), "refresh", t=tool(["app"]))

    def test_other_tool_without_explicit_visibility_rejected(self):
        with pytest.raises(PolicyError, match="not authorised"):
            check(HostPolicy(), "delete-everything", t=tool())

    def test_model_only_rejected_even_if_allowlisted(self):
        with pytest.raises(PolicyError, match="not visible to apps"):
            check(HostPolicy(app_tools={"s1": ["secret"]}), "secret", t=tool(["model"]))

    def test_cross_server_rejected(self):
        with pytest.raises(PolicyError, match="Cross-server"):
            check(HostPolicy(), "show", target="s2")

    def test_unknown_tool_rejected(self):
        with pytest.raises(PolicyError, match="Unknown"):
            check(HostPolicy(), "nope", t=None)

    def test_allowlist(self):
        policy = HostPolicy(app_tools={"s1": ["a"]})
        check(policy, "a", t=tool())
        with pytest.raises(PolicyError, match="allowlist"):
            check(policy, "show", origin="show")

    def test_visibility_default(self):
        assert tool_visibility(NS(meta=None)) == ["model", "app"]
        assert tool_visibility(tool(["app"])) == ["app"]
        assert tool_visibility(NS(meta={"ui": {"visibility": "app"}})) == ["model", "app"]


class TestCsp:
    declared = {
        "connectDomains": ["https://api.example.com", "javascript:alert(1)", "http://evil.example.com"],
        "resourceDomains": [
            "https://cdn.example.com",
            "https://x.example.com; script-src *",
            "https://*.tiles.example",
        ],
        "frameDomains": ["https://'unsafe-eval'"],
        "baseUriDomains": "https://notalist.example",
        "unknown": ["https://ignored.example"],
    }

    def test_default_denies_everything_declared(self):
        csp, rejected = sanitize_csp(self.declared, HostPolicy())
        assert csp == {}
        assert "https://api.example.com" in rejected

    def test_allowlist_keeps_only_approved(self):
        policy = HostPolicy(allowed_csp_domains=["https://api.example.com"])
        csp, rejected = sanitize_csp(self.declared, policy)
        assert csp == {"connectDomains": ["https://api.example.com"]}
        assert "https://cdn.example.com" in rejected

    def test_trust_declared_still_rejects_malformed_and_unsafe_schemes(self):
        csp, rejected = sanitize_csp(self.declared, HostPolicy(trust_declared_csp=True))
        assert csp == {
            "connectDomains": ["https://api.example.com"],
            "resourceDomains": ["https://cdn.example.com", "https://*.tiles.example"],
        }
        assert "javascript:alert(1)" in rejected
        assert "http://evil.example.com" in rejected  # plain http only for loopback
        assert "https://x.example.com; script-src *" in rejected

    def test_loopback_http_allowed(self):
        csp, _ = sanitize_csp(
            {"connectDomains": ["http://localhost:9000"]}, HostPolicy(trust_declared_csp=True)
        )
        assert csp == {"connectDomains": ["http://localhost:9000"]}

    def test_invalid_policy_domain(self):
        with pytest.raises(ValueError):
            HostPolicy(allowed_csp_domains=["*"])

    def test_non_mapping(self):
        assert sanitize_csp(None, HostPolicy()) == ({}, [])


def test_permissions():
    policy = HostPolicy(allowed_permissions=["clipboardWrite"])
    assert sanitize_permissions({"camera": {}, "clipboardWrite": {}, "bogus": {}}, policy) == {
        "clipboardWrite": {}
    }
    assert sanitize_permissions({"camera": {}}, HostPolicy()) == {}
    with pytest.raises(ValueError):
        HostPolicy(allowed_permissions=["usb"])


@pytest.mark.parametrize("url", ["https://example.com/", "http://localhost:3000/x?y=1"])
def test_open_link_ok(url):
    assert validate_open_link(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "file:///etc/passwd",
        "data:text/html,<p>",
        "//example.com",
        "",
        None,
        "https://" + "a" * 5000,
    ],
)
def test_open_link_rejected(url):
    with pytest.raises(PolicyError):
        validate_open_link(url)
