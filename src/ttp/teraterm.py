"""Tera Term 連携モジュール

ttpmacro.exe の自動検出、マクロ実行、ログ管理を行う。
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime
from pathlib import Path

from ttp.models import Connection, AppSettings
from ttp.storage import get_log_dir, get_resource_dir


# ttpmacro.exe の検索パス (新しいバージョン優先)
_SEARCH_PATHS = [
    r"{PROGRAMFILES}\teraterm5\ttpmacro.exe",
    r"{PROGRAMFILES(X86)}\teraterm5\ttpmacro.exe",
    r"{PROGRAMFILES}\teraterm\ttpmacro.exe",
    r"{PROGRAMFILES(X86)}\teraterm\ttpmacro.exe",
]


def search_ttpmacro() -> list[str]:
    """ttpmacro.exe をよくある場所から検索し、見つかったパスのリストを返す。"""
    found: list[str] = []
    env_map = {
        "PROGRAMFILES": os.environ.get("PROGRAMFILES", r"C:\Program Files"),
        "PROGRAMFILES(X86)": os.environ.get(
            "PROGRAMFILES(X86)", r"C:\Program Files (x86)"
        ),
    }
    for template in _SEARCH_PATHS:
        path = template
        for var, val in env_map.items():
            path = path.replace(f"{{{var}}}", val)
        if os.path.isfile(path):
            found.append(path)
    return found


def get_macro_path() -> str:
    """connect.ttl マクロのパスを返す。"""
    return str(get_resource_dir() / "connect.ttl")


def generate_log_filename(connection: Connection, settings: AppSettings) -> str:
    """ログファイルのフルパスを生成する。"""
    log_dir = get_log_dir(settings)
    now = datetime.now()
    # ファイル名に使えない文字を除去
    safe_name = (
        connection.name.replace("/", "_")
        .replace("\\", "_")
        .replace(":", "_")
        .replace("*", "_")
        .replace("?", "_")
        .replace('"', "_")
        .replace("<", "_")
        .replace(">", "_")
        .replace("|", "_")
    )
    filename = f"{now.strftime('%Y-%m-%d_%H%M%S')}_{safe_name}.log"
    return str(log_dir / filename)


def launch_connection(
    connection: Connection,
    settings: AppSettings,
) -> subprocess.Popen | None:
    """テラタームマクロを起動して接続する。

    - 環境変数経由でパスワードを渡す (ファイルに平文を書かない)
    - 鍵認証の場合は ttp_data/keys/ に保存された鍵ファイルのパスを渡す
    - ログファイルを自動設定
    """
    ttpmacro = settings.ttpmacro_path
    if not ttpmacro or not os.path.isfile(ttpmacro):
        return None

    macro_path = get_macro_path()
    if not os.path.isfile(macro_path):
        return None

    try:
        log_path = generate_log_filename(connection, settings)
    except Exception:
        # ログディレクトリ作成に失敗した場合はデフォルトに戻して再試行
        settings.log_dir = ""
        log_path = generate_log_filename(connection, settings)

    # 鍵ファイルパス (publickey認証時)
    key_path = ""
    if connection.auth_type == "publickey" and connection.key_path:
        if os.path.isfile(connection.key_path):
            key_path = connection.key_path

    # 環境変数を構築
    env = os.environ.copy()
    env["TT_TTL_HOST"] = connection.host
    env["TT_TTL_PORT"] = str(connection.port)
    env["TT_TTL_AUTH"] = connection.auth_type
    env["TT_TTL_USER"] = connection.username
    env["TT_TTL_PASSWD"] = connection.password
    env["TT_TTL_PRIVATEKEY"] = key_path
    env["TT_TTL_PROMPT"] = connection.prompt
    env["TT_TTL_SENDLN_PARAM"] = connection.sendln_param
    env["TT_TTL_LOGPATH"] = log_path

    try:
        proc = subprocess.Popen(
            [ttpmacro, macro_path],
            env=env,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception:
        return None

    return proc
