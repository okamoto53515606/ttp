"""起動前のスモークチェック

- 全モジュールが import できるか
- 必要な外部ツールが見つかるか
- PowerShell 引用などの純粋関数が壊れていないか

CI / リリース前の自動テストから呼び出す。
終了コード 0 = OK, 1 = NG
"""

from __future__ import annotations

import sys
from pathlib import Path

# Windows の CI（cp1252 コンソール）でも日本語を出力できるようにする
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    errors: list[str] = []

    try:
        import ttp.app  # noqa: F401
        import ttp.aws  # noqa: F401
        import ttp.aws_browser  # noqa: F401
        import ttp.aws_inventory  # noqa: F401
        import ttp.dialogs  # noqa: F401
        import ttp.models  # noqa: F401
        import ttp.session  # noqa: F401
        import ttp.storage  # noqa: F401
        import ttp.terminal  # noqa: F401
        import ttp.teraterm  # noqa: F401
    except Exception as e:  # noqa: BLE001
        errors.append(f"import 失敗: {type(e).__name__}: {e}")

    if not errors:
        from ttp import session, storage, terminal

        # シングルクォートのエスケープ
        assert terminal._ps_quote("a'b") == "'a''b'", "PowerShell 引用が不正"
        assert terminal._ps_quote("plain") == "'plain'", "PowerShell 引用が不正"
        assert terminal._ps_exe(r"C:\path with space\aws.exe").startswith("&"), (
            "パス引用が不正"
        )
        print("quote/exe: OK")

        # プラグイン引数の組み立て（存在しないパス -> 旧形式フォールバック）
        argv, extra = session._build_argv(
            "no-such-plugin.exe",
            {"SessionId": "s", "TokenValue": "t", "StreamUrl": "u"},
            "ap-northeast-1",
            {"Target": "i-0123456789abcdef0"},
            "https://ssm.ap-northeast-1.amazonaws.com",
        )
        assert argv[2] == "StartSession", "operation 名が不正"
        assert argv[3] == "", "profile は空文字である必要があります"
        assert extra == {}, "未対応プラグインでは環境変数を付けない"
        assert len(argv) == 6, "プラグインの引数は 6 個です"
        print("plugin argv: OK")

        # バージョン文字列の解釈
        assert session.plugin_version("no-such-plugin.exe") is None
        print("plugin version parse: OK")

        # アイコン資源（exe 用 .ico / ウィンドウ用 .png）
        res_dir = storage.get_resource_dir()
        for name in ("ttp.ico", "ttp.png"):
            if not (res_dir / name).is_file():
                errors.append(f"アイコンがありません: {res_dir / name}（scripts/make_icon.py で生成）")
        if not errors:
            print("icon resources: OK")

        # セッション情報のキー形式（SSM=PascalCase / ECS=camelCase）
        camel = {"sessionId": "s", "tokenValue": "t", "streamUrl": "u"}
        assert session._validate_session(camel, normalize=False) is camel
        assert session.session_id_of(camel) == "s"

        pascal = {"SessionId": "s2", "TokenValue": "t2", "StreamUrl": "u2"}
        normalized = session._validate_session(pascal, normalize=True)
        assert normalized == {
            "SessionId": "s2",
            "TokenValue": "t2",
            "StreamUrl": "u2",
        }, "SSM のセッション情報が正規化されていない"
        print("session payload: OK")

        missing = terminal.missing_prerequisites()
        if missing:
            print("警告: 見つからない外部ツール:", ", ".join(missing))
        else:
            print("外部ツール: OK")

        print("Windows Terminal:", terminal.find_windows_terminal() or "見つかりません")
        print("AWS CLI:", terminal.find_aws_cli() or "見つかりません")
        print(
            "Session Manager プラグイン:",
            terminal.find_session_manager_plugin() or "見つかりません",
        )

    if errors:
        for e in errors:
            print("NG:", e)
        return 1

    print("スモークチェック: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
