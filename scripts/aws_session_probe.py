"""セッション開始 API の疎通確認（プラグインは起動しない）

プラグイン未インストールでも、ここまでが正しく動くかを確認できる。
開始したセッションはすぐ terminate するので課金・残留は発生しない。

使い方:
    uv run python scripts/aws_session_probe.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

STATE_FILE = ROOT / ".ttp-test-state.json"


def load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> int:
    load_dotenv(ROOT / ".env")

    from ttp.aws import AwsAuth, AwsError, describe_error
    from ttp.models import AwsSettings

    state = {}
    if STATE_FILE.is_file():
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))

    settings = AwsSettings(
        access_key_id=os.environ.get("aws_access_key_id", ""),
        secret_access_key=os.environ.get("aws_secret_access_key", ""),
        region=os.environ.get("AWS_DEFAULT_REGION", "ap-northeast-1"),
    )
    auth = AwsAuth(settings)
    boto_session = auth.boto3_session()

    ssm = boto_session.client("ssm")
    print("ssm endpoint:", ssm.meta.endpoint_url)

    instance_id = state.get("instance_id")
    if instance_id:
        try:
            response = ssm.start_session(Target=instance_id)
            print("start_session: OK ->", sorted(response.keys()))
            ssm.terminate_session(SessionId=response["SessionId"])
            print("terminate_session: OK")
        except AwsError as e:
            print("start_session: NG ->", e)
        except Exception as e:  # noqa: BLE001
            print("start_session: NG ->", describe_error(e))
    else:
        print("start_session: スキップ（テスト用 EC2 がありません）")

    ecs = boto_session.client("ecs")
    print("ecs endpoint:", ecs.meta.endpoint_url)

    task_arn = state.get("task_arn")
    if task_arn:
        cluster = task_arn.split("/")[-2]
        try:
            response = ecs.execute_command(
                cluster=cluster,
                task=task_arn,
                container="app",
                command="/bin/sh",
                interactive=True,
            )
            session_info = response.get("session") or {}
            print("execute_command: OK ->", sorted(session_info.keys()))
            if session_info.get("SessionId"):
                ssm.terminate_session(SessionId=session_info["SessionId"])
                print("terminate_session: OK")
        except Exception as e:  # noqa: BLE001
            print("execute_command: NG ->", describe_error(e))
    else:
        print("execute_command: スキップ（テスト用タスクがありません）")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
