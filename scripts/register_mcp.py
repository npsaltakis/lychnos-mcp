"""Register the Smart Home MCP server with Claude Code and Claude Desktop (Windows, macOS, Linux).

Run it with the project's virtual-environment Python, so the right interpreter path is used:

    Windows:  .venv\\Scripts\\python.exe scripts\\register_mcp.py
    Linux:    .venv/bin/python scripts/register_mcp.py

What it does:
- writes ``.mcp.json`` in the project root (Claude Code, project scope; machine-specific, git-ignored)
- adds/updates ``smart-home`` in the Claude Desktop config if Claude Desktop is installed
  (a timestamped backup of the original file is kept next to it)
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = {"command": sys.executable, "args": ["-m", "smarthome_mcp"]}


def desktop_config_path() -> Path | None:
    system = platform.system()
    if system == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif system == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    directory = base / "Claude"
    return directory / "claude_desktop_config.json" if directory.is_dir() else None


def check_environment() -> None:
    if sys.prefix == sys.base_prefix:
        sys.exit("Run this with the project's virtual-environment Python (.venv), not the system Python.")
    try:
        import smarthome_mcp  # noqa: F401
    except ImportError:
        sys.exit('smarthome_mcp is not installed in this environment. Run: pip install -e "mcp_server[dev]"')


def write_claude_code_config() -> Path:
    path = ROOT / ".mcp.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    data.setdefault("mcpServers", {})["smart-home"] = {"type": "stdio", **SERVER}
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def write_claude_desktop_config() -> Path | None:
    path = desktop_config_path()
    if path is None:
        return None
    data = {}
    if path.exists():
        backup = path.with_name(f"{path.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(path, backup)
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    data.setdefault("mcpServers", {})["smart-home"] = SERVER
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def main() -> None:
    check_environment()
    print(f"MCP server command: {sys.executable} -m smarthome_mcp")
    print(f"Claude Code:    {write_claude_code_config()}  (open the project in Claude Code and approve 'smart-home')")
    desktop = write_claude_desktop_config()
    if desktop:
        print(f"Claude Desktop: {desktop}  (restart Claude Desktop completely)")
    else:
        print("Claude Desktop: not found on this machine, skipped")
    # When this script runs from inside the Claude desktop app on Windows (MSIX package), writes to
    # AppData are redirected to a private copy that Claude Desktop does not read. Print the snippet
    # so it can be pasted via Claude Desktop → Settings → Developer → Edit config.
    print("\nIf Claude Desktop → Settings → Developer still shows 'No servers added', open 'Edit config'")
    print('and add this inside the top-level { } of claude_desktop_config.json:\n')
    print(json.dumps({"mcpServers": {"smart-home": SERVER}}, indent=2)[1:-1].strip() + ",")


if __name__ == "__main__":
    main()
