"""AWS 接続ブラウザ

SSM / ECS Exec で「今つながる」EC2 インスタンスと Fargate タスクを一覧し、
ダブルクリックで Windows Terminal のセッションを開く。
"""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox, ttk

from ttp import session, terminal
from ttp.aws import AwsAuth, AwsError, describe_error
from ttp.aws_inventory import Ec2Target, EcsTarget, list_ec2, list_ecs
from ttp.dialogs import MfaCodeDialog, MissingToolsDialog, _center_window
from ttp.models import AwsSettings


# 自動更新の間隔（ミリ秒）
AUTO_REFRESH_MS = 30_000


class _ContainerDialog:
    """ECS Exec の対象コンテナを選ぶ小さなダイアログ。"""

    def __init__(self, parent: tk.Toplevel, containers: list[str]) -> None:
        self.result: str | None = None
        self._dlg = tk.Toplevel(parent)
        self._dlg.title("コンテナの選択")
        self._dlg.resizable(False, False)
        try:
            self._dlg.grab_set()
        except Exception:
            pass
        _center_window(self._dlg, 380, 240)

        frame = ttk.Frame(self._dlg, padding=20)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="接続するコンテナを選んでください。").pack(anchor=tk.W)
        self._var = tk.StringVar(value=containers[0])
        for name in containers:
            ttk.Radiobutton(frame, text=name, value=name, variable=self._var).pack(
                anchor=tk.W, padx=10, pady=2
            )

        btn = ttk.Frame(frame)
        btn.pack(pady=(16, 0))
        ttk.Button(btn, text="接続", command=self._ok, width=12).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(btn, text="キャンセル", command=self._cancel, width=12).pack(
            side=tk.LEFT, padx=4
        )

        self._dlg.protocol("WM_DELETE_WINDOW", self._cancel)
        self._dlg.wait_window()

    def _ok(self) -> None:
        self.result = self._var.get()
        self._dlg.destroy()

    def _cancel(self) -> None:
        self.result = None
        self._dlg.destroy()


class AwsBrowserWindow:
    """AWS 上の接続先を一覧するウィンドウ。"""

    def __init__(self, parent: tk.Tk, settings: AwsSettings) -> None:
        self._settings = settings
        self._auth = AwsAuth(settings, mfa_provider=self._ask_mfa)
        self._ec2: list[Ec2Target] = []
        self._ecs: list[EcsTarget] = []
        self._targets: dict[str, object] = {}
        self._busy = False
        self._warnings: list[str] = []
        self._auto_after_id: str | None = None

        self._dlg = tk.Toplevel(parent)
        self._dlg.title(
            f"AWS から接続 - {settings.region}"
        )
        self._dlg.geometry("940x560")
        self._dlg.minsize(720, 420)

        self._build_ui()
        _center_window(self._dlg, 940, 560)

        # 初回読み込み（先に必要ツールの案内を出す）
        self._dlg.after(150, self._initial_load)
        self._dlg.mainloop()

    # ── UI ────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = ttk.Frame(self._dlg, padding=10)
        outer.pack(fill=tk.BOTH, expand=True)

        toolbar = ttk.Frame(outer)
        toolbar.pack(fill=tk.X)

        self._refresh_btn = ttk.Button(
            toolbar, text="更新", command=self.refresh, width=8
        )
        self._refresh_btn.pack(side=tk.LEFT, padx=(0, 10))

        self._only_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            toolbar,
            text="接続可能のみ",
            variable=self._only_var,
            command=self._render,
        ).pack(side=tk.LEFT, padx=(0, 12))

        # 30 秒ごとに一覧を取り直す（サービスのタスク入れ替わりに追従）
        self._auto_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            toolbar,
            text="自動更新(30秒)",
            variable=self._auto_var,
            command=self._on_auto_toggle,
        ).pack(side=tk.LEFT, padx=(0, 16))

        ttk.Label(toolbar, text="検索:").pack(side=tk.LEFT)
        self._search_var = tk.StringVar()
        search = ttk.Entry(toolbar, textvariable=self._search_var, width=26)
        search.pack(side=tk.LEFT, padx=(4, 0))
        self._search_var.trace_add("write", lambda *_: self._render())

        self._region_label = ttk.Label(
            toolbar, text=f"リージョン: {self._settings.region}"
        )
        self._region_label.pack(side=tk.RIGHT)

        # ツリー
        tree_frame = ttk.Frame(outer)
        tree_frame.pack(fill=tk.BOTH, expand=True, pady=(8, 6))

        self._tree = ttk.Treeview(
            tree_frame,
            columns=("status", "detail"),
            show="tree headings",
            selectmode="browse",
        )
        self._tree.heading("#0", text="対象")
        self._tree.heading("status", text="状態")
        self._tree.heading("detail", text="詳細")
        self._tree.column("#0", width=320, minwidth=200)
        self._tree.column("status", width=90, minwidth=70, anchor=tk.CENTER)
        self._tree.column("detail", width=440, minwidth=200)

        scroll = ttk.Scrollbar(
            tree_frame, orient=tk.VERTICAL, command=self._tree.yview
        )
        self._tree.configure(yscrollcommand=scroll.set)
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self._tree.tag_configure("offline", foreground="#999999")
        self._tree.bind("<Double-1>", lambda e: self._on_connect())

        # ボタン
        btn_frame = ttk.Frame(outer)
        btn_frame.pack(fill=tk.X)
        ttk.Button(btn_frame, text="接続", command=self._on_connect, width=12).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(btn_frame, text="閉じる", command=self._on_close, width=12).pack(
            side=tk.LEFT, padx=2
        )

        self._status_var = tk.StringVar(value="準備中...")
        ttk.Label(
            outer, textvariable=self._status_var, font=("", 8), foreground="#555"
        ).pack(fill=tk.X, pady=(6, 0))

        self._dlg.bind("<F5>", lambda e: self.refresh())
        self._dlg.bind("<Return>", lambda e: self._on_connect())
        self._dlg.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── 前提ツールの確認 ─────────────────────────

    def _initial_load(self) -> None:
        """必要ツールの案内を出してから一覧を読み込む。"""
        self._check_tools(True)
        self.refresh()

    def _check_tools(self, notify: bool) -> bool:
        """必要な外部ツールが揃っているか確認する。

        揃っていなくても一覧の取得はできる（boto3 のみで動く）ので、
        接続操作のときだけ差し戻す。
        """
        tools = terminal.missing_tools()
        if not tools:
            return True

        names = "、".join(t["name"] for t in tools)
        self._status_var.set(f"接続には次のツールが必要です: {names}")
        if notify:
            MissingToolsDialog(self._dlg, tools)
        return False

    # ── 自動更新 ───────────────────────────────

    def _on_auto_toggle(self) -> None:
        if self._auto_var.get():
            self._schedule_auto()
        else:
            self._cancel_auto()

    def _schedule_auto(self) -> None:
        self._cancel_auto()
        try:
            self._auto_after_id = self._dlg.after(
                AUTO_REFRESH_MS, self._auto_tick
            )
        except tk.TclError:
            self._auto_after_id = None

    def _cancel_auto(self) -> None:
        if self._auto_after_id is None:
            return
        try:
            self._dlg.after_cancel(self._auto_after_id)
        except Exception:
            pass
        self._auto_after_id = None

    def _auto_tick(self) -> None:
        self._auto_after_id = None
        try:
            if not self._dlg.winfo_exists():
                return
        except tk.TclError:
            return

        if self._auto_var.get():
            if self._auth.is_expired():
                # MFA の再入力が必要なので、勝手にダイアログは出さない
                self._status_var.set(
                    "MFA の有効期限が切れました。「更新」を押して再入力してください。"
                )
            elif not self._busy:
                self.refresh(notify=False)
            self._schedule_auto()

    def _on_close(self) -> None:
        self._cancel_auto()
        self._dlg.destroy()

    # ── MFA ───────────────────────────────────────────

    def _ask_mfa(self, serial: str) -> str | None:
        return MfaCodeDialog(self._dlg, serial).result

    # ── 読み込み ──────────────────────────────────────

    def refresh(self, notify: bool = True) -> None:
        if self._busy:
            return

        # MFA は UI スレッドで入力させる（バックグラウンドだとダイアログを出せない）
        try:
            self._auth.credentials()
        except AwsError as e:
            if notify:
                messagebox.showerror("AWS 接続", str(e), parent=self._dlg)
            else:
                self._status_var.set("認証が必要です。「更新」を押してください。")
            return

        self._busy = True
        self._refresh_btn.configure(state="disabled")
        self._status_var.set("AWS から一覧を取得しています...")
        threading.Thread(target=self._load_worker, daemon=True).start()

    def _load_worker(self) -> None:
        try:
            session = self._auth.boto3_session()
            ec2_targets, ec2_warnings = list_ec2(session)
            ecs_targets, ecs_warnings = list_ecs(session)
        except Exception as e:  # noqa: BLE001
            self._post(self._on_load_error, e)
            return
        self._post(
            self._on_loaded,
            ec2_targets,
            ecs_targets,
            ec2_warnings + ecs_warnings,
        )

    def _post(self, func, *args) -> None:
        try:
            self._dlg.after(0, func, *args)
        except tk.TclError:
            pass  # ウィンドウが既に閉じられている

    def _on_load_error(self, exc: Exception) -> None:
        self._busy = False
        self._refresh_btn.configure(state="normal")
        self._status_var.set("一覧の取得に失敗しました。")
        messagebox.showerror(
            "AWS 接続", describe_error(exc), parent=self._dlg
        )

    def _on_loaded(
        self,
        ec2_targets: list[Ec2Target],
        ecs_targets: list[EcsTarget],
        warnings: list[str],
    ) -> None:
        self._busy = False
        self._refresh_btn.configure(state="normal")
        self._ec2 = ec2_targets
        self._ecs = ecs_targets
        self._warnings = warnings
        self._render()

    # ── 表示 ──────────────────────────────────────────

    def _matches(self, *values: str) -> bool:
        query = self._search_var.get().strip().lower()
        if not query:
            return True
        return any(query in (v or "").lower() for v in values)

    def _render(self) -> None:
        if not hasattr(self, "_tree"):
            return

        tree = self._tree
        tree.delete(*tree.get_children())
        self._targets.clear()

        only = self._only_var.get()

        ec2_items = [
            t
            for t in self._ec2
            if (t.connectable or not only)
            and self._matches(t.display_name, t.instance_id, t.private_ip)
        ]
        ec2_connectable = sum(1 for t in self._ec2 if t.connectable)
        ec2_hidden = len(self._ec2) - len(ec2_items)
        ec2_label = f"EC2 ({len(ec2_items)})"
        if ec2_hidden:
            ec2_label += f"  非表示 {ec2_hidden}"
        ec2_node = tree.insert(
            "", tk.END, iid="ec2", text=ec2_label, open=True
        )
        for target in ec2_items:
            iid = f"ec2:{target.instance_id}"
            detail = " / ".join(
                x
                for x in [target.instance_type, target.platform, target.private_ip]
                if x
            )
            tree.insert(
                ec2_node,
                tk.END,
                iid=iid,
                text=f"  {target.display_name}",
                values=(target.status_text, detail),
                tags=() if target.connectable else ("offline",),
            )
            self._targets[iid] = target

        ecs_items = [
            t
            for t in self._ecs
            if (t.connectable or not only)
            and self._matches(
                t.cluster, t.service, t.task_id, t.task_definition
            )
        ]
        ecs_connectable = sum(1 for t in self._ecs if t.connectable)
        ecs_hidden = len(self._ecs) - len(ecs_items)
        ecs_label = f"ECS ({len(ecs_items)})"
        if ecs_hidden:
            ecs_label += f"  非表示 {ecs_hidden}"
        ecs_node = tree.insert(
            "", tk.END, iid="ecs", text=ecs_label, open=True
        )

        grouped: dict[str, dict[str, list[EcsTarget]]] = {}
        for target in ecs_items:
            service = target.service or "(単発タスク)"
            grouped.setdefault(target.cluster, {}).setdefault(service, []).append(
                target
            )

        for cluster, services in grouped.items():
            total = sum(len(v) for v in services.values())
            cluster_iid = f"cluster:{cluster}"
            tree.insert(
                ecs_node,
                tk.END,
                iid=cluster_iid,
                text=f"  {cluster} ({total})",
                open=True,
            )
            for service, tasks in services.items():
                service_iid = f"service:{cluster}/{service}"
                tree.insert(
                    cluster_iid,
                    tk.END,
                    iid=service_iid,
                    text=f"    {service} ({len(tasks)})",
                    open=True,
                )
                for target in tasks:
                    iid = f"task:{target.task_arn}"
                    detail = " / ".join(
                        x
                        for x in [
                            target.task_definition,
                            target.launch_type,
                            target.started_at,
                            ",".join(target.containers),
                        ]
                        if x
                    )
                    tree.insert(
                        service_iid,
                        tk.END,
                        iid=iid,
                        text=f"      {target.short_id}",
                        values=(target.status_text, detail),
                        tags=() if target.connectable else ("offline",),
                    )
                    self._targets[iid] = target

        status = (
            f"EC2 {len(self._ec2)}台 (接続可 {ec2_connectable}) / "
            f"ECS {len(self._ecs)}タスク (接続可 {ecs_connectable})"
            f"  |  表示 {len(self._targets)} 件"
        )
        hidden = ec2_hidden + ecs_hidden
        if hidden:
            reason = "接続可能のみ ON" if only else "検索条件"
            status += f"  |  非表示 {hidden} 件（{reason}）"
        if self._warnings:
            status += f"  |  警告 {len(self._warnings)} 件"
        if self._busy:
            status = "読み込み中... " + status
        self._status_var.set(status)

    # ── 接続 ──────────────────────────────────────────

    def _on_connect(self) -> None:
        selection = self._tree.selection()
        if not selection:
            messagebox.showinfo(
                "選択",
                "EC2 インスタンスまたは ECS タスクを選択してください。",
                parent=self._dlg,
            )
            return

        target = self._targets.get(selection[0])
        if target is None:
            messagebox.showinfo(
                "選択",
                "EC2 インスタンスまたは ECS タスクを選択してください。",
                parent=self._dlg,
            )
            return

        missing = terminal.missing_prerequisites()
        if missing:
            self._check_tools(True)
            return

        try:
            if isinstance(target, Ec2Target):
                if terminal.backend() == "plugin":
                    session.open_ssm(
                        self._auth, target.instance_id, target.display_name
                    )
                else:
                    terminal.open_ssm_session(
                        self._auth, target.instance_id, target.display_name
                    )
                self._status_var.set(
                    f"SSM セッションを開きました: {target.display_name}"
                )
                return

            if isinstance(target, EcsTarget):
                if not target.connectable:
                    messagebox.showwarning(
                        "接続できません",
                        f"このタスクは接続できません ({target.status_text})。\n"
                        "ECS Exec を有効にして新しいタスクを起動してください。",
                        parent=self._dlg,
                    )
                    return

                if len(target.containers) == 1:
                    container = target.containers[0]
                else:
                    container = _ContainerDialog(self._dlg, target.containers).result
                    if not container:
                        return

                if terminal.backend() == "plugin":
                    session.open_ecs(
                        self._auth,
                        target.cluster,
                        target.task_arn,
                        container,
                        service=target.service,
                        runtime_id=target.runtime_ids.get(container, ""),
                    )
                else:
                    terminal.open_ecs_exec(
                        self._auth,
                        target.cluster,
                        target.task_arn,
                        container,
                        service=target.service,
                    )
                self._status_var.set(
                    f"ECS Exec セッションを開きました: {target.short_id}/{container}"
                )
        except terminal.TerminalError as e:
            messagebox.showerror("接続エラー", str(e), parent=self._dlg)
        except session.SessionError as e:
            messagebox.showerror("接続エラー", str(e), parent=self._dlg)
        except AwsError as e:
            messagebox.showerror("接続エラー", str(e), parent=self._dlg)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror(
                "接続エラー", describe_error(e), parent=self._dlg
            )