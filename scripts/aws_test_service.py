"""ECS サービス配下の複数タスクで動作するかの確認用スクリプト

既存のテスト用クラスタ（ttp-test-cluster）に、desiredCount=2 の
サービスを作成して ECS Exec が有効なタスクが 2 つ並ぶことを確認する。

使い方:
    uv run python scripts/aws_test_service.py create
    uv run python scripts/aws_test_service.py status
    uv run python scripts/aws_test_service.py delete
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

STATE_FILE = ROOT / ".ttp-test-state.json"
CLUSTER = "ttp-test-cluster"
TASK_FAMILY = "ttp-test-task"
SERVICE = "ttp-test-svc"
DESIRED_COUNT = 2


def load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def make_session():
    import boto3

    return boto3.Session(
        aws_access_key_id=os.environ.get("aws_access_key_id")
        or os.environ.get("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.environ.get("aws_secret_access_key")
        or os.environ.get("AWS_SECRET_ACCESS_KEY"),
        region_name=os.environ.get("AWS_DEFAULT_REGION", "ap-northeast-1"),
    )


def state() -> dict:
    if STATE_FILE.is_file():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def wait_service_stable(ecs, timeout: int = 300) -> list[dict]:
    started = time.time()
    while time.time() - started < timeout:
        tasks = ecs.list_tasks(
            cluster=CLUSTER, serviceName=SERVICE, desiredStatus="RUNNING"
        )["taskArns"]
        if len(tasks) >= DESIRED_COUNT:
            details = ecs.describe_tasks(cluster=CLUSTER, tasks=tasks)["tasks"]
            ready = 0
            for task in details:
                statuses = [
                    agent.get("lastStatus")
                    for container in task.get("containers", [])
                    for agent in container.get("managedAgents", [])
                    if agent.get("name") == "ExecuteCommandAgent"
                ]
                if "RUNNING" in statuses:
                    ready += 1
            print(f"  ... タスク {len(details)} / Exec 可能 {ready}")
            if ready >= DESIRED_COUNT:
                return details
        else:
            print(f"  ... タスク起動待ち ({len(tasks)}/{DESIRED_COUNT})")
        time.sleep(15)
    return []


def cmd_create(session) -> int:
    ecs = session.client("ecs")
    s = state()
    if not s.get("subnet_id") or not s.get("sg_id"):
        print("NG: 先に scripts/aws_test_env.py create を実行してください")
        return 1

    try:
        ecs.create_service(
            cluster=CLUSTER,
            serviceName=SERVICE,
            taskDefinition=TASK_FAMILY,
            desiredCount=DESIRED_COUNT,
            launchType="FARGATE",
            platformVersion="1.4.0",
            enableExecuteCommand=True,
            networkConfiguration={
                "awsvpcConfiguration": {
                    "subnets": [s["subnet_id"]],
                    "securityGroups": [s["sg_id"]],
                    "assignPublicIp": "ENABLED",
                }
            },
        )
        print(f"サービス作成: {SERVICE} (desiredCount={DESIRED_COUNT})")
    except Exception as e:  # noqa: BLE001
        print(f"create_service: {type(e).__name__}: {e}")

    tasks = wait_service_stable(ecs)
    if not tasks:
        print("! タスクが安定しませんでした")
        return 1

    print("\n=== サービス配下のタスク ===")
    for task in tasks:
        print(f"  {task['taskArn'].rsplit('/', 1)[-1]}  group={task.get('group')}")

    # 一覧取得モジュールで拾えるか
    from ttp.aws_inventory import list_ecs

    targets, warnings = list_ecs(session)
    svc = [t for t in targets if t.service == SERVICE]
    print(f"\nlist_ecs: サービス配下 {len(svc)} 件")
    for t in svc:
        print(
            f"  {t.task_id[:12]} [{t.status_text}] containers={t.containers} "
            f"runtime_ids={sorted(t.runtime_ids)}"
        )
    for w in warnings:
        print(f"  ! {w}")

    # 実際にセッションを開始できるか（すぐ終了する）
    ssm = session.client("ssm")
    for task in tasks:
        arn = task["taskArn"]
        container = task["containers"][0]["name"]
        try:
            resp = ecs.execute_command(
                cluster=CLUSTER,
                task=arn,
                container=container,
                command="/bin/sh",
                interactive=True,
            )
            info = resp.get("session") or {}
            print(f"execute_command OK: {arn.rsplit('/', 1)[-1][:12]} keys={sorted(info)}")
            if info.get("sessionId"):
                ssm.terminate_session(SessionId=info["sessionId"])
        except Exception as e:  # noqa: BLE001
            print(f"execute_command NG: {arn.rsplit('/', 1)[-1][:12]} {e}")

    return 0


def cmd_status(session) -> int:
    from ttp.aws_inventory import list_ecs

    targets, _ = list_ecs(session)
    svc = [t for t in targets if t.service == SERVICE]
    print(f"サービス {SERVICE}: {len(svc)} タスク")
    for t in svc:
        print(f"  {t.task_id[:12]} [{t.status_text}] {t.task_definition} {t.started_at}")
    return 0


def cmd_delete(session) -> int:
    ecs = session.client("ecs")
    try:
        ecs.update_service(cluster=CLUSTER, service=SERVICE, desiredCount=0)
        print("desiredCount=0 に設定")
    except Exception as e:  # noqa: BLE001
        print(f"update_service: {type(e).__name__}")
    try:
        ecs.delete_service(cluster=CLUSTER, service=SERVICE, force=True)
        print(f"サービス削除: {SERVICE}")
    except Exception as e:  # noqa: BLE001
        print(f"delete_service: {type(e).__name__}: {e}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["create", "status", "delete"])
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    session = make_session()

    if args.action == "create":
        return cmd_create(session)
    if args.action == "status":
        return cmd_status(session)
    return cmd_delete(session)


if __name__ == "__main__":
    raise SystemExit(main())
