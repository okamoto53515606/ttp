"""データモデル"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any


@dataclass
class Connection:
    """SSH接続情報"""

    name: str = ""
    host: str = ""
    port: int = 22
    auth_type: str = "password"  # "password" or "publickey"
    username: str = ""
    password: str = ""
    key_path: str = ""  # 鍵ファイルのフルパス
    prompt: str = ""  # ログイン後に待つプロンプト
    sendln_param: str = ""  # プロンプト後に送るコマンド
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Connection:
        # 未知のフィールドは無視
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)

    @property
    def display_auth(self) -> str:
        return "鍵" if self.auth_type == "publickey" else "PW"

    @property
    def display_host(self) -> str:
        return f"{self.host}:{self.port}"


@dataclass
class AppSettings:
    """アプリケーション設定"""

    ttpmacro_path: str = ""
    log_dir: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AppSettings:
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)
