"""AWS 疎通確認スクリプト（読み取りのみ）

.env の認証情報でアカウント情報と SSM / ECS の可視性を確認する。
秘密情報（アクセスキー・シークレット）は一切出力しない。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


load_dotenv(ROOT / ".env")

import boto3  # noqa: E402
from botocore.exceptions import BotoCoreError, ClientError  # noqa: E402

from ttp.aws import AwsAuth, AwsError, describe_error  # noqa: E402
from ttp.aws_inventory import list_ec2, list_ecs  # noqa: E402
from ttp.models import AwsSettings  # noqa: E402

REGION = (
    os.environ.get("AWS_DEFAULT_REGION")
    or os.environ.get("AWS_REGION")
    or "ap-northeast-1"
)


def main() -> int:
    access_key = os.environ.get("aws_access_key_id") or os.environ.get(
        "AWS_ACCESS_KEY_ID", ""
    )
    secret_key = os.environ.get("aws_secret_access_key") or os.environ.get(
        "AWS_SECRET_ACCESS_KEY", ""
    )

    if not access_key or not secret_key:
        print("NG: .env に aws_access_key_id / aws_secret_access_key がありません")
        return 1

    settings = AwsSettings(
        access_key_id=access_key,
        secret_access_key=secret_key,
        region=REGION,
    )
    auth = AwsAuth(settings)

    print(f"region: {REGION}")
    try:
        identity = auth.identity()
    except AwsError as e:
        print(f"NG: 認証に失敗しました\n{e}")
        return 1
    print(f"account: {identity['Account']}")
    print(f"arn: {identity['Arn']}")

    session = auth.boto3_session()

    # 有効なリージョン
    try:
        regions = session.client("ec2").describe_regions(
            Filters=[{"Name": "opt-in-status", "Values": ["opt-in-not-required"]}]
        )["Regions"]
        print(f"enabled regions: {len(regions)}")
    except (ClientError, BotoCoreError) as e:
        print(f"regions: 取得できません ({describe_error(e).splitlines()[0]})")

    # EC2
    ec2_targets, ec2_warnings = list_ec2(session)
    print(f"\nEC2: {len(ec2_targets)} 台 (接続可能 {sum(1 for t in ec2_targets if t.connectable)})")
    for t in ec2_targets[:20]:
        print(
            f"  - {t.instance_id} {t.display_name} [{t.status_text}] "
            f"{t.instance_type} {t.platform}"
        )
    for w in ec2_warnings:
        print(f"  ! {w}")

    # ECS
    ecs_targets, ecs_warnings = list_ecs(session)
    print(f"\nECS: {len(ecs_targets)} タスク (接続可能 {sum(1 for t in ecs_targets if t.connectable)})")
    for t in ecs_targets[:20]:
        print(
            f"  - {t.cluster}/{t.service or '-'} {t.short_id} [{t.status_text}] "
            f"{t.launch_type} containers={','.join(t.containers)}"
        )
    for w in ecs_warnings:
        print(f"  ! {w}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
