"""TTP 動作確認用のテスト環境を作成/削除するスクリプト

作成されるもの (すべて名前が ttp-test- で始まる):
  EC2  : t3.micro / Amazon Linux 2023 / SSM エージェント有効 / インバウンドなし
  ECS  : Fargate タスク 1 個 (ECS Exec 有効) / amazonlinux:2023 / sleep 3600
  共通 : IAM ロール、セキュリティグループ、ロググループ

使い方:
    uv run python scripts/aws_test_env.py create
    uv run python scripts/aws_test_env.py status
    uv run python scripts/aws_test_env.py cleanup
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

PREFIX = "ttp-test"
REGION = os.environ.get("AWS_DEFAULT_REGION") or "ap-northeast-1"

EC2_ROLE = f"{PREFIX}-ec2-role"
EC2_PROFILE = f"{PREFIX}-ec2-profile"
SG_NAME = f"{PREFIX}-sg"
CLUSTER = f"{PREFIX}-cluster"
TASK_FAMILY = f"{PREFIX}-task"
EXEC_ROLE = f"{PREFIX}-ecs-execution"
TASK_ROLE = f"{PREFIX}-ecs-task"
LOG_GROUP = f"/ecs/{PREFIX}"

STATE_FILE = ROOT / ".{0}-state.json".format(PREFIX)


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

    access_key = os.environ.get("aws_access_key_id") or os.environ.get(
        "AWS_ACCESS_KEY_ID"
    )
    secret_key = os.environ.get("aws_secret_access_key") or os.environ.get(
        "AWS_SECRET_ACCESS_KEY"
    )
    if not access_key or not secret_key:
        raise SystemExit("NG: .env に認証情報がありません")
    return boto3.Session(
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=REGION,
    )


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def load_state() -> dict:
    if STATE_FILE.is_file():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


# ── 共通 ──────────────────────────────────────────────


def ensure_ec2_role(session) -> str:
    iam = session.client("iam")
    assume = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "ec2.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }
    try:
        role = iam.get_role(RoleName=EC2_ROLE)["Role"]
        print(f"  IAM ロール {EC2_ROLE}: 既存")
    except iam.exceptions.NoSuchEntityException:
        role = iam.create_role(
            RoleName=EC2_ROLE,
            AssumeRolePolicyDocument=json.dumps(assume),
            Description="TTP test instance role (SSM only)",
        )["Role"]
        print(f"  IAM ロール {EC2_ROLE}: 作成")
    iam.attach_role_policy(
        RoleName=EC2_ROLE,
        PolicyArn="arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
    )

    try:
        iam.get_instance_profile(InstanceProfileName=EC2_PROFILE)
        print(f"  インスタンスプロファイル {EC2_PROFILE}: 既存")
    except iam.exceptions.NoSuchEntityException:
        iam.create_instance_profile(InstanceProfileName=EC2_PROFILE)
        print(f"  インスタンスプロファイル {EC2_PROFILE}: 作成")

    profile = iam.get_instance_profile(InstanceProfileName=EC2_PROFILE)[
        "InstanceProfile"
    ]
    if not any(r["RoleName"] == EC2_ROLE for r in profile.get("Roles", [])):
        iam.add_role_to_instance_profile(
            InstanceProfileName=EC2_PROFILE, RoleName=EC2_ROLE
        )
        print("  ロールをプロファイルに追加（反映待ち 10 秒）")
        time.sleep(10)
        profile = iam.get_instance_profile(InstanceProfileName=EC2_PROFILE)[
            "InstanceProfile"
        ]
    # run_instances にはインスタンスプロファイルの ARN を渡す
    return profile["Arn"]


def ensure_sg(session, vpc_id: str) -> str:
    ec2 = session.client("ec2")
    found = ec2.describe_security_groups(
        Filters=[
            {"Name": "group-name", "Values": [SG_NAME]},
            {"Name": "vpc-id", "Values": [vpc_id]},
        ]
    )["SecurityGroups"]
    if found:
        print(f"  セキュリティグループ {SG_NAME}: 既存")
        return found[0]["GroupId"]
    sg = ec2.create_security_group(
        GroupName=SG_NAME,
        Description="TTP test SG (no inbound)",
        VpcId=vpc_id,
    )
    ec2.revoke_security_group_egress(
        GroupId=sg["GroupId"],
        IpPermissions=[{"IpProtocol": "-1", "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}],
    )
    ec2.authorize_security_group_egress(
        GroupId=sg["GroupId"],
        IpPermissions=[
            {
                "IpProtocol": "-1",
                "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
                "Ipv6Ranges": [{"CidrIpv6": "::/0"}],
            }
        ],
    )
    print(f"  セキュリティグループ {SG_NAME}: 作成（インバウンドなし）")
    return sg["GroupId"]


# ── EC2 ───────────────────────────────────────────────


def resolve_al2023_ami(session) -> str:
    ssm = session.client("ssm")
    try:
        return ssm.get_parameter(
            Name="/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
        )["Parameter"]["Value"]
    except Exception:
        ec2 = session.client("ec2")
        images = ec2.describe_images(
            Owners=["amazon"],
            Filters=[
                {"Name": "name", "Values": ["al2023-ami-2023*-x86_64"]},
                {"Name": "state", "Values": ["available"]},
            ],
        )["Images"]
        images.sort(key=lambda i: i["CreationDate"], reverse=True)
        return images[0]["ImageId"]


def create_ec2(session, vpc_id: str, subnet_id: str, sg_id: str, profile_arn: str) -> dict:
    ec2 = session.client("ec2")
    ami = resolve_al2023_ami(session)
    print(f"  AMI: {ami}")

    resp = ec2.run_instances(
        ImageId=ami,
        InstanceType="t3.micro",
        MinCount=1,
        MaxCount=1,
        SubnetId=subnet_id,
        SecurityGroupIds=[sg_id],
        IamInstanceProfile={"Arn": profile_arn},
        MetadataOptions={
            "HttpTokens": "required",
            "HttpEndpoint": "enabled",
            "HttpPutResponseHopLimit": 2,
        },
        TagSpecifications=[
            {
                "ResourceType": "instance",
                "Tags": [
                    {"Key": "Name", "Value": f"{PREFIX}-ec2"},
                    {"Key": "Purpose", "Value": "ttp-test"},
                ],
            }
        ],
    )
    instance_id = resp["Instances"][0]["InstanceId"]
    print(f"  インスタンス起動: {instance_id}")
    return {"instance_id": instance_id}


def wait_ssm_online(session, instance_id: str, timeout: int = 420) -> bool:
    ssm = session.client("ssm")
    started = time.time()
    while time.time() - started < timeout:
        try:
            info = ssm.describe_instance_information(
                Filters=[{"Key": "InstanceIds", "Values": [instance_id]}]
            )["InstanceInformationList"]
            if info and info[0].get("PingStatus") == "Online":
                print(f"  SSM Online を確認 ({int(time.time() - started)} 秒)")
                return True
            if info:
                print(f"  ... SSM 登録済み / Ping={info[0].get('PingStatus')}")
            else:
                print("  ... SSM 未登録")
        except Exception as e:  # noqa: BLE001
            print(f"  ... 待機中 ({type(e).__name__})")
        time.sleep(20)
    print("  ! SSM Online になりませんでした")
    return False


# ── ECS / Fargate ─────────────────────────────────────


def ensure_ecs_roles(session) -> tuple[str, str]:
    iam = session.client("iam")

    exec_assume = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "ecs-tasks.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }

    def ensure_role(name: str, description: str) -> str:
        try:
            arn = iam.get_role(RoleName=name)["Role"]["Arn"]
            print(f"  IAM ロール {name}: 既存")
        except iam.exceptions.NoSuchEntityException:
            arn = iam.create_role(
                RoleName=name,
                AssumeRolePolicyDocument=json.dumps(exec_assume),
                Description=description,
            )["Role"]["Arn"]
            print(f"  IAM ロール {name}: 作成")
        return arn

    exec_arn = ensure_role(EXEC_ROLE, "TTP test ECS execution role")
    iam.attach_role_policy(
        RoleName=EXEC_ROLE,
        PolicyArn="arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy",
    )

    task_arn = ensure_role(TASK_ROLE, "TTP test ECS task role (ECS Exec)")
    iam.put_role_policy(
        RoleName=TASK_ROLE,
        PolicyName="EcsExecPermissions",
        PolicyDocument=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": [
                            "ssmmessages:CreateControlChannel",
                            "ssmmessages:CreateDataChannel",
                            "ssmmessages:OpenControlChannel",
                            "ssmmessages:OpenDataChannel",
                        ],
                        "Resource": "*",
                    }
                ],
            }
        ),
    )

    time.sleep(10)  # IAM 反映待ち
    return exec_arn, task_arn


def create_ecs(session, subnet_id: str, sg_id: str) -> dict:
    ecs = session.client("ecs")
    logs = session.client("logs")

    exec_arn, task_arn = ensure_ecs_roles(session)

    try:
        logs.create_log_group(logGroupName=LOG_GROUP)
        print(f"  ロググループ {LOG_GROUP}: 作成")
    except logs.exceptions.ResourceAlreadyExistsException:
        print(f"  ロググループ {LOG_GROUP}: 既存")

    try:
        ecs.create_cluster(
            clusterName=CLUSTER,
            capacityProviders=["FARGATE"],
            settings=[{"name": "containerInsights", "value": "disabled"}],
        )
        print(f"  クラスタ {CLUSTER}: 作成")
    except Exception as e:  # noqa: BLE001
        print(f"  クラスタ {CLUSTER}: {type(e).__name__} (既存とみなして続行)")

    ecs.register_task_definition(
        family=TASK_FAMILY,
        networkMode="awsvpc",
        requiresCompatibilities=["FARGATE"],
        cpu="256",
        memory="512",
        executionRoleArn=exec_arn,
        taskRoleArn=task_arn,
        containerDefinitions=[
            {
                "name": "app",
                "image": "public.ecr.aws/amazonlinux/amazonlinux:2023",
                "essential": True,
                "command": ["sh", "-c", "sleep 86400"],
                "linuxParameters": {"initProcessEnabled": True},
                "logConfiguration": {
                    "logDriver": "awslogs",
                    "options": {
                        "awslogs-group": LOG_GROUP,
                        "awslogs-region": REGION,
                        "awslogs-stream-prefix": "ttp-test",
                    },
                },
            }
        ],
    )
    print(f"  タスク定義 {TASK_FAMILY}: 登録")

    resp = ecs.run_task(
        cluster=CLUSTER,
        taskDefinition=TASK_FAMILY,
        launchType="FARGATE",
        platformVersion="1.4.0",
        enableExecuteCommand=True,
        networkConfiguration={
            "awsvpcConfiguration": {
                "subnets": [subnet_id],
                "securityGroups": [sg_id],
                "assignPublicIp": "ENABLED",
            }
        },
        count=1,
    )
    if resp.get("failures"):
        print(f"  ! RunTask 失敗: {resp['failures']}")
        return {}
    task_arn = resp["tasks"][0]["taskArn"]
    print(f"  タスク起動: {task_arn.rsplit('/', 1)[-1]}")
    return {"task_arn": task_arn}


def wait_exec_agent(session, task_arn: str, timeout: int = 420) -> bool:
    ecs = session.client("ecs")
    started = time.time()
    while time.time() - started < timeout:
        try:
            task = ecs.describe_tasks(cluster=CLUSTER, tasks=[task_arn])["tasks"][0]
            if task.get("lastStatus") == "STOPPED":
                print(f"  ! タスクが停止しました: {task.get('stoppedReason')}")
                return False
            statuses = [
                a.get("lastStatus")
                for c in task.get("containers", [])
                for a in c.get("managedAgents", [])
                if a.get("name") == "ExecuteCommandAgent"
            ]
            print(f"  ... {task.get('lastStatus')} / agent={statuses}")
            if "RUNNING" in statuses:
                print(f"  ExecuteCommandAgent RUNNING を確認 ({int(time.time() - started)} 秒)")
                return True
        except Exception as e:  # noqa: BLE001
            print(f"  ... 待機中 ({type(e).__name__}: {e})")
        time.sleep(15)
    print("  ! ExecuteCommandAgent が RUNNING になりませんでした")
    return False


# ── コマンド ──────────────────────────────────────────


def cmd_create(session) -> int:
    print("=== 前提確認 ===")
    ec2 = session.client("ec2")
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "is-default", "Values": ["true"]}])["Vpcs"]
    if not vpcs:
        print("NG: デフォルト VPC がありません")
        return 1
    vpc_id = vpcs[0]["VpcId"]
    subnets = ec2.describe_subnets(
        Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
    )["Subnets"]
    subnet_id = subnets[0]["SubnetId"]
    print(f"  VPC={vpc_id} Subnet={subnet_id}")

    print("=== EC2 用リソース ===")
    role_arn = ensure_ec2_role(session)
    sg_id = ensure_sg(session, vpc_id)

    print("=== ECS 用リソース ===")
    # ECS 用 SG は EC2 と同じものを使う（インバウンドなし）
    state = load_state()
    state.update({"vpc_id": vpc_id, "subnet_id": subnet_id, "sg_id": sg_id})
    save_state(state)

    ecs_state = create_ecs(session, subnet_id, sg_id)
    state.update(ecs_state)
    save_state(state)

    print("=== EC2 インスタンス ===")
    ec2_state: dict = {}
    existing = state.get("instance_id")
    if existing:
        try:
            found = ec2.describe_instances(InstanceIds=[existing])["Reservations"][0][
                "Instances"
            ][0]
            if found["State"]["Name"] in ("running", "pending"):
                print(f"  既存のインスタンスを再利用: {existing}")
                ec2_state = {"instance_id": existing}
        except Exception:
            pass
    if not ec2_state:
        ec2_state = create_ec2(session, vpc_id, subnet_id, sg_id, role_arn)
    state.update(ec2_state)
    save_state(state)

    wait_ssm_online(session, ec2_state["instance_id"])
    if ecs_state.get("task_arn"):
        wait_exec_agent(session, ecs_state["task_arn"])

    print("\n=== 作成完了 ===")
    print(json.dumps(state, indent=2))
    print("\n削除するには: uv run python scripts/aws_test_env.py cleanup")
    return 0


def cmd_run_task(session) -> int:
    """Fargate タスクだけを 1 つ起動する（EC2 は作らない）。

    タスクは sleep 86400（约 24 時間）で動き続ける。
    """
    ecs = session.client("ecs")
    s = load_state()
    if not s.get("subnet_id") or not s.get("sg_id"):
        print("NG: 先に scripts/aws_test_env.py create を実行してください")
        return 1

    exec_arn, task_role_arn = ensure_ecs_roles(session)

    logs = session.client("logs")
    try:
        logs.create_log_group(logGroupName=LOG_GROUP)
    except Exception:
        pass

    registered = ecs.register_task_definition(
        family=TASK_FAMILY,
        networkMode="awsvpc",
        requiresCompatibilities=["FARGATE"],
        cpu="256",
        memory="512",
        executionRoleArn=exec_arn,
        taskRoleArn=task_role_arn,
        containerDefinitions=[
            {
                "name": "app",
                "image": "public.ecr.aws/amazonlinux/amazonlinux:2023",
                "essential": True,
                "command": ["sh", "-c", "sleep 86400"],
                "linuxParameters": {"initProcessEnabled": True},
                "logConfiguration": {
                    "logDriver": "awslogs",
                    "options": {
                        "awslogs-group": LOG_GROUP,
                        "awslogs-region": REGION,
                        "awslogs-stream-prefix": "ttp-test",
                    },
                },
            }
        ],
    )
    print(
        "  タスク定義:",
        registered["taskDefinition"]["taskDefinitionArn"].rsplit("/", 1)[-1],
    )

    resp = ecs.run_task(
        cluster=CLUSTER,
        taskDefinition=TASK_FAMILY,
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
        count=1,
    )
    if resp.get("failures"):
        print("  ! RunTask 失敗:", resp["failures"])
        return 1

    arn = resp["tasks"][0]["taskArn"]
    s["task_arn"] = arn
    save_state(s)
    print(f"  タスク起動: {arn.rsplit('/', 1)[-1]}")

    if not wait_exec_agent(session, arn):
        return 1

    print("\nTTP の「AWS から接続」で「更新」を押すと一覧に出ます。")
    return 0


def cmd_stop_task(session) -> int:
    """起動中の Fargate タスクだけを停止する。

    単発の run-task なので停止すれば再起動しない。
    サービスが残っている場合は、停止しても再起動されるので注意を促す。
    """
    ecs = session.client("ecs")

    try:
        services = ecs.list_services(cluster=CLUSTER).get("serviceArns", [])
    except Exception as e:  # noqa: BLE001
        print(f"クラスタ {CLUSTER} が見つかりません（{type(e).__name__}）")
        return 0

    if services:
        print("! サービスが残っています。停止しても再起動されます:")
        for arn in services:
            print("   ", arn.rsplit("/", 1)[-1])
        print("  cleanup でサービスごと削除してください。")
        return 1

    arns = ecs.list_tasks(cluster=CLUSTER, desiredStatus="RUNNING").get(
        "taskArns", []
    )
    if not arns:
        print("実行中のタスクはありません。")
        return 0

    for arn in arns:
        ecs.stop_task(cluster=CLUSTER, task=arn, reason="ttp test finished")
        print("停止:", arn.rsplit("/", 1)[-1])

    ecs.get_waiter("tasks_stopped").wait(cluster=CLUSTER, tasks=arns)
    print("停止を確認しました（サービスが無いため再起動しません）。")
    return 0


def cmd_status(session) -> int:
    state = load_state()
    print(json.dumps(state, indent=2) if state else "(状態ファイルなし)")
    from ttp.aws_inventory import list_ec2, list_ecs

    targets, _ = list_ec2(session)
    print(f"\nEC2 {len(targets)} 台")
    for t in targets:
        print(f"  {t.instance_id} {t.display_name} [{t.status_text}]")
    tasks, _ = list_ecs(session)
    print(f"ECS {len(tasks)} タスク")
    for t in tasks:
        print(f"  {t.cluster}/{t.service or '-'} {t.short_id} [{t.status_text}]")
    return 0


def cmd_cleanup(session) -> int:
    state = load_state()
    ec2 = session.client("ec2")
    ecs = session.client("ecs")
    iam = session.client("iam")
    logs = session.client("logs")

    print("=== ECS ===")
    try:
        arns = ecs.list_tasks(cluster=CLUSTER)["taskArns"]
        if arns:
            ecs.stop_task(cluster=CLUSTER, task=arns[0], reason="ttp test cleanup")
            print(f"  タスク停止: {len(arns)} 件")
            ecs.get_waiter("tasks_stopped").wait(cluster=CLUSTER, tasks=arns)
    except Exception as e:  # noqa: BLE001
        print(f"  (タスク停止スキップ: {type(e).__name__})")

    try:
        ecs.delete_cluster(cluster=CLUSTER)
        print(f"  クラスタ削除: {CLUSTER}")
    except Exception as e:  # noqa: BLE001
        print(f"  (クラスタ削除スキップ: {type(e).__name__})")

    try:
        task_defs = ecs.list_task_definitions(familyPrefix=TASK_FAMILY, status="ACTIVE")[
            "taskDefinitionArns"
        ]
        for td in task_defs:
            ecs.deregister_task_definition(taskDefinition=td)
            print(f"  タスク定義削除: {td.rsplit('/', 1)[-1]}")
    except Exception as e:  # noqa: BLE001
        print(f"  (タスク定義削除スキップ: {type(e).__name__})")

    try:
        logs.delete_log_group(logGroupName=LOG_GROUP)
        print(f"  ロググループ削除: {LOG_GROUP}")
    except Exception as e:  # noqa: BLE001
        print(f"  (ロググループ削除スキップ: {type(e).__name__})")

    for role_name, policies in (
        (TASK_ROLE, ["EcsExecPermissions"]),
        (EXEC_ROLE, []),
    ):
        try:
            for p in policies:
                iam.delete_role_policy(RoleName=role_name, PolicyName=p)
            for attached in iam.list_attached_role_policies(RoleName=role_name)[
                "AttachedPolicies"
            ]:
                iam.detach_role_policy(
                    RoleName=role_name, PolicyArn=attached["PolicyArn"]
                )
            iam.delete_role(RoleName=role_name)
            print(f"  IAM ロール削除: {role_name}")
        except Exception as e:  # noqa: BLE001
            print(f"  (IAM ロール削除スキップ: {role_name} {type(e).__name__})")

    print("=== EC2 ===")
    instance_id = state.get("instance_id")
    try:
        if instance_id:
            ec2.terminate_instances(InstanceIds=[instance_id])
            print(f"  インスタンス終了: {instance_id}")
            ec2.get_waiter("instance_terminated").wait(InstanceIds=[instance_id])
    except Exception as e:  # noqa: BLE001
        print(f"  (インスタンス終了スキップ: {type(e).__name__})")

    try:
        if EC2_PROFILE:
            iam.remove_role_from_instance_profile(
                InstanceProfileName=EC2_PROFILE, RoleName=EC2_ROLE
            )
    except Exception:
        pass
    try:
        iam.delete_instance_profile(InstanceProfileName=EC2_PROFILE)
        print(f"  インスタンスプロファイル削除: {EC2_PROFILE}")
    except Exception as e:  # noqa: BLE001
        print(f"  (プロファイル削除スキップ: {type(e).__name__})")
    try:
        iam.detach_role_policy(
            RoleName=EC2_ROLE,
            PolicyArn="arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
        )
        iam.delete_role(RoleName=EC2_ROLE)
        print(f"  IAM ロール削除: {EC2_ROLE}")
    except Exception as e:  # noqa: BLE001
        print(f"  (IAM ロール削除スキップ: {EC2_ROLE} {type(e).__name__})")

    try:
        sg_id = state.get("sg_id")
        if sg_id:
            ec2.delete_security_group(GroupId=sg_id)
            print(f"  セキュリティグループ削除: {sg_id}")
    except Exception as e:  # noqa: BLE001
        print(f"  (SG 削除スキップ: {type(e).__name__})")

    if STATE_FILE.is_file():
        STATE_FILE.unlink()
    print("\n=== 削除完了 ===")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=["create", "status", "cleanup", "run-task", "stop-task"],
    )
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    session = make_session()

    if args.action == "create":
        return cmd_create(session)
    if args.action == "status":
        return cmd_status(session)
    if args.action == "run-task":
        return cmd_run_task(session)
    if args.action == "stop-task":
        return cmd_stop_task(session)
    return cmd_cleanup(session)


if __name__ == "__main__":
    raise SystemExit(main())
