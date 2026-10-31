# TTP - Tera Term Pilot

テラタームマクロをGUIで管理・実行するツールです。  
接続情報は暗号化して保存されます。

## 特徴

- **ワンクリック接続**: 接続先を選んでクリックするだけ
- **AWS から接続 (v1.2.0)**: SSM / ECS Exec で「今つながる」EC2・Fargate を一覧から選択
- **接続情報の暗号化**: パスワード・秘密鍵・AWS アクセスキーはマスターパスワードで暗号化
- **自動ログ記録**: 接続時にログファイルが自動生成される（忘れない）
- **鍵ファイル管理**: 鍵はアプリ内に暗号化保存。接続設定を消せば鍵も消える
- **ttpmacro.exe 自動検出**: インストール済みのTera Termを自動検出
- **ポータブル**: zip解凍するだけ。アンインストールはフォルダ削除するだけ

## ドキュメント

- **[使い方マニュアル](docs/manual.md)** — インストール・セットアップ・各画面の操作方法

## 動作要件

- Windows 11
- Tera Term (ttpmacro.exe) がインストールされていること
- AWS から接続する場合のみ: Session Manager プラグイン（AWS CLI は不要）

## インストール

1. `ttp-x.x.x.zip` を任意の場所に解凍
2. `ttp.exe` をダブルクリック
3. 初回起動時にマスターパスワードを設定
4. ttpmacro.exe のパスを確認（自動検出されます）

### 初回起動時の警告について

コード署名を付けていないため、初回起動時に Microsoft Defender SmartScreen が
「Windows によって PC が保護されました」と表示されることがあります。

![SmartScreen の警告](docs/screenshot/windows_alert.png)

1. **「詳細情報」** をクリック
2. 表示された **「実行」** をクリック

で起動できます。2 回目以降は表示されません。

> ソースから自分でビルドして確かめることもできます（`uv sync` → `uv run python build.py`）。

## 使い方

1. 「新規追加」で接続先を登録
2. 接続先を選んで「接続」をクリック（またはダブルクリック）
3. ログは自動で `logs/` フォルダに保存される

## キーボードショートカット

| キー | 操作 |
|------|------|
| Enter / ダブルクリック | 接続 |
| Ctrl+N | 新規追加 |
| Ctrl+E | 編集 |
| Ctrl+D | 複製 |
| Delete | 削除 |
| Ctrl+L | ログフォルダを開く |
| Ctrl+, | 設定 |
| Ctrl+Shift+A | AWS から接続 |

## AWS から接続 (SSM / ECS Exec)

SSH を立てずに、Session Manager / ECS Exec 経由でシェルに入ります。
SSH の鍵もインバウンドポートも不要です。

### 必要なもの

1. [Session Manager プラグイン](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html)（**必須・これだけでOK**）
2. EC2 側: SSM Agent と `AmazonSSMManagedInstanceCore` 相当のインスタンスプロファイル
3. ECS 側: `enableExecuteCommand` を有効にしたタスク

AWS CLI v2 は不要です（インストール済みならフォールバックとして使われます）。
プラグインは TTP に同梱されていません。未導入の場合は「AWS から接続」を
開いたときにインストール案内のポップアップが表示されます。
インストール後は **TTP を再起動**してください（PATH の再読み込みが必要）。

### 使い方

1. メニューの「AWS」→「AWS 設定...」でアクセスキー・シークレット・リージョンを入力
2. 「接続テスト」で疎通確認
3. メニューの「AWS」→「AWS から接続...」（Ctrl+Shift+A）
4. 一覧から EC2 インスタンスまたは ECS タスクを選んでダブルクリック

一覧には **接続できるものだけ** を表示します。

- EC2: SSM に登録され `PingStatus=Online` のもの
- ECS: ECS Exec が有効で `ExecuteCommandAgent` が RUNNING のもの

### MFA について

IAM ユーザーに MFA が必須のポリシーを敷いている場合だけ、「AWS 設定」で
「MFA を使用する」を有効にしてください。接続時に 6 桁コードを入力します。
MFA を使っていないユーザーは何も入力不要です。

### 必要な IAM 権限（最小構成）

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ec2:DescribeInstances",
        "ecs:ListClusters",
        "ecs:ListTasks",
        "ecs:DescribeTasks",
        "ecs:ExecuteCommand",
        "ssm:DescribeInstanceInformation",
        "ssm:StartSession"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "ssmmessages:CreateControlChannel",
        "ssmmessages:CreateDataChannel",
        "ssmmessages:OpenControlChannel",
        "ssmmessages:OpenDataChannel"
      ],
      "Resource": "*"
    }
  ]
}
```

## 開発 (ソースから実行)

```bash
# 依存パッケージをインストール
uv sync

# 実行
uv run python -m ttp
```

## ビルド (exe作成)

```bash
uv run python build.py
```

`dist/ttp/` フォルダに `ttp.exe` が生成されます。  
フォルダごとzip圧縮して配布してください。

## アンインストール

フォルダを削除するだけです。  
接続設定・暗号化された鍵・ログもすべてフォルダ内に保存されているため、フォルダ削除で完全にクリーンアップされます。

## ライセンス

MIT License - 詳細は [LICENSE](LICENSE) をご確認ください。
