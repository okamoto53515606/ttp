"""ダイアログ UI モジュール

マスターパスワード設定/入力、接続先編集、設定、ttpmacro選択のダイアログ。
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from typing import Any

from ttp.models import Connection, AppSettings
from ttp.teraterm import search_ttpmacro


# ── ユーティリティ ──────────────────────────────────────────


def _center_window(win: tk.Toplevel | tk.Tk, width: int, height: int) -> None:
    """ウィンドウを画面中央に配置し、最前面にする。"""
    win.update_idletasks()
    x = (win.winfo_screenwidth() - width) // 2
    y = (win.winfo_screenheight() - height) // 2
    win.geometry(f"{width}x{height}+{x}+{y}")
    win.attributes("-topmost", True)
    win.after(100, lambda: win.attributes("-topmost", False))
    win.lift()
    win.focus_force()


def _setup_dialog(dlg: tk.Toplevel, parent: tk.Tk | tk.Toplevel) -> None:
    """ダイアログの共通セットアップ。"""
    # transientを使わない（親が透明/最小化時に子も消える問題を回避）
    try:
        dlg.grab_set()
    except Exception:
        pass


# ── マスターパスワード設定ダイアログ ────────────────────────


class SetMasterPasswordDialog:
    """初回起動時のマスターパスワード設定ダイアログ。"""

    def __init__(self, parent: tk.Tk) -> None:
        self.result: str | None = None
        self._dlg = tk.Toplevel(parent)
        self._dlg.title("マスターパスワードの設定")
        self._dlg.resizable(False, False)
        _setup_dialog(self._dlg, parent)

        _center_window(self._dlg, 420, 230)

        frame = ttk.Frame(self._dlg, padding=20)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(
            frame,
            text="接続情報を暗号化するための\nマスターパスワードを設定してください。",
            justify=tk.LEFT,
        ).grid(row=0, column=0, columnspan=2, sticky=tk.W, pady=(0, 15))

        ttk.Label(frame, text="パスワード:").grid(row=1, column=0, sticky=tk.E, padx=(0, 8))
        self._pw1 = ttk.Entry(frame, show="*", width=30)
        self._pw1.grid(row=1, column=1, sticky=tk.W)

        ttk.Label(frame, text="確認:").grid(row=2, column=0, sticky=tk.E, padx=(0, 8), pady=(8, 0))
        self._pw2 = ttk.Entry(frame, show="*", width=30)
        self._pw2.grid(row=2, column=1, sticky=tk.W, pady=(8, 0))

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=3, column=0, columnspan=2, pady=(20, 0))
        ttk.Button(btn_frame, text="設定", command=self._on_ok, width=12).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_frame, text="キャンセル", command=self._on_cancel, width=12).pack(side=tk.LEFT, padx=4)

        self._pw1.focus_set()
        self._dlg.bind("<Return>", lambda e: self._on_ok())
        self._dlg.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self._dlg.wait_window()

    def _on_ok(self) -> None:
        pw1 = self._pw1.get()
        pw2 = self._pw2.get()
        if not pw1:
            messagebox.showwarning("入力エラー", "パスワードを入力してください。", parent=self._dlg)
            return
        if len(pw1) < 4:
            messagebox.showwarning("入力エラー", "4文字以上のパスワードを設定してください。", parent=self._dlg)
            return
        if pw1 != pw2:
            messagebox.showwarning("入力エラー", "パスワードが一致しません。", parent=self._dlg)
            return
        self.result = pw1
        self._dlg.destroy()

    def _on_cancel(self) -> None:
        self.result = None
        self._dlg.destroy()


# ── マスターパスワード入力ダイアログ ────────────────────────


class EnterMasterPasswordDialog:
    """起動時のマスターパスワード入力ダイアログ。"""

    def __init__(self, parent: tk.Tk) -> None:
        self.result: str | None = None
        self._dlg = tk.Toplevel(parent)
        self._dlg.title("マスターパスワード")
        self._dlg.resizable(False, False)
        _setup_dialog(self._dlg, parent)

        _center_window(self._dlg, 380, 160)

        frame = ttk.Frame(self._dlg, padding=20)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="マスターパスワードを入力してください。").grid(
            row=0, column=0, columnspan=2, sticky=tk.W, pady=(0, 15)
        )

        ttk.Label(frame, text="パスワード:").grid(row=1, column=0, sticky=tk.E, padx=(0, 8))
        self._pw = ttk.Entry(frame, show="*", width=30)
        self._pw.grid(row=1, column=1, sticky=tk.W)

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=2, column=0, columnspan=2, pady=(20, 0))
        ttk.Button(btn_frame, text="OK", command=self._on_ok, width=12).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_frame, text="終了", command=self._on_cancel, width=12).pack(side=tk.LEFT, padx=4)

        self._pw.focus_set()
        self._dlg.bind("<Return>", lambda e: self._on_ok())
        self._dlg.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self._dlg.wait_window()

    def _on_ok(self) -> None:
        pw = self._pw.get()
        if not pw:
            messagebox.showwarning("入力エラー", "パスワードを入力してください。", parent=self._dlg)
            return
        self.result = pw
        self._dlg.destroy()

    def _on_cancel(self) -> None:
        self.result = None
        self._dlg.destroy()


# ── ttpmacro.exe 選択ダイアログ ─────────────────────────────


class SelectTTpmacroDialog:
    """ttpmacro.exe を選択するダイアログ。自動検索結果も表示する。"""

    def __init__(self, parent: tk.Tk) -> None:
        self.result: str | None = None
        self._dlg = tk.Toplevel(parent)
        self._dlg.title("ttpmacro.exe の選択")
        self._dlg.resizable(False, False)
        _setup_dialog(self._dlg, parent)

        _center_window(self._dlg, 520, 320)

        frame = ttk.Frame(self._dlg, padding=20)
        frame.pack(fill=tk.BOTH, expand=True)

        # 自動検索
        found = search_ttpmacro()

        if found:
            ttk.Label(
                frame,
                text="ttpmacro.exe が見つかりました。\n使用するものを選んでください。",
                justify=tk.LEFT,
            ).pack(anchor=tk.W, pady=(0, 10))

            self._var = tk.StringVar(value=found[0])
            for path in found:
                ttk.Radiobutton(
                    frame, text=path, variable=self._var, value=path
                ).pack(anchor=tk.W, padx=10, pady=2)
        else:
            ttk.Label(
                frame,
                text="ttpmacro.exe が見つかりませんでした。\n手動で選択してください。",
                justify=tk.LEFT,
            ).pack(anchor=tk.W, pady=(0, 10))
            self._var = tk.StringVar()

        ttk.Separator(frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=10)

        path_frame = ttk.Frame(frame)
        path_frame.pack(fill=tk.X)
        ttk.Label(path_frame, text="パス:").pack(side=tk.LEFT)
        self._path_entry = ttk.Entry(path_frame, textvariable=self._var, width=45)
        self._path_entry.pack(side=tk.LEFT, padx=5)
        ttk.Button(path_frame, text="参照...", command=self._browse).pack(side=tk.LEFT)

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(pady=(15, 0))
        ttk.Button(btn_frame, text="決定", command=self._on_ok, width=12).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_frame, text="キャンセル", command=self._on_cancel, width=12).pack(side=tk.LEFT, padx=4)

        self._dlg.bind("<Return>", lambda e: self._on_ok())
        self._dlg.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self._dlg.wait_window()

    def _browse(self) -> None:
        path = filedialog.askopenfilename(
            title="ttpmacro.exe を選択",
            filetypes=[("ttpmacro.exe", "ttpmacro.exe"), ("実行ファイル", "*.exe")],
            parent=self._dlg,
        )
        if path:
            self._var.set(path)

    def _on_ok(self) -> None:
        path = self._var.get().strip()
        if not path:
            messagebox.showwarning("入力エラー", "パスを指定してください。", parent=self._dlg)
            return
        if not os.path.isfile(path):
            messagebox.showwarning("入力エラー", "指定されたファイルが見つかりません。", parent=self._dlg)
            return
        self.result = path
        self._dlg.destroy()

    def _on_cancel(self) -> None:
        self.result = None
        self._dlg.destroy()


# ── 接続先 追加/編集ダイアログ ──────────────────────────────


class ConnectionDialog:
    """接続先を追加・編集するダイアログ。"""

    def __init__(self, parent: tk.Tk, connection: Connection | None = None) -> None:
        self.result: Connection | None = None
        self._conn = connection  # 編集時は既存のConnection
        self._key_path: str = connection.key_path if connection else ""

        self._dlg = tk.Toplevel(parent)
        self._dlg.title("接続先の編集" if connection else "接続先の追加")
        self._dlg.resizable(False, False)
        _setup_dialog(self._dlg, parent)

        _center_window(self._dlg, 500, 480)

        frame = ttk.Frame(self._dlg, padding=20)
        frame.pack(fill=tk.BOTH, expand=True)

        row = 0

        # 表示名
        ttk.Label(frame, text="表示名:").grid(row=row, column=0, sticky=tk.E, padx=(0, 8), pady=4)
        self._name = ttk.Entry(frame, width=35)
        self._name.grid(row=row, column=1, columnspan=2, sticky=tk.W, pady=4)
        row += 1

        # ホスト
        ttk.Label(frame, text="ホスト:").grid(row=row, column=0, sticky=tk.E, padx=(0, 8), pady=4)
        self._host = ttk.Entry(frame, width=35)
        self._host.grid(row=row, column=1, columnspan=2, sticky=tk.W, pady=4)
        row += 1

        # ポート
        ttk.Label(frame, text="ポート:").grid(row=row, column=0, sticky=tk.E, padx=(0, 8), pady=4)
        self._port = ttk.Entry(frame, width=8)
        self._port.grid(row=row, column=1, columnspan=2, sticky=tk.W, pady=4)
        row += 1

        # 認証方式
        ttk.Label(frame, text="認証方式:").grid(row=row, column=0, sticky=tk.E, padx=(0, 8), pady=4)
        auth_frame = ttk.Frame(frame)
        auth_frame.grid(row=row, column=1, columnspan=2, sticky=tk.W, pady=4)
        self._auth_var = tk.StringVar(value="password")
        ttk.Radiobutton(
            auth_frame, text="パスワード", variable=self._auth_var, value="password",
            command=self._on_auth_change,
        ).pack(side=tk.LEFT, padx=(0, 15))
        ttk.Radiobutton(
            auth_frame, text="公開鍵", variable=self._auth_var, value="publickey",
            command=self._on_auth_change,
        ).pack(side=tk.LEFT)
        row += 1

        # ユーザ名
        ttk.Label(frame, text="ユーザ名:").grid(row=row, column=0, sticky=tk.E, padx=(0, 8), pady=4)
        self._user = ttk.Entry(frame, width=25)
        self._user.grid(row=row, column=1, columnspan=2, sticky=tk.W, pady=4)
        row += 1

        # パスワード
        ttk.Label(frame, text="パスワード:").grid(row=row, column=0, sticky=tk.E, padx=(0, 8), pady=4)
        pw_frame = ttk.Frame(frame)
        pw_frame.grid(row=row, column=1, columnspan=2, sticky=tk.W, pady=4)
        self._passwd = ttk.Entry(pw_frame, show="*", width=25)
        self._passwd.pack(side=tk.LEFT)
        self._show_pw_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            pw_frame, text="表示", variable=self._show_pw_var,
            command=self._toggle_password,
        ).pack(side=tk.LEFT, padx=5)
        row += 1

        # 鍵ファイル
        ttk.Label(frame, text="鍵ファイル:").grid(row=row, column=0, sticky=tk.E, padx=(0, 8), pady=4)
        key_frame = ttk.Frame(frame)
        key_frame.grid(row=row, column=1, columnspan=2, sticky=tk.W, pady=4)
        self._key_label = ttk.Label(key_frame, text="(未選択)", width=30, anchor=tk.W)
        self._key_label.pack(side=tk.LEFT)
        self._key_browse_btn = ttk.Button(key_frame, text="参照...", command=self._browse_key)
        self._key_browse_btn.pack(side=tk.LEFT, padx=5)
        self._key_clear_btn = ttk.Button(key_frame, text="クリア", command=self._clear_key)
        self._key_clear_btn.pack(side=tk.LEFT)
        row += 1

        # 詳細設定セパレータ
        ttk.Separator(frame, orient=tk.HORIZONTAL).grid(
            row=row, column=0, columnspan=3, sticky=tk.EW, pady=10
        )
        row += 1

        ttk.Label(frame, text="▼ 詳細設定 (任意)", font=("", 9)).grid(
            row=row, column=0, columnspan=3, sticky=tk.W, pady=(0, 5)
        )
        row += 1

        # プロンプト
        ttk.Label(frame, text="プロンプト:").grid(row=row, column=0, sticky=tk.E, padx=(0, 8), pady=4)
        self._prompt = ttk.Entry(frame, width=35)
        self._prompt.grid(row=row, column=1, columnspan=2, sticky=tk.W, pady=4)
        row += 1

        # ログイン後コマンド
        ttk.Label(frame, text="ログイン後CMD:").grid(row=row, column=0, sticky=tk.E, padx=(0, 8), pady=4)
        self._sendln = ttk.Entry(frame, width=35)
        self._sendln.grid(row=row, column=1, columnspan=2, sticky=tk.W, pady=4)
        row += 1

        # ボタン
        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=row, column=0, columnspan=3, pady=(15, 0))
        ttk.Button(btn_frame, text="保存", command=self._on_save, width=12).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_frame, text="キャンセル", command=self._on_cancel, width=12).pack(side=tk.LEFT, padx=4)

        # 既存データの反映
        if connection:
            self._name.insert(0, connection.name)
            self._host.insert(0, connection.host)
            self._port.insert(0, str(connection.port))
            self._auth_var.set(connection.auth_type)
            self._user.insert(0, connection.username)
            self._passwd.insert(0, connection.password)
            self._prompt.insert(0, connection.prompt)
            self._sendln.insert(0, connection.sendln_param)
            if connection.key_path:
                self._key_label.config(text=Path(connection.key_path).name)
        else:
            self._port.insert(0, "22")

        self._on_auth_change()
        self._name.focus_set()
        self._dlg.bind("<Return>", lambda e: self._on_save())
        self._dlg.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self._dlg.wait_window()

    def _on_auth_change(self) -> None:
        is_key = self._auth_var.get() == "publickey"
        state = "normal" if is_key else "disabled"
        self._key_browse_btn.config(state=state)
        self._key_clear_btn.config(state=state)

    def _toggle_password(self) -> None:
        self._passwd.config(show="" if self._show_pw_var.get() else "*")

    def _browse_key(self) -> None:
        path = filedialog.askopenfilename(
            title="秘密鍵ファイルを選択",
            filetypes=[
                ("鍵ファイル", "*.pem *.ppk *.key *.id_rsa"),
                ("すべてのファイル", "*.*"),
            ],
            parent=self._dlg,
        )
        if path:
            if os.path.isfile(path):
                self._key_path = path
                self._key_label.config(text=Path(path).name)
            else:
                messagebox.showerror("エラー", "鍵ファイルが見つかりません。", parent=self._dlg)

    def _clear_key(self) -> None:
        self._key_path = ""
        self._key_label.config(text="(未選択)")

    def _on_save(self) -> None:
        name = self._name.get().strip()
        host = self._host.get().strip()
        port_str = self._port.get().strip()

        if not name:
            messagebox.showwarning("入力エラー", "表示名を入力してください。", parent=self._dlg)
            return
        if not host:
            messagebox.showwarning("入力エラー", "ホストを入力してください。", parent=self._dlg)
            return
        try:
            port = int(port_str)
        except ValueError:
            messagebox.showwarning("入力エラー", "ポートは数値で入力してください。", parent=self._dlg)
            return

        auth_type = self._auth_var.get()
        if auth_type == "publickey" and not self._key_path:
            messagebox.showwarning("入力エラー", "公開鍵認証の場合は鍵ファイルを選択してください。", parent=self._dlg)
            return

        from datetime import datetime

        now = datetime.now().isoformat()
        conn_id = self._conn.id if self._conn else Connection().id

        self.result = Connection(
            name=name,
            host=host,
            port=port,
            auth_type=auth_type,
            username=self._user.get().strip(),
            password=self._passwd.get(),
            key_path=self._key_path,
            prompt=self._prompt.get().strip(),
            sendln_param=self._sendln.get().strip(),
            id=conn_id,
            created_at=self._conn.created_at if self._conn else now,
            updated_at=now,
        )
        self._dlg.destroy()

    def _on_cancel(self) -> None:
        self.result = None
        self._dlg.destroy()


# ── 設定ダイアログ ──────────────────────────────────────────


class SettingsDialog:
    """アプリ設定ダイアログ。"""

    def __init__(self, parent: tk.Tk, settings: AppSettings) -> None:
        self.result: AppSettings | None = None
        self._dlg = tk.Toplevel(parent)
        self._dlg.title("設定")
        self._dlg.resizable(False, False)
        _setup_dialog(self._dlg, parent)

        _center_window(self._dlg, 520, 200)

        frame = ttk.Frame(self._dlg, padding=20)
        frame.pack(fill=tk.BOTH, expand=True)

        # ttpmacro パス
        ttk.Label(frame, text="ttpmacro.exe:").grid(row=0, column=0, sticky=tk.E, padx=(0, 8), pady=4)
        self._ttpmacro_var = tk.StringVar(value=settings.ttpmacro_path)
        ttk.Entry(frame, textvariable=self._ttpmacro_var, width=40).grid(row=0, column=1, sticky=tk.W, pady=4)
        ttk.Button(frame, text="参照...", command=self._browse_ttpmacro).grid(row=0, column=2, padx=4, pady=4)

        # ログ保存先
        ttk.Label(frame, text="ログ保存先:").grid(row=1, column=0, sticky=tk.E, padx=(0, 8), pady=4)
        self._logdir_var = tk.StringVar(value=settings.log_dir)
        ttk.Entry(frame, textvariable=self._logdir_var, width=40).grid(row=1, column=1, sticky=tk.W, pady=4)
        ttk.Button(frame, text="参照...", command=self._browse_logdir).grid(row=1, column=2, padx=4, pady=4)

        # ボタン
        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=3, column=0, columnspan=3, pady=(20, 0))
        ttk.Button(btn_frame, text="保存", command=self._on_save, width=12).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_frame, text="キャンセル", command=self._on_cancel, width=12).pack(side=tk.LEFT, padx=4)

        self._dlg.bind("<Return>", lambda e: self._on_save())
        self._dlg.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self._dlg.wait_window()

    def _browse_ttpmacro(self) -> None:
        path = filedialog.askopenfilename(
            title="ttpmacro.exe を選択",
            filetypes=[("ttpmacro.exe", "ttpmacro.exe"), ("実行ファイル", "*.exe")],
            parent=self._dlg,
        )
        if path:
            self._ttpmacro_var.set(path)

    def _browse_logdir(self) -> None:
        path = filedialog.askdirectory(title="ログ保存先を選択", parent=self._dlg)
        if path:
            self._logdir_var.set(path)

    def _on_save(self) -> None:
        ttpmacro = self._ttpmacro_var.get().strip()
        if ttpmacro and not os.path.isfile(ttpmacro):
            messagebox.showwarning(
                "入力エラー",
                "指定された ttpmacro.exe が見つかりません。",
                parent=self._dlg,
            )
            return
        self.result = AppSettings(
            ttpmacro_path=ttpmacro,
            log_dir=self._logdir_var.get().strip(),
        )
        self._dlg.destroy()

    def _on_cancel(self) -> None:
        self.result = None
        self._dlg.destroy()


# ── マスターパスワード変更ダイアログ ────────────────────────


class ChangeMasterPasswordDialog:
    """マスターパスワード変更ダイアログ。"""

    def __init__(self, parent: tk.Tk) -> None:
        self.old_password: str | None = None
        self.new_password: str | None = None

        self._dlg = tk.Toplevel(parent)
        self._dlg.title("マスターパスワードの変更")
        self._dlg.resizable(False, False)
        _setup_dialog(self._dlg, parent)

        _center_window(self._dlg, 420, 260)

        frame = ttk.Frame(self._dlg, padding=20)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="現在のパスワード:").grid(row=0, column=0, sticky=tk.E, padx=(0, 8), pady=4)
        self._old = ttk.Entry(frame, show="*", width=30)
        self._old.grid(row=0, column=1, sticky=tk.W, pady=4)

        ttk.Label(frame, text="新しいパスワード:").grid(row=1, column=0, sticky=tk.E, padx=(0, 8), pady=4)
        self._new1 = ttk.Entry(frame, show="*", width=30)
        self._new1.grid(row=1, column=1, sticky=tk.W, pady=4)

        ttk.Label(frame, text="新パスワード(確認):").grid(row=2, column=0, sticky=tk.E, padx=(0, 8), pady=4)
        self._new2 = ttk.Entry(frame, show="*", width=30)
        self._new2.grid(row=2, column=1, sticky=tk.W, pady=4)

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=3, column=0, columnspan=2, pady=(20, 0))
        ttk.Button(btn_frame, text="変更", command=self._on_ok, width=12).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_frame, text="キャンセル", command=self._on_cancel, width=12).pack(side=tk.LEFT, padx=4)

        self._old.focus_set()
        self._dlg.bind("<Return>", lambda e: self._on_ok())
        self._dlg.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self._dlg.wait_window()

    def _on_ok(self) -> None:
        old_pw = self._old.get()
        new_pw1 = self._new1.get()
        new_pw2 = self._new2.get()
        if not old_pw or not new_pw1:
            messagebox.showwarning("入力エラー", "すべての欄を入力してください。", parent=self._dlg)
            return
        if len(new_pw1) < 4:
            messagebox.showwarning("入力エラー", "4文字以上のパスワードを設定してください。", parent=self._dlg)
            return
        if new_pw1 != new_pw2:
            messagebox.showwarning("入力エラー", "新しいパスワードが一致しません。", parent=self._dlg)
            return
        self.old_password = old_pw
        self.new_password = new_pw1
        self._dlg.destroy()

    def _on_cancel(self) -> None:
        self.old_password = None
        self.new_password = None
        self._dlg.destroy()
