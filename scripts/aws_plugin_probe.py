"""session-manager-plugin を実際に起動して疎通を確認する

プラグイン単体でセッションが張れるかを、短時間だけコンソールを開いて確認する。
（確認後はプロセスを終了し、セッションも terminate する）

使い方:
    uv run python scripts/aws_plugin_probe.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

STATE_FILE = ROOT / ".ttp-test-state.json"
HOLD_SECONDS = 8


def load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def run_plugin(
    label, plugin, payload, region, parameters, endpoint, env, method="launcher"
) -> bool:
    from ttp import session, terminal

    argv, extra = session._build_argv(plugin, payload, region, parameters, endpoint)
    child_env = dict(env)
    child_env.update(extra)

    print(f"\n[{label}] argv[0]={argv[0][:40]!r} method={method}")

    if method == "launcher":
        # アプリ本体と同じ経路（空文字引数が落ちないことを確認する）
        proc = terminal.launch_process(plugin, argv, child_env, f"probe {label}")
        time.sleep(HOLD_SECONDS)
        alive = proc.poll() is None
        if alive:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
        print(f"[{label}] 起動継続: {alive} (exit={proc.poll()})")
        return alive

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".log")
    tmp.close()
    with open(tmp.name, "w", encoding="utf-8", errors="replace") as handle:
        proc = subprocess.Popen(
            [plugin, *argv],
            env=child_env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
        time.sleep(HOLD_SECONDS)
        alive = proc.poll() is None

    if alive:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

    output = Path(tmp.name).read_text(encoding="utf-8", errors="replace").strip()
    os.unlink(tmp.name)

    print(f"[{label}] 起動継続: {alive} (exit={proc.poll()})")
    if output:
        print(f"[{label}] 出力: {output[:500]}")
    return alive


def main() -> int:
    load_dotenv(ROOT / ".env")

    from ttp.aws import AwsAuth
    from ttp import session, terminal
    from ttp.models import AwsSettings

    plugin = terminal.find_session_manager_plugin()
    if not plugin:
        print("NG: Session Manager プラグインが見つかりません")
        return 1
    print("plugin:", plugin)
    print("version:", session.plugin_version(plugin))

    state = {}
    if STATE_FILE.is_file():
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))

    auth = AwsAuth(
        AwsSettings(
            access_key_id=os.environ.get("aws_access_key_id", ""),
            secret_access_key=os.environ.get("aws_secret_access_key", ""),
            region=os.environ.get("AWS_DEFAULT_REGION", "ap-northeast-1"),
        )
    )
    env = auth.env()
    boto_session = auth.boto3_session()
    ssm = boto_session.client("ssm")
    ok = True

    # ── EC2 (SSM) ──
    instance_id = state.get("instance_id")
    if instance_id:
        parameters = {"Target": instance_id}
        response = ssm.start_session(**parameters)
        payload = session._validate_session(response, normalize=True)
        ok = run_plugin(
            "SSM",
            plugin,
            payload,
            ssm.meta.region_name,
            parameters,
            ssm.meta.endpoint_url,
            env,
        ) and ok
        ssm.terminate_session(SessionId=response["SessionId"])
        print("[SSM] terminate_session: OK")

    # ── ECS Exec ──
    task_arn = state.get("task_arn")
    if task_arn:
        ecs = boto_session.client("ecs")
        cluster = task_arn.split("/")[-2]
        task_id = task_arn.rsplit("/", 1)[-1]
        detail = ecs.describe_tasks(cluster=cluster, tasks=[task_id])["tasks"]
        if not detail:
            print("[ECS] タスクが停止しています（スキップ）")
        else:
            container = detail[0]["containers"][0]
            runtime_id = container["runtimeId"]
            response = ecs.execute_command(
                cluster=cluster,
                task=task_arn,
                container=container["name"],
                command="/bin/sh",
                interactive=True,
            )
            info = response["session"]
            parameters = {
                "Target": f"ecs:{cluster}_{task_id}_{runtime_id}",
            }
            ok = run_plugin(
                "ECS",
                plugin,
                info,
                ecs.meta.region_name,
                parameters,
                ecs.meta.endpoint_url,
                env,
            ) and ok
            ssm.terminate_session(SessionId=info["sessionId"])
            print("[ECS] terminate_session: OK")

    print("\n結果:", "OK" if ok else "NG")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
