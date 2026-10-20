"""Assemble the three Posit Connect deployment folders for the sales explorer.

    python scripts/package_connect.py [OUT_DIR]            (make connect-bundles; default dist/connect)
    python scripts/package_connect.py --from-git REV ...   (install shiny-mcp-host from GitHub instead)

Produces, needing neither Node nor a network build on the server:

    OUT_DIR/mcp-server/   server.py, view.built.html, requirements.txt (mcp only)   entry point server:app
    OUT_DIR/sandbox/      app.py, requirements.txt, shiny-mcp-host wheel             entry point app:app
    OUT_DIR/shiny-app/    app.py, requirements.txt, shiny-mcp-host wheel             entry point app:app

The Shiny app is examples/sales_explorer/app.py and the sandbox is
examples/posit_connect/sandbox/app.py, copied unchanged. requirements.txt pins
every dependency to the versions in uv.lock (what the test suite ran). By
default shiny-mcp-host itself is a wheel built from this checkout and copied
into the folder, because the package is not on PyPI. See docs/deployment.md.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from package_demo_server import package as package_mcp_server  # noqa: E402

REPO = "https://github.com/mattmalcher/mcp-app-shiny"
SOURCES = {
    "sandbox": ROOT / "examples/posit_connect/sandbox/app.py",
    "shiny-app": ROOT / "examples/sales_explorer/app.py",
}


def _run(cmd: list[str]) -> str:
    return subprocess.run(cmd, cwd=ROOT, check=True, capture_output=True, text=True).stdout


def pinned_requirements() -> list[str]:
    """Runtime dependencies of shiny-mcp-host, pinned as in uv.lock."""
    out = _run(
        [
            "uv",
            "export",
            "--frozen",
            "--no-dev",
            "--no-emit-project",
            "--no-hashes",
            "--no-header",
            "--no-annotate",
        ]
    )
    return [line for line in out.splitlines() if line.strip() and not line.startswith("#")]


def build_wheel() -> Path:
    out = Path(tempfile.mkdtemp(prefix="shiny-mcp-host-wheel-"))
    _run(["uv", "build", "--wheel", "--out-dir", str(out)])
    (wheel,) = out.glob("shiny_mcp_host-*.whl")
    return wheel


def package(out: Path, *, from_git: str | None = None) -> Path:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    package_mcp_server(out / "mcp-server")

    pins = pinned_requirements()
    if from_git:
        own = f"shiny-mcp-host @ git+{REPO}@{from_git}"
        wheel = None
    else:
        wheel = build_wheel()
        own = f"./{wheel.name}"
    for name, source in SOURCES.items():
        folder = out / name
        folder.mkdir()
        shutil.copy2(source, folder / "app.py")
        if wheel is not None:
            shutil.copy2(wheel, folder / wheel.name)
        (folder / "requirements.txt").write_text("\n".join([own, *pins]) + "\n", encoding="utf-8")
    revision = _run(["git", "rev-parse", "HEAD"]).strip()
    dirty = bool(_run(["git", "status", "--porcelain"]).strip())
    (out / "REVISION").write_text(
        f"{revision}{' (uncommitted changes)' if dirty else ''}\n", encoding="utf-8"
    )
    return out


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("out", nargs="?", type=Path, default=ROOT / "dist/connect")
    parser.add_argument(
        "--from-git",
        metavar="REV",
        help="reference shiny-mcp-host as git+https at this pushed commit/tag instead of bundling a wheel",
    )
    args = parser.parse_args()
    out = package(args.out, from_git=args.from_git)
    for folder in sorted(p for p in out.iterdir() if p.is_dir()):
        print(f"{folder.relative_to(out.parent)}/: {', '.join(sorted(p.name for p in folder.iterdir()))}")
    print(f"revision: {(out / 'REVISION').read_text().strip()}")


if __name__ == "__main__":
    main()
