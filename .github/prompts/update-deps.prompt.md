---
mode: agent
description: ttp の依存パッケージを月次更新し、リリース準備まで行う
---

# 依存パッケージの月次更新

次の手順で進めてください。

1. `uv run python scripts/update_deps.py` を実行する
2. 出力から更新されたパッケージを読み取り、**メジャーバージョン更新があれば必ず明示**する
3. `uv run python scripts/smoke_check.py` が成功していることを確認する
4. 失敗した場合は `uv.lock` を元に戻し（`git checkout -- uv.lock pyproject.toml`）、原因を報告する
5. 成功したら、変更内容を 5 行以内の箇条書きで要約する
6. コミットメッセージ案を提示する（例: `chore: update dependencies (2026-11)`）

リリースまで進める場合は追加で:

7. `uv run python scripts/bump_version.py patch` を実行する
8. `uv run python build.py` で exe と zip をビルドする
9. 生成された `dist/ttp-<version>.zip` のサイズを報告する

## 禁止事項

- `.env` や認証情報の中身を表示しない
- スモークチェックを通していない変更をリリースしない
- `uv.lock` を手で編集しない（必ず `uv` コマンド経由）
