"""ストレージモジュール

接続情報・設定の保存/読み込みを行う。
接続情報はマスターパスワードで暗号化して保存する。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from ttp.crypto import (
    generate_salt,
    derive_key,
    create_verify_token,
    verify_master_password,
    encrypt,
    decrypt,
)
from ttp.models import Connection, AppSettings


def get_app_dir() -> Path:
    """アプリのルートディレクトリを返す。

    - PyInstaller exe: exeと同じディレクトリ
    - 通常実行: プロジェクトルート (pyproject.tomlがある場所)
    """
    if getattr(sys, "frozen", False):
        # PyInstaller exe
        return Path(sys.executable).parent
    else:
        # 開発時: src/ttp/ → src/ → project root
        return Path(__file__).resolve().parent.parent.parent


def get_data_dir() -> Path:
    """データディレクトリ (ttp_data/) のパスを返す。"""
    d = get_app_dir() / "ttp_data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_log_dir(settings: AppSettings | None = None) -> Path:
    """ログディレクトリのパスを返す。"""
    if settings and settings.log_dir:
        d = Path(settings.log_dir)
    else:
        d = get_app_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_resource_dir() -> Path:
    """リソースディレクトリを返す。"""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "resources"
    else:
        return get_app_dir() / "resources"


# ── マスターパスワード ──────────────────────────────────────


class MasterStore:
    """マスターパスワードのsalt/verify tokenを管理する。"""

    def __init__(self) -> None:
        self._path = get_data_dir() / "master.json"

    def exists(self) -> bool:
        return self._path.is_file()

    def setup(self, master_password: str) -> bytes:
        """マスターパスワードを設定し、導出鍵を返す。"""
        salt = generate_salt()
        key = derive_key(master_password, salt)
        token = create_verify_token(key)
        data = {
            "salt": salt.hex(),
            "verify_token": token.decode("utf-8"),
        }
        self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return key

    def unlock(self, master_password: str) -> bytes | None:
        """マスターパスワードを検証し、正しければ導出鍵を返す。"""
        data = json.loads(self._path.read_text(encoding="utf-8"))
        salt = bytes.fromhex(data["salt"])
        token = data["verify_token"].encode("utf-8")
        key = derive_key(master_password, salt)
        if verify_master_password(token, key):
            return key
        return None

    def change_password(self, old_password: str, new_password: str) -> bytes | None:
        """マスターパスワードを変更する。接続情報の再暗号化が必要。"""
        old_key = self.unlock(old_password)
        if old_key is None:
            return None
        new_key = self.setup(new_password)
        return new_key


# ── 接続情報 ──────────────────────────────────────────────


class ConnectionStore:
    """暗号化された接続情報を管理する。"""

    def __init__(self) -> None:
        self._path = get_data_dir() / "connections.enc"

    def load(self, key: bytes) -> list[Connection]:
        """接続情報を復号化して読み込む。"""
        if not self._path.is_file():
            return []
        encrypted = self._path.read_bytes()
        if not encrypted:
            return []
        try:
            decrypted = decrypt(encrypted, key)
            data = json.loads(decrypted.decode("utf-8"))
            return [Connection.from_dict(c) for c in data.get("connections", [])]
        except Exception:
            return []

    def save(self, connections: list[Connection], key: bytes) -> None:
        """接続情報を暗号化して保存する。"""
        data = {"connections": [c.to_dict() for c in connections]}
        plaintext = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        encrypted = encrypt(plaintext, key)
        self._path.write_bytes(encrypted)

    def re_encrypt(self, old_key: bytes, new_key: bytes) -> bool:
        """古い鍵で復号→新しい鍵で再暗号化する。"""
        connections = self.load(old_key)
        self.save(connections, new_key)
        return True


# ── アプリ設定 ──────────────────────────────────────────────


class SettingsStore:
    """アプリ設定 (非暗号化) を管理する。"""

    def __init__(self) -> None:
        self._path = get_data_dir() / "settings.json"

    def load(self) -> AppSettings:
        if not self._path.is_file():
            return AppSettings()
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return AppSettings.from_dict(data)
        except Exception:
            return AppSettings()

    def save(self, settings: AppSettings) -> None:
        self._path.write_text(
            json.dumps(settings.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
