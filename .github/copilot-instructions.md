# ttp - Tera Term Pilot

Windows 向けの Tkinter アプリ。Tera Term マクロ（SSH）と AWS セッション
（SSM / ECS Exec）を GUI から起動する。

## 構成

```
src/ttp/
  app.py            メインウィンドウ
  dialogs.py        各ダイアログ
  models.py         Connection / AppSettings / AwsSettings
  storage.py        暗号化ストア (ttp_data/)
  crypto.py         PBKDF2 + Fernet
  teraterm.py       Tera Term マクロ起動
  aws.py            認証（任意の MFA）と boto3 セッション
  aws_inventory.py  EC2(SSM) / ECS Exec の接続可能一覧
  aws_browser.py    AWS 接続先ブラウザ
  terminal.py       Windows Terminal / PowerShell 起動
resources/connect.ttl  Tera Term マクロ
scripts/          開発・保守用スクリプト（アプリには含めない）
```

## コマンド

| 目的 | コマンド |
|---|---|
| 実行 | `uv run python -m ttp` |
| スモークチェック | `uv run python scripts/smoke_check.py` |
| ビルド | `uv run python build.py` |
| 依存更新 | `uv run python scripts/update_deps.py` |
| バージョン更新 | `uv run python scripts/bump_version.py patch` |

## 規約

- **秘密情報を絶対に出力しない**。`.env` は読むだけで、値をログ・例外・
  コマンドライン引数に出さない。
- AWS の認証情報は `ttp_data/aws.enc`（マスターパスワード由来の鍵で暗号化）。
  子プロセスへは環境変数でのみ渡す。
- ダイアログは `Toplevel` + `_setup_dialog` + `_center_window` + `wait_window`
  のパターンに合わせる。結果は `self.result` に入れる。
- ネットワーク処理は UI スレッドで行わない（`threading` + `after`）。
- boto3 は遅延 import（SSH のみのユーザーの起動時間を悪化させない）。
- 日本語 UI。コメントは日本語で簡潔に。
