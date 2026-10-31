"""AWS 認証・セッションモジュール

アクセスキー（+ 任意で MFA）から boto3 セッションを組み立てる。

MFA は「必要な IAM ユーザーだけ」が有効化できる opt-in 方式。
MFA が必須のポリシーを敷いていないユーザーは、そのままアクセスキーで動く。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from ttp.models import AwsSettings

__all__ = [
    "AwsAuth",
    "AwsCredentials",
    "AwsError",
    "MfaProvider",
    "MFA_DURATION_SECONDS",
    "describe_error",
]


# MFA コード入力コールバック: (MFA デバイスのシリアル) -> 6桁コード（中止時 None）
MfaProvider = Callable[[str], "str | None"]

# get-session-token の有効期間（秒）。既定の 12 時間。
MFA_DURATION_SECONDS = 43200

# 期限切れの少し前に取り直す
_EXPIRY_MARGIN = timedelta(minutes=5)


class AwsError(Exception):
    """そのままユーザーに見せられるメッセージを持つ例外。"""


@dataclass
class AwsCredentials:
    access_key_id: str
    secret_access_key: str
    session_token: str = ""
    expiration: datetime | None = None


# エラーコードごとの日本語ヒント
_ERROR_HINTS = {
    "InvalidClientTokenId": "アクセスキーが無効です。「AWS 設定」を確認してください。",
    "SignatureDoesNotMatch": "シークレットアクセスキーが正しくありません。",
    "AuthFailure": "認証に失敗しました。アクセスキーとシークレットを確認してください。",
    "UnrecognizedClientException": "アクセスキーが認識されません。",
    "ExpiredToken": "一時クレデンシャルの有効期限が切れました。もう一度接続してください。",
    "ExpiredTokenException": "一時クレデンシャルの有効期限が切れました。もう一度接続してください。",
    "MultiFactorAuthenticationFailed": "MFA コードが正しくありません。もう一度入力してください。",
    "AccessDenied": "権限が不足しています。IAM ポリシーを確認してください。",
    "AccessDeniedException": "権限が不足しています。IAM ポリシーを確認してください。",
    "ThrottlingException": "AWS API のレート制限に達しました。少し待って再試行してください。",
    "RequestLimitExceeded": "AWS API のレート制限に達しました。少し待って再試行してください。",
}


def describe_error(exc: Exception) -> str:
    """botocore の例外を、ユーザーが読める日本語メッセージに変換する。"""
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        err = response.get("Error", {}) or {}
        code = err.get("Code", "") or ""
        detail = err.get("Message", "") or ""
        hint = _ERROR_HINTS.get(code)
        if hint:
            return f"{hint}\n({code}: {detail})" if detail else hint
        if code or detail:
            return f"{code}: {detail}" if code else detail

    text = str(exc)
    lowered = text.lower()
    if "multifactorauth" in lowered or (
        "explicit deny" in lowered and "mfa" in lowered
    ):
        return (
            "この IAM ユーザーは MFA が必須の可能性があります。\n"
            "「AWS 設定」で「MFA を使用する」を有効にしてください。\n\n" + text
        )
    return text


class AwsAuth:
    """AWS 認証情報の解決と boto3 セッション生成。"""

    def __init__(
        self,
        settings: AwsSettings,
        mfa_provider: MfaProvider | None = None,
    ) -> None:
        self._settings = settings
        self._mfa_provider = mfa_provider
        self._creds: AwsCredentials | None = None

    @property
    def settings(self) -> AwsSettings:
        return self._settings

    @property
    def region(self) -> str:
        return self._settings.region or "ap-northeast-1"

    # ── クレデンシャル解決 ─────────────────────────────

    def credentials(self, force: bool = False) -> AwsCredentials:
        if not self._settings.is_configured:
            raise AwsError(
                "AWS の設定が未入力です。\n"
                "メニューの「AWS」→「AWS 設定」から、\n"
                "アクセスキー・シークレット・リージョンを登録してください。"
            )

        if not force and self._creds is not None and not self._is_expired(self._creds):
            return self._creds

        base = AwsCredentials(
            access_key_id=self._settings.access_key_id.strip(),
            secret_access_key=self._settings.secret_access_key.strip(),
        )

        # MFA を使わないユーザーはここで終わり（毎回のコード入力なし）
        if not self._settings.mfa_enabled:
            self._creds = base
            return base

        serial = (self._settings.mfa_serial or "").strip()
        if not serial:
            serial = self._detect_mfa_serial(base)

        if self._mfa_provider is None:
            raise AwsError("MFA コードの入力手段がありません。")

        code = self._mfa_provider(serial)
        if not code:
            raise AwsError("MFA コードが入力されなかったため中止しました。")

        self._creds = self._get_session_token(base, serial, code.strip())
        return self._creds

    @staticmethod
    def _is_expired(creds: AwsCredentials) -> bool:
        if creds.expiration is None:
            return False
        return datetime.now(timezone.utc) >= creds.expiration - _EXPIRY_MARGIN

    def is_expired(self) -> bool:
        """MFA の一時クレデンシャルが切れて、再入力が必要かどうか。

        自動更新のようにダイアログを出したくない場面で使う。
        """
        return self._creds is not None and self._is_expired(self._creds)

    def _base_session(self, creds: AwsCredentials):
        import boto3

        return boto3.Session(
            aws_access_key_id=creds.access_key_id,
            aws_secret_access_key=creds.secret_access_key,
            aws_session_token=creds.session_token or None,
            region_name=self.region,
        )

    def _detect_mfa_serial(self, base: AwsCredentials) -> str:
        """IAM ユーザーに紐づく MFA デバイスの ARN を自動検出する。"""
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            session = self._base_session(base)
            identity = session.client("sts").get_caller_identity()
            arn = identity.get("Arn", "")
            if ":user/" not in arn:
                raise AwsError(
                    "MFA デバイスを自動検出できません。\n"
                    "「AWS 設定」で MFA デバイスの ARN を指定してください。"
                )
            user_name = arn.split(":user/", 1)[1]
            devices = (
                session.client("iam")
                .list_mfa_devices(UserName=user_name)
                .get("MFADevices", [])
            )
            if not devices:
                raise AwsError("この IAM ユーザーに MFA デバイスが登録されていません。")
            return devices[0]["SerialNumber"]
        except AwsError:
            raise
        except (ClientError, BotoCoreError) as e:
            raise AwsError(
                "MFA デバイスの自動検出に失敗しました。\n"
                "「AWS 設定」で MFA デバイスの ARN を入力してください。\n\n"
                + describe_error(e)
            ) from e

    def _get_session_token(
        self, base: AwsCredentials, serial: str, code: str
    ) -> AwsCredentials:
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            session = self._base_session(base)
            resp = session.client("sts").get_session_token(
                SerialNumber=serial,
                TokenCode=code,
                DurationSeconds=MFA_DURATION_SECONDS,
            )
        except (ClientError, BotoCoreError) as e:
            raise AwsError(describe_error(e)) from e

        c = resp["Credentials"]
        return AwsCredentials(
            access_key_id=c["AccessKeyId"],
            secret_access_key=c["SecretAccessKey"],
            session_token=c["SessionToken"],
            expiration=c.get("Expiration"),
        )

    # ── セッション / 環境変数 ───────────────────────────

    def boto3_session(self):
        import boto3

        creds = self.credentials()
        return boto3.Session(
            aws_access_key_id=creds.access_key_id,
            aws_secret_access_key=creds.secret_access_key,
            aws_session_token=creds.session_token or None,
            region_name=self.region,
        )

    def env(self) -> dict[str, str]:
        """子プロセス（aws CLI）へ渡す環境変数。

        コマンドライン引数には絶対に載せない（タスクマネージャで見えるため）。
        """
        creds = self.credentials()
        env = os.environ.copy()
        env["AWS_ACCESS_KEY_ID"] = creds.access_key_id
        env["AWS_SECRET_ACCESS_KEY"] = creds.secret_access_key
        env["AWS_DEFAULT_REGION"] = self.region
        env["AWS_REGION"] = self.region
        if creds.session_token:
            env["AWS_SESSION_TOKEN"] = creds.session_token
        else:
            env.pop("AWS_SESSION_TOKEN", None)
        # ローカル profile の設定に引っ張られないよう除去
        env.pop("AWS_PROFILE", None)
        env.pop("AWS_DEFAULT_PROFILE", None)
        return env

    # ── 接続テスト ──────────────────────────────────────

    def identity(self) -> dict[str, str]:
        """呼び出し元の Account / Arn を返す（接続テスト用）。"""
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            resp = self.boto3_session().client("sts").get_caller_identity()
        except (ClientError, BotoCoreError) as e:
            raise AwsError(describe_error(e)) from e
        return {
            "Account": str(resp.get("Account", "")),
            "Arn": str(resp.get("Arn", "")),
            "UserId": str(resp.get("UserId", "")),
        }
