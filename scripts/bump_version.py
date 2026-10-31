"""バージョン番号を更新する

使い方:
    uv run python scripts/bump_version.py patch
    uv run python scripts/bump_version.py minor
    uv run python scripts/bump_version.py 1.2.3
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
INIT = ROOT / "src" / "ttp" / "__init__.py"

_PATTERN = re.compile(r'^version = "(\d+)\.(\d+)\.(\d+)"$', re.MULTILINE)


def read_version() -> tuple[int, int, int]:
    text = PYPROJECT.read_text(encoding="utf-8")
    match = _PATTERN.search(text)
    if not match:
        raise SystemExit("pyproject.toml から version を読み取れませんでした")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def write_version(version: str) -> None:
    for path, pattern, replacement in (
        (PYPROJECT, _PATTERN, f'version = "{version}"'),
        (
            INIT,
            re.compile(r'^__version__ = "[^"]+"$', re.MULTILINE),
            f'__version__ = "{version}"',
        ),
    ):
        text = path.read_text(encoding="utf-8")
        new_text, count = pattern.subn(replacement, text)
        if count != 1:
            raise SystemExit(f"{path.name} の version を置換できませんでした")
        path.write_text(new_text, encoding="utf-8")


def next_version(kind: str) -> str:
    if re.fullmatch(r"\d+\.\d+\.\d+", kind):
        return kind

    major, minor, patch = read_version()
    if kind == "major":
        major, minor, patch = major + 1, 0, 0
    elif kind == "minor":
        minor, patch = minor + 1, 0
    elif kind == "patch":
        patch += 1
    else:
        raise SystemExit(f"不明な指定です: {kind} (patch / minor / major / x.y.z)")
    return f"{major}.{minor}.{patch}"


def main() -> int:
    kind = sys.argv[1] if len(sys.argv) > 1 else "patch"
    version = next_version(kind)
    write_version(version)
    print(version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
