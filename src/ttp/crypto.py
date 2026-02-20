"""暗号化・復号化モジュール

マスターパスワードから鍵を導出し、Fernet (AES) で接続情報を暗号化/復号化する。
"""

from __future__ import annotations

import base64
import os

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

# パスワード検証用の固定文字列
_VERIFY_PLAINTEXT = b"TTP_MASTER_VERIFY"

# PBKDF2 イテレーション回数 (OWASP 推奨)
_ITERATIONS = 480_000


def generate_salt() -> bytes:
    """ランダムな16バイトのsaltを生成する。"""
    return os.urandom(16)


def derive_key(master_password: str, salt: bytes) -> bytes:
    """マスターパスワードとsaltからFernet鍵を導出する。"""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=_ITERATIONS,
    )
    return base64.urlsafe_b64encode(kdf.derive(master_password.encode("utf-8")))


def encrypt(data: bytes, key: bytes) -> bytes:
    """データをFernetで暗号化する。"""
    return Fernet(key).encrypt(data)


def decrypt(data: bytes, key: bytes) -> bytes:
    """データをFernetで復号化する。InvalidToken例外が出る場合はパスワード不一致。"""
    return Fernet(key).decrypt(data)


def create_verify_token(key: bytes) -> bytes:
    """マスターパスワード検証用トークンを生成する。"""
    return encrypt(_VERIFY_PLAINTEXT, key)


def verify_master_password(token: bytes, key: bytes) -> bool:
    """マスターパスワードが正しいか検証する。"""
    try:
        return decrypt(token, key) == _VERIFY_PLAINTEXT
    except InvalidToken:
        return False
