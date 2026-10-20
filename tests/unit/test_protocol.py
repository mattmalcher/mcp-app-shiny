import base64
from types import SimpleNamespace as NS

import pytest

from shiny_mcp_host.errors import MCPHostError, ResourceError
from shiny_mcp_host.protocol import (
    RESOURCE_MIME_TYPE,
    extract_ui_html,
    get_tool_ui_resource_uri,
    parse_envelope,
    to_wire,
)

URI = "ui://demo/app.html"


def content(**kw):
    base = {"uri": URI, "mime_type": RESOURCE_MIME_TYPE, "text": "<p>hi</p>", "blob": None, "meta": None}
    base.update(kw)
    return NS(**base)


class TestToolUiResourceUri:
    def test_nested_meta(self):
        assert get_tool_ui_resource_uri({"_meta": {"ui": {"resourceUri": URI}}}) == URI

    def test_deprecated_flat_key(self):
        assert get_tool_ui_resource_uri({"_meta": {"ui/resourceUri": URI}}) == URI

    def test_nested_wins_over_flat(self):
        tool = {"_meta": {"ui": {"resourceUri": URI}, "ui/resourceUri": "ui://other"}}
        assert get_tool_ui_resource_uri(tool) == URI

    def test_sdk_tool_object(self):
        assert get_tool_ui_resource_uri(NS(meta={"ui": {"resourceUri": URI}})) == URI

    @pytest.mark.parametrize("tool", [{}, {"_meta": None}, {"_meta": {"ui": {}}}, NS(meta=None)])
    def test_absent(self, tool):
        assert get_tool_ui_resource_uri(tool) is None

    @pytest.mark.parametrize("bad", ["https://evil.example/app.html", "javascript:alert(1)", 42])
    def test_invalid_uri_raises(self, bad):
        with pytest.raises(ResourceError):
            get_tool_ui_resource_uri({"_meta": {"ui": {"resourceUri": bad}}})

    def test_unknown_optional_metadata_is_ignored(self):
        tool = {"_meta": {"ui": {"resourceUri": URI, "futureField": {"x": 1}}, "vendor/x": 1}}
        assert get_tool_ui_resource_uri(tool) == URI


class TestExtractUiHtml:
    def test_text(self):
        html, meta = extract_ui_html(NS(contents=[content(meta={"ui": {"prefersBorder": True}})]), expected_uri=URI, max_bytes=100)
        assert html == "<p>hi</p>" and meta == {"prefersBorder": True}

    def test_blob(self):
        blob = base64.b64encode(b"<p>blob</p>").decode()
        html, _ = extract_ui_html(NS(contents=[content(text=None, blob=blob)]), expected_uri=URI, max_bytes=100)
        assert html == "<p>blob</p>"

    def test_invalid_blob(self):
        with pytest.raises(ResourceError, match="base64"):
            extract_ui_html(NS(contents=[content(text=None, blob="!!!")]), expected_uri=URI, max_bytes=100)

    def test_wrong_mime(self):
        with pytest.raises(ResourceError, match="MIME"):
            extract_ui_html(NS(contents=[content(mime_type="text/html")]), expected_uri=URI, max_bytes=100)

    def test_uri_mismatch_prevents_cross_contamination(self):
        with pytest.raises(ResourceError, match="mismatch"):
            extract_ui_html(NS(contents=[content(uri="ui://other/app.html")]), expected_uri=URI, max_bytes=100)

    @pytest.mark.parametrize("contents", [[], [content(), content()], None])
    def test_content_count(self, contents):
        with pytest.raises(ResourceError, match="exactly one"):
            extract_ui_html(NS(contents=contents), expected_uri=URI, max_bytes=100)

    def test_size_limit_text(self):
        with pytest.raises(ResourceError, match="exceeds"):
            extract_ui_html(NS(contents=[content(text="x" * 101)]), expected_uri=URI, max_bytes=100)

    def test_size_limit_blob(self):
        blob = base64.b64encode(b"x" * 1000).decode()
        with pytest.raises(ResourceError, match="exceeds"):
            extract_ui_html(NS(contents=[content(text=None, blob=blob)]), expected_uri=URI, max_bytes=100)

    def test_empty(self):
        with pytest.raises(ResourceError, match="empty"):
            extract_ui_html(NS(contents=[content(text="  ")]), expected_uri=URI, max_bytes=100)


def env(**kw):
    base = {"v": 1, "kind": "tools/call", "widget_id": "w1", "mount_id": "abc", "request_id": "abc.1", "render_id": 1, "payload": {}}
    base.update(kw)
    return base


class TestEnvelope:
    def test_valid(self):
        e = parse_envelope(env(payload={"params": {"name": "x", "arguments": {"a": (1, 2)}}}))
        assert e.kind == "tools/call" and e.payload["params"]["arguments"]["a"] == [1, 2]

    @pytest.mark.parametrize(
        "patch",
        [
            {"v": 2},
            {"kind": "eval"},
            {"widget_id": "<script>"},
            {"widget_id": ""},
            {"mount_id": None},
            {"request_id": "x" * 500},
            {"render_id": "1"},
            {"render_id": True},
            {"payload": []},
        ],
    )
    def test_invalid(self, patch):
        with pytest.raises(MCPHostError):
            parse_envelope(env(**patch))

    def test_not_a_mapping(self):
        with pytest.raises(MCPHostError):
            parse_envelope("nope")


def test_to_wire_strips_sdk_internal_fields():
    from mcp.types import CallToolResult, TextContent

    wire = to_wire(CallToolResult(content=[TextContent(type="text", text="t")], structured_content={"a": 1}))
    assert wire == {"content": [{"type": "text", "text": "t"}], "structuredContent": {"a": 1}, "isError": False}
