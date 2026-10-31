"""Windows Terminal / PowerShell で AWS セッションを起動するモジュール。

認証情報は環境変数で子プロセスに渡す（コマンドライン引数には載せない）。
"""

from __future__ import annotations

import os
import shutil
import subprocess

from ttp.aws import AwsAuth


_CREATE_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class TerminalError(Exception):
    """そのままユーザーに見せられるメッセージを持つ例外。"""


def find_windows_terminal() -> str | None:
    """wt.exe を探す。"""
    for name in ("wt.exe", "wt"):
        found = shutil.which(name)
        if found:
            return found
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    if local_appdata:
        candidate = os.path.join(
            local_appdata, "Microsoft", "WindowsApps", "wt.exe"
        )
        if os.path.isfile(candidate):
            return candidate
    return None


def find_aws_cli() -> str | None:
    """aws CLI を探す。"""
    return shutil.which("aws.exe") or shutil.which("aws")


def find_session_manager_plugin() -> str | None:
    """Session Manager プラグインを探す。"""
    found = shutil.which("session-manager-plugin.exe") or shutil.which(
        "session-manager-plugin"
    )
    if found:
        return found
    candidates = [
        r"C:\Program Files\Amazon\SessionManagerPlugin\bin\session-manager-plugin.exe",
        os.path.join(
            os.environ.get("ProgramFiles", r"C:\Program Files"),
            "Amazon",
            "SessionManagerPlugin",
            "bin",
            "session-manager-plugin.exe",
        ),
        os.path.join(
            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
            "Amazon",
            "SessionManagerPlugin",
            "bin",
            "session-manager-plugin.exe",
        ),
    ]
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    return None


def missing_prerequisites() -> list[str]:
    """接続に必要な外部ツールのうち、見つからないものの名前を返す。"""
    return [tool["name"] for tool in missing_tools()]


# 必要な外部ツールの案内情報
TOOL_INFO: dict[str, dict[str, str]] = {
    "plugin": {
        "name": "Session Manager プラグイン",
        "purpose": (
            "セッションの通信路（トンネル）を作ります。"
            "これが入っていれば AWS CLI は不要です。"
        ),
        "url": (
            "https://docs.aws.amazon.com/systems-manager/latest/userguide/"
            "session-manager-working-with-install-plugin.html"
        ),
        "check": "session-manager-plugin --version",
    },
    "aws_cli": {
        "name": "AWS CLI v2",
        "purpose": "プラグインが無い場合の代替手段です（サイズは約 250MB）。",
        "url": (
            "https://docs.aws.amazon.com/cli/latest/userguide/"
            "getting-started-install.html"
        ),
        "check": "aws --version",
    },
}


def missing_tools() -> list[dict[str, str]]:
    """接続に使える手段が何も無い場合に、案内情報を返す。

    プラグインがあれば AWS CLI は不要。AWS CLI だけでも動く（旧方式）。
    """
    if backend():
        return []
    return [
        dict(TOOL_INFO["plugin"], key="plugin"),
        dict(TOOL_INFO["aws_cli"], key="aws_cli"),
    ]


def backend() -> str:
    """利用可能な接続方式を返す。

    "plugin" : session-manager-plugin を直接起動（推奨・AWS CLI 不要）
    "cli"    : AWS CLI に任せる（旧方式）
    ""       : どちらも無い
    """
    if find_session_manager_plugin():
        return "plugin"
    if find_aws_cli():
        return "cli"
    return ""


def _ps_quote(value: str) -> str:
    """PowerShell のシングルクォート文字列にする。"""
    return "'" + str(value).replace("'", "''") + "'"


def _ps_exe(path: str) -> str:
    """パスにスペースを含む場合も実行できる形にする。"""
    if "\\" in path or "/" in path or " " in path:
        return "& " + _ps_quote(path)
    return path


def _find_powershell() -> str | None:
    """pwsh (PowerShell 7) を優先し、無ければ Windows PowerShell を返す。"""
    return shutil.which("pwsh") or shutil.which("powershell")


def _launch(command: str, title: str, env: dict[str, str]) -> subprocess.Popen:
    """新しいコンソールで PowerShell を起動してコマンドを実行する。

    wt.exe の new-tab は「既に起動中の Windows Terminal」に処理を委譲するため、
    こちらで設定した環境変数（AWS の認証情報）が新しいタブに引き継がれない。
    そのため CREATE_NEW_CONSOLE で直接起動する。
    Windows 11 の既定ターミナルが Windows Terminal なら、
    そのままタブとして開く。
    """
    shell = _find_powershell()
    if not shell:
        raise TerminalError("PowerShell が見つかりませんでした。")

    ps_command = f"$Host.UI.RawUI.WindowTitle = {_ps_quote(title)}; {command}"
    return subprocess.Popen(
        [shell, "-NoLogo", "-NoExit", "-Command", ps_command],
        env=env,
        creationflags=_CREATE_NEW_CONSOLE,
    )


def launch_process(
    executable: str,
    argv: list[str],
    env: dict[str, str],
    title: str,
) -> subprocess.Popen:
    """任意の実行ファイルを新しいコンソールで直接起動する。

    PowerShell 経由で起動すると、**空文字の引数（プロファイル名）が落ちて**
    プラグイン側で引数が 1 つずれ、次のようにクラッシュする。

        panic: interface conversion: interface {} is nil, not string

    そのため CreateProcess に引数リストをそのまま渡す。
    引数は Python 側で `""` にクォートされるため、空文字も正しく渡る。
    """
    startupinfo = None
    if hasattr(subprocess, "STARTUPINFO"):
        startupinfo = subprocess.STARTUPINFO()
        # lpTitle をコンソールのウィンドウタイトルとして使う
        startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
        startupinfo.wShowWindow = 1  # SW_SHOWNORMAL
        startupinfo.lpTitle = title

    return subprocess.Popen(
        [executable, *argv],
        env=env,
        creationflags=_CREATE_NEW_CONSOLE,
        startupinfo=startupinfo,
    )


def _require_aws() -> str:
    aws = find_aws_cli()
    if not aws:
        raise TerminalError(
            "AWS CLI (aws.exe) が見つかりません。\n"
            "AWS CLI v2 をインストールし、PATH を通してください。"
        )
    return aws


def open_ssm_session(auth: AwsAuth, instance_id: str, name: str = "") -> subprocess.Popen:
    """SSM Session Manager で EC2 に対話シェル接続する。"""
    aws = _require_aws()
    command = " ".join(
        [
            _ps_exe(aws),
            "ssm",
            "start-session",
            "--target",
            _ps_quote(instance_id),
            "--region",
            _ps_quote(auth.region),
        ]
    )
    title = f"SSM {name or instance_id}"
    return _launch(command, title, auth.env())


def open_ecs_exec(
    auth: AwsAuth,
    cluster: str,
    task_arn: str,
    container: str,
    command: str = "/bin/sh",
    service: str = "",
) -> subprocess.Popen:
    """ECS Exec でタスクのコンテナに対話シェル接続する。"""
    aws = _require_aws()
    cmd = " ".join(
        [
            _ps_exe(aws),
            "ecs",
            "execute-command",
            "--cluster",
            _ps_quote(cluster),
            "--task",
            _ps_quote(task_arn),
            "--container",
            _ps_quote(container),
            "--interactive",
            "--command",
            _ps_quote(command),
            "--region",
            _ps_quote(auth.region),
        ]
    )
    task_id = task_arn.rsplit("/", 1)[-1][:12]
    title = f"ECS {cluster}/{service or task_id}"
    return _launch(cmd, title, auth.env())
