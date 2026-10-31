"""ログから機密情報だけをマスクする

使い方:
    uv run python scripts/redact_secrets.py <file>           # ドライラン
    uv run python scripts/redact_secrets.py <file> --apply   # 書き換え

コード例や通常の文章を壊さないよう、**明確に資格情報と分かる形**だけを対象にする。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# (説明, 正規表現, 置換後)
RULES: list[tuple[str, re.Pattern[str], str]] = [
    (
        "AWS アクセスキー ID",
        re.compile(
            r"\b(?:AKIA|ASIA|AIDA|AROA|AGPA|ANPA|ANVA|AIPA|ABIA|ACCA)[A-Z0-9]{16}\b"
        ),
        "AKIA****************",
    ),
    (
        "AWS シークレット (代入)",
        re.compile(
            r"(?i)(aws_secret_access_key\s*[=:]\s*)"
            r"(?:" + "\"[^\"]{8,}\"" + r"|'[^']{8,}'|[A-Za-z0-9/+=]{16,})"
        ),
        r"\1<REDACTED>",
    ),
    (
        "AWS アクセスキー (代入)",
        re.compile(
            r"(?i)(aws_access_key_id\s*[=:]\s*)"
            r"(?:" + "\"[^\"]{8,}\"" + r"|'[^']{8,}'|[A-Za-z0-9/+=]{16,})"
        ),
        r"\1<REDACTED>",
    ),
    (
        "AWS セッショントークン (代入)",
        re.compile(
            r"(?i)(aws_session_token\s*[=:]\s*)"
            r"(?:" + "\"[^\"]{8,}\"" + r"|'[^']{8,}'|[A-Za-z0-9/+=]{16,})"
        ),
        r"\1<REDACTED>",
    ),
    (
        "AWS セッショントークン (JSON)",
        re.compile(r"(?i)(\"(?:TokenValue|tokenValue)\"\s*:\s*\")[^\"]+"),
        r"\1<REDACTED>",
    ),
    (
        "AWS 資格情報 (JSON)",
        re.compile(
            r"(?i)(\"(?:SecretAccessKey|SessionToken|AccessKeyId)\"\s*:\s*\")[^\"]+"
        ),
        r"\1<REDACTED>",
    ),
    (
        "秘密鍵ブロック",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
        "-----BEGIN PRIVATE KEY-----<REDACTED>-----END PRIVATE KEY-----",
    ),
    (
        "GitHub トークン",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
        "<REDACTED_GITHUB_TOKEN>",
    ),
    (
        "Slack トークン",
        re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
        "<REDACTED_SLACK_TOKEN>",
    ),
    (
        "API キー (sk-/AIza)",
        re.compile(r"\b(?:sk-[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{30,})\b"),
        "<REDACTED_API_KEY>",
    ),
    (
        "AWS アカウント ID (12桁)",
        re.compile(r"(?<!\d)(?!123456789012\b)\d{12}(?!\d)"),
        "123456789012",
    ),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    path = Path(args.path)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    text = path.read_text(encoding="utf-8")
    original_len = len(text)

    total = 0
    report: list[tuple[str, int, list[str]]] = []

    for label, pattern, replacement in RULES:
        matches = list(pattern.finditer(text))
        if not matches:
            continue
        total += len(matches)
        lines: list[str] = []
        for match in matches[:3]:
            start = text.rfind("\n", 0, match.start()) + 1
            end = text.find("\n", match.end())
            if end == -1:
                end = len(text)
            line_no = text.count("\n", 0, match.start()) + 1
            redacted_line = pattern.sub(replacement, text[start:end])
            lines.append(f"    L{line_no}: {redacted_line[:160]}")
        report.append((label, len(matches), lines))

    print(f"対象: {path}  ({original_len} 文字)")
    if total == 0:
        print("検出された機密はありませんでした。")
    for label, count, lines in report:
        print(f"\n[{label}] {count} 件")
        for line in lines:
            print(line)

    if total and args.apply:
        for _label, pattern, replacement in RULES:
            text = pattern.sub(replacement, text)
        path.write_text(text, encoding="utf-8")
        print(f"\n書き換えました（{total} 件マスク）。")
    elif total:
        print("\n（ドライランです。--apply で書き換えます）")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
