"""AWS リソース一覧取得モジュール

「今つながるもの」だけを抽出する。

- EC2 : SSM マネージドノード（PingStatus=Online）を接続可能とみなす
- ECS : タスクで ECS Exec が有効 かつ ExecuteCommandAgent が RUNNING

boto3 は遅延 import しているため、SSH だけ使うユーザーの起動時間には影響しない。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ttp.aws import describe_error


@dataclass
class Ec2Target:
    """SSM で接続できる可能性のある EC2 インスタンス。"""

    instance_id: str
    name: str = ""
    platform: str = ""
    ping_status: str = ""
    instance_type: str = ""
    private_ip: str = ""
    ssm_registered: bool = True

    @property
    def connectable(self) -> bool:
        return self.ssm_registered and self.ping_status == "Online"

    @property
    def display_name(self) -> str:
        return self.name or self.instance_id

    @property
    def status_text(self) -> str:
        if not self.ssm_registered:
            return "SSM 未登録"
        return self.ping_status or "不明"


@dataclass
class EcsTarget:
    """ECS Exec で接続できる可能性のあるタスク。"""

    cluster: str
    service: str
    task_arn: str
    task_definition: str = ""
    launch_type: str = ""
    started_at: str = ""
    exec_enabled: bool = False
    containers: list[str] = field(default_factory=list)
    runtime_ids: dict[str, str] = field(default_factory=dict)
    agent_status: str = ""

    @property
    def task_id(self) -> str:
        return self.task_arn.rsplit("/", 1)[-1]

    @property
    def short_id(self) -> str:
        return self.task_id[:12]

    @property
    def connectable(self) -> bool:
        return self.exec_enabled and bool(self.containers)

    @property
    def status_text(self) -> str:
        if self.connectable:
            return "Exec 可"
        if not self.exec_enabled:
            return "Exec 無効"
        return self.agent_status or "Agent 停止"


# ── EC2 ────────────────────────────────────────────────


def list_ec2(
    session: Any, include_unmanaged: bool = True
) -> tuple[list[Ec2Target], list[str]]:
    """SSM 経由で接続できる EC2 インスタンスを列挙する。

    Args:
        session: boto3.Session
        include_unmanaged: SSM 未登録の起動中インスタンスも一覧に含めるか

    Returns:
        (一覧, 警告メッセージ)
    """
    from botocore.exceptions import BotoCoreError, ClientError

    warnings: list[str] = []
    targets: dict[str, Ec2Target] = {}

    ssm = session.client("ssm")
    try:
        paginator = ssm.get_paginator("describe_instance_information")
        for page in paginator.paginate():
            for info in page.get("InstanceInformationList", []):
                # EC2 以外（オンプレのマネージドノード等）は対象外
                if info.get("ResourceType", "EC2Instance") != "EC2Instance":
                    continue
                instance_id = info.get("InstanceId", "")
                if not instance_id:
                    continue
                targets[instance_id] = Ec2Target(
                    instance_id=instance_id,
                    platform=info.get("PlatformName") or info.get("PlatformType", ""),
                    ping_status=info.get("PingStatus", ""),
                    ssm_registered=True,
                )
    except (ClientError, BotoCoreError) as e:
        warnings.append("SSM の一覧取得: " + describe_error(e))

    ec2 = session.client("ec2")
    try:
        paginator = ec2.get_paginator("describe_instances")
        for page in paginator.paginate(
            Filters=[{"Name": "instance-state-name", "Values": ["running"]}]
        ):
            for reservation in page.get("Reservations", []):
                for inst in reservation.get("Instances", []):
                    instance_id = inst.get("InstanceId", "")
                    if not instance_id:
                        continue
                    target = targets.get(instance_id)
                    if target is None:
                        if not include_unmanaged:
                            continue
                        target = Ec2Target(
                            instance_id=instance_id, ssm_registered=False
                        )
                        targets[instance_id] = target

                    name = ""
                    for tag in inst.get("Tags", []) or []:
                        if tag.get("Key") == "Name":
                            name = tag.get("Value", "")
                            break
                    target.name = name or target.name
                    target.instance_type = inst.get("InstanceType", "")
                    target.private_ip = inst.get("PrivateIpAddress", "") or ""
                    if not target.platform:
                        target.platform = inst.get("PlatformDetails", "") or ""
    except (ClientError, BotoCoreError) as e:
        warnings.append("EC2 の一覧取得: " + describe_error(e))

    result = sorted(
        targets.values(),
        key=lambda t: (not t.connectable, t.display_name.lower()),
    )
    return result, warnings


# ── ECS ────────────────────────────────────────────────


def _describe_tasks_chunk(ecs: Any, cluster_arn: str, task_arns: list[str]) -> list[dict]:
    resp = ecs.describe_tasks(cluster=cluster_arn, tasks=task_arns)
    return resp.get("tasks", []) or []


def list_ecs(
    session: Any, max_tasks_per_cluster: int = 500
) -> tuple[list[EcsTarget], list[str]]:
    """ECS Exec で接続できるタスクを列挙する。

    Returns:
        (一覧, 警告メッセージ)
    """
    from botocore.exceptions import BotoCoreError, ClientError

    warnings: list[str] = []
    targets: list[EcsTarget] = []

    ecs = session.client("ecs")

    try:
        cluster_arns: list[str] = []
        for page in ecs.get_paginator("list_clusters").paginate():
            cluster_arns.extend(page.get("clusterArns", []) or [])
    except (ClientError, BotoCoreError) as e:
        return [], ["ECS クラスタの一覧取得: " + describe_error(e)]

    for cluster_arn in cluster_arns:
        cluster = cluster_arn.rsplit("/", 1)[-1]

        try:
            task_arns: list[str] = []
            for page in ecs.get_paginator("list_tasks").paginate(
                cluster=cluster_arn, desiredStatus="RUNNING"
            ):
                task_arns.extend(page.get("taskArns", []) or [])
                if len(task_arns) >= max_tasks_per_cluster:
                    task_arns = task_arns[:max_tasks_per_cluster]
                    warnings.append(
                        f"[{cluster}] タスクが多いため先頭 {max_tasks_per_cluster} 件のみ表示しています。"
                    )
                    break
        except (ClientError, BotoCoreError) as e:
            warnings.append(f"[{cluster}] タスクの一覧取得: " + describe_error(e))
            continue

        for i in range(0, len(task_arns), 100):
            chunk = task_arns[i : i + 100]
            try:
                tasks = _describe_tasks_chunk(ecs, cluster_arn, chunk)
            except (ClientError, BotoCoreError) as e:
                warnings.append(f"[{cluster}] タスク詳細の取得: " + describe_error(e))
                break

            for task in tasks:
                group = task.get("group", "") or ""
                service = group.split(":", 1)[1] if group.startswith("service:") else ""

                containers: list[str] = []
                runtime_ids: dict[str, str] = {}
                agent_statuses: list[str] = []
                for container in task.get("containers", []) or []:
                    for agent in container.get("managedAgents", []) or []:
                        if agent.get("name") != "ExecuteCommandAgent":
                            continue
                        status = agent.get("lastStatus", "") or ""
                        agent_statuses.append(status)
                        if status == "RUNNING":
                            name = container.get("name", "")
                            containers.append(name)
                            runtime_id = container.get("runtimeId")
                            if name and runtime_id:
                                runtime_ids[name] = runtime_id

                started = task.get("startedAt")

                targets.append(
                    EcsTarget(
                        cluster=cluster,
                        service=service,
                        task_arn=task.get("taskArn", ""),
                        task_definition=(task.get("taskDefinitionArn", "") or "").rsplit(
                            "/", 1
                        )[-1],
                        launch_type=task.get("launchType")
                        or task.get("capacityProviderName")
                        or "",
                        started_at=started.strftime("%Y-%m-%d %H:%M")
                        if hasattr(started, "strftime")
                        else "",
                        exec_enabled=bool(task.get("enableExecuteCommand")),
                        containers=[c for c in containers if c],
                        runtime_ids=runtime_ids,
                        agent_status=",".join(agent_statuses),
                    )
                )

    # 接続可能なものを先頭に、クラスタ / サービス順、
    # 同一サービス内では新しいタスク（startedAt）を上に
    targets.sort(key=lambda t: t.started_at, reverse=True)
    targets.sort(key=lambda t: (not t.connectable, t.cluster, t.service))
    return targets, warnings
