import subprocess
import sys

from shiny_mcp_host.view import SDK_VERSION, app_html, sdk_script


def test_vendored_sdk_is_the_official_build_exposed_as_a_global():
    js = sdk_script()
    assert js.startswith(f"/*! @modelcontextprotocol/ext-apps@{SDK_VERSION} ")
    assert js.rstrip().endswith("};") and "globalThis.McpApps={" in js
    assert "App:" in js.rsplit("globalThis.McpApps=", 1)[1]
    code = js.split("*/\n", 1)[1]  # after the vendoring header comment
    assert "export{" not in code and "export {" not in code


def test_app_html_inlines_sdk_then_user_script():
    html = app_html(
        body="<p id=x></p>", script="console.log(McpApps.App)", title="A & B", head="<style></style>"
    )
    assert html.startswith("<!doctype html>") and "<title>A &amp; B</title>" in html
    sdk_at = html.index("globalThis.McpApps=")
    user_at = html.index("console.log(McpApps.App)")
    assert sdk_at < user_at
    assert html.count('<script type="module">') == 2
    assert "src=" not in html  # nothing fetched from the network


def test_script_cannot_close_its_own_element():
    html = app_html(body="", script='const s = "</script><img src=x onerror=alert(1)>";')
    assert "</script><img" not in html
    assert html.count("</script>") == 2


def test_view_module_does_not_import_shiny():
    code = "import sys, shiny_mcp_host.view; print(any(m.split('.')[0] == 'shiny' for m in sys.modules))"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert out.strip() == "False"
