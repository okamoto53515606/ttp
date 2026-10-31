"""Session Manager / ECS Exec のセッション開始（AWS CLI 不要版）

boto3 で `ssm.start_session` / `ecs.execute_command` を呼び、
`session-manager-plugin` を直接起動する。

aws-cli が行っている呼び出しをそのまま再現している:

    session-manager-plugin <session_json> <region> StartSession <profile> \
        <ssm_params_json> <endpoint_url>

プラグイン 1.2.497.0 以降は、セッショントークンを含む JSON を
環境変数 `AWS_SSM_START_SESSION_RESPONSE` で渡せる（プロセス一覧に出ない）。
"""

from __future__ import annotations

import json
import re
import subprocess
from functools import lru_cache

from ttp import terminal
from ttp.aws import AwsAuth, AwsError, describe_error


class SessionError(Exception):
    """そのままユーザーに見せられるメッセージを持つ例外。"""


# セッションレスポンスを環境変数で渡せるようになったプラグインのバージョン
_ENV_VAR_MIN_VERSION = (1, 2, 497, 0)
_ENV_VAR_NAME = "AWS_SSM_START_SESSION_RESPONSE"


# ── プラグインのユーティリティ ────────────────────────────


@lru_cache(maxsize=4)
def plugin_version(plugin: str) -> tuple[int, ...] | None:
    """プラグインのバージョンを (major, minor, patch, build) で返す。"""
    try:
        output = subprocess.check_output(
            [plugin, "--version"],
            text=True,
            timeout=10,
            creationflags=terminal.CREATE_NO_WINDOW,
        )
    except Exception:
        return None

    match = re.search(r"(\d+)\.(\d+)\.(\d+)(?:\.(\d+))?", output or "")
    if not match:
        return None
    return tuple(int(group) for group in match.groups() if group is not None)


def _require_plugin() -> str:
    plugin = terminal.find_session_manager_plugin()
    if not plugin:
        raise SessionError(
            "Session Manager プラグインが見つかりません。\n"
            "インストールして TTP を再起動してください。"
        )
    return plugin


def _pick(mapping: dict, *names: str) -> str | None:
    """複数のキー名（PascalCase / camelCase）から最初に見つかった値を返す。"""
    for name in names:
        value = mapping.get(name)
        if value:
            return value
    return None


def session_id_of(session: dict) -> str | None:
    """セッション ID を取り出す（SSM は PascalCase、ECS は camelCase）。"""
    return _pick(session, "SessionId", "sessionId")


def _validate_session(session: dict, *, normalize: bool) -> dict:
    """プラグインに渡せるセッション情報か検証する。

    SSM の start_session は PascalCase、ECS の execute_command は
    camelCase を返す。プラグインには API が返した形のまま渡すのが安全なので、
    normalize=True のときだけ PascalCase に揃える。
    """
    session_id = session_id_of(session)
    token = _pick(session, "TokenValue", "tokenValue")
    stream_url = _pick(session, "StreamUrl", "streamUrl")

    if not (session_id and token and stream_url):
        raise SessionError(
            "セッション情報を取得できませんでした"
            "（ECS Exec が無効の可能性があります）。"
        )

    if not normalize:
        return session
    return {
        "SessionId": session_id,
        "TokenValue": token,
        "StreamUrl": stream_url,
    }


def _build_argv(
    plugin: str,
    payload: dict[str, str],
    region: str,
    parameters: dict[str, str],
    endpoint_url: str,
) -> tuple[list[str], dict[str, str]]:
    """プラグインに渡す引数と、追加の環境変数を組み立てる。"""
    version = plugin_version(plugin)
    extra_env: dict[str, str] = {}

    if version is not None and version >= _ENV_VAR_MIN_VERSION:
        # トークンがコマンドライン（プロセス一覧）に出ない
        extra_env[_ENV_VAR_NAME] = json.dumps(payload)
        first = _ENV_VAR_NAME
    else:
        # 古いプラグイン向けフォールバック
        first = json.dumps(payload)

    argv = [
        first,
        region,
        "StartSession",
        "",  # profile 名（空 = 環境変数のクレデンシャルを使う）
        json.dumps(parameters),
        endpoint_url,
    ]
    return argv, extra_env


def _terminate(boto_session, session_id: str | None) -> None:
    """起動に失敗したときに、ゾンビセッションを残さない。

    terminate_session は SSM の API（ECS Exec のセッションも同じ ID で終了できる）。
    """
    if not session_id:
        return
    try:
        boto_session.client("ssm").terminate_session(SessionId=session_id)
    except Exception:
        pass


# ── EC2 (SSM) ─────────────────────────────────────────


def open_ssm(
    auth: AwsAuth, instance_id: str, name: str = ""
) -> subprocess.Popen:
    """SSM Session Manager で EC2 に対話シェル接続する。"""
    plugin = _require_plugin()

    session = auth.boto3_session()
    client = session.client("ssm")

    parameters = {"Target": instance_id}
    try:
        response = client.start_session(**parameters)
    except Exception as e:  # noqa: BLE001
        raise AwsError(describe_error(e)) from e

    payload = _validate_session(response, normalize=True)
    argv, extra_env = _build_argv(
        plugin,
        payload,
        client.meta.region_name,
        parameters,
        client.meta.endpoint_url,
    )

    env = auth.env()
    env.update(extra_env)

    try:
        return terminal.launch_process(
            plugin, argv, env, f"SSM {name or instance_id}"
        )
    except Exception:
        _terminate(session, session_id_of(payload))
        raise


# ── ECS (ECS Exec) ────────────────────────────────────


def _container_runtime_id(
    client, cluster: str, task_id: str, container: str
) -> str:
    response = client.describe_tasks(cluster=cluster, tasks=[task_id])
    tasks = response.get("tasks", [])
    if not tasks:
        raise SessionError("タスクが見つかりません（停止した可能性があります）。")

    for item in tasks[0].get("containers", []):
        if item.get("name") == container:
            runtime_id = item.get("runtimeId")
            if runtime_id:
                return runtime_id
    raise SessionError(
        "コンテナの runtimeId を取得できませんでした。\n"
        "ECS Exec が有効になっているか確認してください。"
    )


def open_ecs(
    auth: AwsAuth,
    cluster: str,
    task_arn: str,
    container: str,
    command: str = "/bin/sh",
    service: str = "",
    runtime_id: str = "",
) -> subprocess.Popen:
    """ECS Exec でタスクのコンテナに対話シェル接続する。"""
    plugin = _require_plugin()

    session = auth.boto3_session()
    client = session.client("ecs")

    task_id = task_arn.rsplit("/", 1)[-1]
    if not runtime_id:
        runtime_id = _container_runtime_id(client, cluster, task_id, container)

    try:
        response = client.execute_command(
            cluster=cluster,
            task=task_arn,
            container=container,
            command=command,
            interactive=True,
        )
    except Exception as e:  # noqa: BLE001
        raise AwsError(describe_error(e)) from e

    session_info = response.get("session") or {}
    payload = _validate_session(session_info, normalize=False)

    cluster_name = cluster.rsplit("/", 1)[-1] if "/" in cluster else cluster
    parameters = {"Target": f"ecs:{cluster_name}_{task_id}_{runtime_id}"}
    argv, extra_env = _build_argv(
        plugin,
        payload,
        client.meta.region_name,
        parameters,
        client.meta.endpoint_url,
    )

    env = auth.env()
    env.update(extra_env)

    title = f"ECS {cluster_name}/{service or task_id[:12]}"
    try:
        return terminal.launch_process(plugin, argv, env, title)
    except Exception:
        _terminate(session, session_id_of(payload))
        raise
