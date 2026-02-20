"""TTP メインアプリケーション

メインウィンドウの構築、接続リスト管理、テラターム起動を行う。
"""

from __future__ import annotations

import copy
import os
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox

from ttp import __version__
from ttp.models import Connection, AppSettings
from ttp.storage import (
    MasterStore,
    ConnectionStore,
    SettingsStore,
    get_log_dir,
)
from ttp.teraterm import launch_connection
from ttp.dialogs import (
    SetMasterPasswordDialog,
    EnterMasterPasswordDialog,
    SelectTTpmacroDialog,
    ConnectionDialog,
    SettingsDialog,
    ChangeMasterPasswordDialog,
)


class TTPApp:
    """メインアプリケーション"""

    def __init__(self) -> None:
        self._root = tk.Tk()
        self._root.title("TTP - Tera Term Pilot")
        # 認証中は非表示（withdrawではなく画面外に小さく配置）
        self._root.geometry("1x1+-100+-100")
        self._root.attributes("-alpha", 0)  # 完全透明

        self._master_store = MasterStore()
        self._conn_store = ConnectionStore()
        self._settings_store = SettingsStore()

        self._key: bytes = b""  # マスターパスワードから導出した鍵
        self._connections: list[Connection] = []
        self._settings: AppSettings = AppSettings()

    def run(self) -> None:
        """アプリを起動する。"""
        # マスター認証
        if not self._authenticate():
            self._root.destroy()
            return

        # 設定とデータを読み込み
        self._settings = self._settings_store.load()
        self._connections = self._conn_store.load(self._key)

        # ttpmacro パスが未設定なら選択ダイアログ
        if not self._settings.ttpmacro_path or not os.path.isfile(
            self._settings.ttpmacro_path
        ):
            self._select_ttpmacro()
            if not self._settings.ttpmacro_path:
                self._root.destroy()
                return

        # メインウィンドウ構築
        self._build_ui()
        # 通常ウィンドウに復帰・画面中央に配置
        w, h = 720, 480
        self._root.attributes("-alpha", 1.0)  # 不透明に戻す
        self._root.update_idletasks()
        x = (self._root.winfo_screenwidth() - w) // 2
        y = (self._root.winfo_screenheight() - h) // 2
        self._root.geometry(f"{w}x{h}+{x}+{y}")
        self._root.minsize(600, 380)
        self._root.deiconify()
        self._root.lift()
        self._root.focus_force()
        self._refresh_list()
        self._root.mainloop()

    # ── 認証 ────────────────────────────────────────────

    def _authenticate(self) -> bool:
        """マスターパスワードの設定 or 入力を行う。"""
        if not self._master_store.exists():
            # 初回: パスワード設定
            dlg = SetMasterPasswordDialog(self._root)
            if dlg.result is None:
                return False
            self._key = self._master_store.setup(dlg.result)
            return True
        else:
            # 既存: パスワード入力 (最大3回)
            for attempt in range(3):
                dlg = EnterMasterPasswordDialog(self._root)
                if dlg.result is None:
                    return False
                key = self._master_store.unlock(dlg.result)
                if key is not None:
                    self._key = key
                    return True
                remaining = 2 - attempt
                if remaining > 0:
                    messagebox.showerror(
                        "認証エラー",
                        f"パスワードが違います。\nあと{remaining}回試行できます。",
                    )
                else:
                    messagebox.showerror("認証エラー", "認証に失敗しました。")
            return False

    def _select_ttpmacro(self) -> None:
        """ttpmacro.exe を選択する。"""
        dlg = SelectTTpmacroDialog(self._root)
        if dlg.result:
            self._settings.ttpmacro_path = dlg.result
            if not self._settings.log_dir:
                self._settings.log_dir = str(get_log_dir())
            self._settings_store.save(self._settings)

    # ── UI 構築 ──────────────────────────────────────────

    def _build_ui(self) -> None:
        root = self._root
        root.title(f"TTP - Tera Term Pilot v{__version__}")
        root.geometry("720x480")
        root.minsize(600, 380)

        # メニューバー
        menubar = tk.Menu(root)
        root.config(menu=menubar)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="設定...", command=self._on_settings, accelerator="Ctrl+,")
        file_menu.add_command(
            label="マスターパスワード変更...", command=self._on_change_password
        )
        file_menu.add_separator()
        file_menu.add_command(label="終了", command=root.quit, accelerator="Alt+F4")
        menubar.add_cascade(label="ファイル", menu=file_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(
            label=f"TTP v{__version__} について",
            command=lambda: messagebox.showinfo(
                "TTP について",
                f"TTP - Tera Term Pilot v{__version__}\n\n"
                "テラタームマクロをGUIで管理・実行するツール。\n"
                "接続情報は暗号化して保存されます。\n\n"
                "License: MIT",
            ),
        )
        menubar.add_cascade(label="ヘルプ", menu=help_menu)

        # メインフレーム
        main_frame = ttk.Frame(root, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # ── 接続先一覧 ──
        list_label = ttk.Label(main_frame, text="接続先一覧", font=("", 11, "bold"))
        list_label.pack(anchor=tk.W, pady=(0, 5))

        # Treeview + スクロールバー
        tree_frame = ttk.Frame(main_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        columns = ("name", "host", "port", "auth", "user")
        self._tree = ttk.Treeview(
            tree_frame, columns=columns, show="headings", selectmode="browse"
        )
        self._tree.heading("name", text="名前", command=lambda: self._sort_column("name"))
        self._tree.heading("host", text="ホスト", command=lambda: self._sort_column("host"))
        self._tree.heading("port", text="ポート", command=lambda: self._sort_column("port"))
        self._tree.heading("auth", text="認証", command=lambda: self._sort_column("auth"))
        self._tree.heading("user", text="ユーザ", command=lambda: self._sort_column("user"))

        self._tree.column("name", width=150, minwidth=80)
        self._tree.column("host", width=220, minwidth=100)
        self._tree.column("port", width=60, minwidth=40, anchor=tk.CENTER)
        self._tree.column("auth", width=50, minwidth=40, anchor=tk.CENTER)
        self._tree.column("user", width=120, minwidth=60)

        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self._tree.yview)
        self._tree.configure(yscrollcommand=scrollbar.set)

        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # ダブルクリックで接続
        self._tree.bind("<Double-1>", lambda e: self._on_connect())

        # ── ボタン ──
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, pady=(10, 5))

        ttk.Button(btn_frame, text="接続", command=self._on_connect, width=10).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(btn_frame, text="新規追加", command=self._on_add, width=10).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(btn_frame, text="編集", command=self._on_edit, width=10).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(btn_frame, text="複製", command=self._on_duplicate, width=10).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(btn_frame, text="削除", command=self._on_delete, width=10).pack(
            side=tk.LEFT, padx=2
        )

        # 右側ボタン
        ttk.Button(
            btn_frame, text="ログフォルダを開く", command=self._on_open_logs, width=16
        ).pack(side=tk.RIGHT, padx=2)

        # ── ステータスバー ──
        status_frame = ttk.Frame(main_frame)
        status_frame.pack(fill=tk.X, pady=(5, 0))

        self._status_var = tk.StringVar()
        ttk.Label(status_frame, textvariable=self._status_var, font=("", 8)).pack(
            side=tk.LEFT
        )

        # キーボードショートカット
        root.bind("<Control-n>", lambda e: self._on_add())
        root.bind("<Control-N>", lambda e: self._on_add())
        root.bind("<Delete>", lambda e: self._on_delete())
        root.bind("<Control-e>", lambda e: self._on_edit())
        root.bind("<Control-E>", lambda e: self._on_edit())
        root.bind("<Control-d>", lambda e: self._on_duplicate())
        root.bind("<Control-D>", lambda e: self._on_duplicate())
        root.bind("<Control-l>", lambda e: self._on_open_logs())
        root.bind("<Control-L>", lambda e: self._on_open_logs())
        root.bind("<Control-comma>", lambda e: self._on_settings())
        root.bind("<Return>", lambda e: self._on_connect())

        self._sort_reverse = False

    # ── リスト操作 ────────────────────────────────────────

    def _refresh_list(self) -> None:
        """Treeview を現在の接続リストで更新する。"""
        self._tree.delete(*self._tree.get_children())
        for i, conn in enumerate(self._connections):
            self._tree.insert(
                "",
                tk.END,
                iid=str(i),
                values=(conn.name, conn.host, conn.port, conn.display_auth, conn.username),
            )
        self._update_status()

    def _update_status(self) -> None:
        n = len(self._connections)
        ttpmacro = self._settings.ttpmacro_path or "(未設定)"
        self._status_var.set(f"接続先: {n}件  |  ttpmacro: {ttpmacro}")

    def _get_selected_index(self) -> int | None:
        sel = self._tree.selection()
        if not sel:
            return None
        return int(sel[0])

    def _save_connections(self) -> None:
        self._conn_store.save(self._connections, self._key)

    def _sort_column(self, col: str) -> None:
        """列ヘッダークリックでソート。"""
        col_map = {"name": "name", "host": "host", "port": "port", "auth": "auth_type", "user": "username"}
        attr = col_map.get(col, col)
        self._connections.sort(key=lambda c: getattr(c, attr, ""), reverse=self._sort_reverse)
        self._sort_reverse = not self._sort_reverse
        self._refresh_list()

    # ── イベントハンドラ ──────────────────────────────────

    def _on_connect(self) -> None:
        idx = self._get_selected_index()
        if idx is None:
            messagebox.showinfo("選択", "接続先を選択してください。")
            return
        conn = self._connections[idx]

        if not os.path.isfile(self._settings.ttpmacro_path):
            messagebox.showerror(
                "エラー",
                "ttpmacro.exe が見つかりません。\n設定で正しいパスを指定してください。",
            )
            return

        proc = launch_connection(conn, self._settings)
        if proc is None:
            messagebox.showerror("エラー", "接続の起動に失敗しました。")
        # 起動成功: テラタームが開く (別プロセス)

    def _on_add(self) -> None:
        dlg = ConnectionDialog(self._root)
        if dlg.result:
            self._connections.append(dlg.result)
            self._save_connections()
            self._refresh_list()
            # 追加した項目を選択
            last_idx = len(self._connections) - 1
            self._tree.selection_set(str(last_idx))
            self._tree.see(str(last_idx))

    def _on_edit(self) -> None:
        idx = self._get_selected_index()
        if idx is None:
            messagebox.showinfo("選択", "編集する接続先を選択してください。")
            return
        conn = self._connections[idx]
        dlg = ConnectionDialog(self._root, connection=conn)
        if dlg.result:
            self._connections[idx] = dlg.result
            self._save_connections()
            self._refresh_list()
            self._tree.selection_set(str(idx))

    def _on_duplicate(self) -> None:
        idx = self._get_selected_index()
        if idx is None:
            messagebox.showinfo("選択", "複製する接続先を選択してください。")
            return
        conn = copy.deepcopy(self._connections[idx])
        conn.id = Connection().id  # 新しいID
        conn.name = f"{conn.name} (コピー)"
        from datetime import datetime

        conn.created_at = datetime.now().isoformat()
        conn.updated_at = conn.created_at
        self._connections.append(conn)
        self._save_connections()
        self._refresh_list()
        last_idx = len(self._connections) - 1
        self._tree.selection_set(str(last_idx))
        self._tree.see(str(last_idx))

    def _on_delete(self) -> None:
        idx = self._get_selected_index()
        if idx is None:
            messagebox.showinfo("選択", "削除する接続先を選択してください。")
            return
        conn = self._connections[idx]
        if messagebox.askyesno("確認", f"「{conn.name}」を削除しますか？"):
            self._connections.pop(idx)
            self._save_connections()
            self._refresh_list()

    def _on_open_logs(self) -> None:
        log_dir = get_log_dir(self._settings)
        try:
            os.startfile(str(log_dir))
        except Exception:
            messagebox.showerror("エラー", f"ログフォルダを開けませんでした。\n{log_dir}")

    def _on_settings(self) -> None:
        dlg = SettingsDialog(self._root, self._settings)
        if dlg.result:
            self._settings = dlg.result
            self._settings_store.save(self._settings)
            self._update_status()

    def _on_change_password(self) -> None:
        dlg = ChangeMasterPasswordDialog(self._root)
        if dlg.old_password is None or dlg.new_password is None:
            return

        old_key = self._master_store.unlock(dlg.old_password)
        if old_key is None:
            messagebox.showerror("エラー", "現在のパスワードが正しくありません。")
            return

        new_key = self._master_store.setup(dlg.new_password)
        self._conn_store.re_encrypt(old_key, new_key)
        self._key = new_key
        messagebox.showinfo("完了", "マスターパスワードを変更しました。")


def main() -> None:
    """エントリーポイント"""
    app = TTPApp()
    app.run()
