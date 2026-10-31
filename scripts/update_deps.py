"""依存パッケージの更新（月次メンテナンス用）

やること:
1. uv.lock を最新に更新
2. 依存を同期
3. スモークチェック
4. 更新内容の要約を表示

使い方:
    uv run python scripts/update_deps.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(args: list[str]) -> int:
    print(f"\n$ {' '.join(args)}")
    return subprocess.call(args, cwd=ROOT)


def summarize() -> None:
    """更新されたパッケージを表示する。"""
    result = subprocess.run(
        ["git", "diff", "--stat", "--", "uv.lock"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    print(result.stdout.strip() or "(uv.lock に差分はありません)")

    diff = subprocess.run(
        ["git", "diff", "-U0", "--", "uv.lock"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    ).stdout
    added = [
        line.strip()
        for line in diff.splitlines()
        if line.startswith("+name = ")
    ]
    if added:
        print("\n新規追加 / 変更されたエントリ:")
        for line in added[:40]:
            print(f"  {line.lstrip('+')}")


def main() -> int:
    if run(["uv", "lock", "--upgrade"]) != 0:
        print("NG: uv lock --upgrade が失敗しました")
        return 1

    if run(["uv", "sync"]) != 0:
        print("NG: uv sync が失敗しました")
        return 1

    if run(["uv", "run", "python", "scripts/smoke_check.py"]) != 0:
        print("NG: スモークチェックが失敗しました。ロールバックしてください。")
        return 1

    summarize()

    print(
        "\n次の手順:\n"
        "  1. アプリを起動して動作確認 (uv run python -m ttp)\n"
        "  2. 問題なければコミット (uv.lock と pyproject.toml)\n"
        "  3. リリースする場合: uv run python scripts/bump_version.py patch\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
