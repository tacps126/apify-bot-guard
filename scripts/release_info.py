"""Release checks shared by CI and the release workflow.

python3 scripts/release_info.py check   # versions agree and the changelog has the version
python3 scripts/release_info.py version # print the version
python3 scripts/release_info.py notes   # print the changelog section for the version
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parents[1]


def versions() -> dict[str, str]:
    py = tomllib.loads((ROOT / "python/pyproject.toml").read_text())["project"]["version"]
    init = re.search(r'__version__ = "([^"]+)"', (ROOT / "python/apify_bot_guard/__init__.py").read_text())
    js = json.loads((ROOT / "js/package.json").read_text())["version"]
    return {
        "python/pyproject.toml": py,
        "python/apify_bot_guard/__init__.py": init.group(1) if init else "",
        "js/package.json": js,
    }


def notes(version: str) -> str | None:
    text = (ROOT / "CHANGELOG.md").read_text()
    m = re.search(rf"^## {re.escape(version)}\s*$(.*?)(?=^## |\Z)", text, re.M | re.S)
    return m.group(1).strip() if m else None


def main(cmd: str) -> int:
    vs = versions()
    if len(set(vs.values())) != 1:
        print("versions differ:", vs, file=sys.stderr)
        return 1
    version = next(iter(vs.values()))
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?", version):
        print(f"not a semantic version: {version}", file=sys.stderr)
        return 1
    body = notes(version)
    if cmd == "version":
        print(version)
    elif cmd == "notes":
        print(body or "")
    elif not body:
        print(f"CHANGELOG.md has no '## {version}' section", file=sys.stderr)
        return 1
    else:
        print(f"release {version}: versions agree, changelog ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "check"))
