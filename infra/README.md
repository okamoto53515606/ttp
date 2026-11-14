# LP (ttpilot.okamomedia.tokyo) のインフラ

S3 + CloudFront の静的サイト。サーバーサイドの処理は持たない。

```
ブラウザ
  │  HTTPS
  ▼
CloudFront（ap-northeast-1 のスタックから作成。CloudFront 自体はグローバル）
  ├─ Viewer request: CloudFront Functions で /foo/ → /foo/index.html
  ├─ Response headers: CSP / HSTS / COOP / CORP / COEP
  ▼  OAC（SigV4）
S3（ap-northeast-1 / 非公開 / SSE-S3 / バージョニング有効）
```

## 前提：ACM 証明書だけは us-east-1

CloudFront に紐づける ACM 証明書は **us-east-1 にしか置けません**。これは CloudFront の仕様で、
変更できません。S3 とスタック本体は東京（ap-northeast-1）で問題ありません。

```bash
# 1. 証明書の発行（us-east-1 で実行する）
aws acm request-certificate \
  --region us-east-1 \
  --domain-name ttpilot.okamomedia.tokyo \
  --validation-method DNS
```

表示された ARN を控え、DNS 検証用の CNAME を Route53 に追加して発行完了を待つ。

```bash
aws acm wait certificate-validated \
  --region us-east-1 \
  --certificate-arn <証明書ARN>
```

## 2. スタックの作成（東京）

```bash
aws cloudformation deploy \
  --region ap-northeast-1 \
  --stack-name ttpilot-lp \
  --template-file infra/lp-stack.yaml \
  --parameter-overrides \
      DomainName=ttpilot.okamomedia.tokyo \
      AcmCertificateArn=<証明書ARN> \
      EnableCustomDomain=true \
      CreateDnsRecord=false \
      HostedZoneId= \
  --capabilities CAPABILITY_IAM
```

> **証明書が ISSUED になる前に作りたい場合**
> `EnableCustomDomain=false` で先に作成し、発行後に
> `EnableCustomDomain=true` と `AcmCertificateArn` を指定して更新する。
> この場合、S3 と CloudFront を先に用意して動作確認を進められる。
> なお、CloudFront は **ISSUED でない証明書を受け付けない**ため、
> 最初から `true` で作るなら検証完了を待つ必要がある。

## 3. 出力の確認

```bash
aws cloudformation describe-stacks \
  --region ap-northeast-1 \
  --stack-name ttpilot-lp \
  --query 'Stacks[0].Outputs'
```

`BucketName` と `DistributionId` を GitHub のリポジトリ変数に設定する。

| 変数 | 値 |
|---|---|
| `AWS_DEPLOY_ROLE_ARN` | GitHub Actions が assume する IAM ロールの ARN |
| `LP_BUCKET` | スタック出力の `BucketName` |
| `CF_DISTRIBUTION_ID` | スタック出力の `DistributionId` |

## 3.5. DNS の設定

レコードの追加は手元で行う前提。次のどれかひとつ。

> **このプロジェクトの DNS は Route53 ではない。**
> `okamomedia.tokyo` のホストゾーンはこのアカウントに存在しないため、
> 実際に使うのは C の方法（外部 DNS に CNAME を追加）になる。
> A / B は Route53 に移行した場合の選択肢として残している。

### A. スタックに作らせる（Route53 が同じアカウントにある場合）

`CreateDnsRecord=true` と `HostedZoneId` を渡すと、A レコード（エイリアス）まで作成される。
追加作業は不要。

### B. Route53 に手動で追加

| 項目 | 値 |
|---|---|
| レコード名 | `ttpilot.okamomedia.tokyo` |
| レコードタイプ | A |
| エイリアス | はい |
| ルーティング先 | CloudFront ディストリビューション |
| ディストリビューション | スタック出力の `DistributionDomainName` に対応するもの |
| ホストゾーン ID | `Z2FDTNDATAQYW2`（CloudFront 用の固定値） |
| 評価ターゲットの正常性 | いいえ |

### C. 外部 DNS（お名前.com / Cloudflare / ムームードメインなど）

```
ttpilot    CNAME    dxxxxxxxxxxxxx.cloudfront.net.
```

`ttpilot` はサブドメインなので CNAME で問題ない（apex では CNAME を使えない）。
TTL は 300 秒程度でよい。

> **Cloudflare を使う場合**: プロキシ（オレンジ色の雲）は必ずオフ（DNS only / グレー雲）にする。
> オンにすると CloudFront の手前にもう1段 CDN が入り、証明書の検証・HSTS・
> クライアント IP の扱いが壊れる。

### 順序を守る

1. **ACM 証明書を発行して ISSUED にする**（us-east-1。DNS 検証用の CNAME も必要）
2. **スタックを作成する**（証明書 ARN を渡す）
3. **DNS レコードを追加する**（`CreateDnsRecord=true` なら 2 で完了）

DNS を先に向けると、証明書が無い状態で名前解決だけが通るため、ブラウザに
証明書エラーが出る。逆に証明書が ISSUED になる前にスタックを作ると失敗する。

### 確認

```bash
dig +short ttpilot.okamomedia.tokyo
curl -sI https://ttpilot.okamomedia.tokyo/ | head -20
```

応答に `content-security-policy` と `strict-transport-security` が含まれていれば、
CloudFront の Response Headers Policy が効いている。

## 4. デプロイ用 IAM ロール

GitHub Actions の OIDC 用ロールには、次の権限だけを与える。

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:ListBucket"],
      "Resource": "arn:aws:s3:::ttpilot-lp-<account-id>-ap-northeast-1"
    },
    {
      "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:DeleteObject", "s3:GetObject"],
      "Resource": "arn:aws:s3:::ttpilot-lp-<account-id>-ap-northeast-1/*"
    },
    {
      "Effect": "Allow",
      "Action": ["cloudfront:CreateInvalidation", "cloudfront:GetInvalidation"],
      "Resource": "arn:aws:cloudfront::<account-id>:distribution/<distribution-id>"
    }
  ]
}
```

信頼ポリシーの `sub` はリポジトリとブランチで絞る。

```json
{
  "Effect": "Allow",
  "Principal": { "Federated": "arn:aws:iam::<account-id>:oidc-provider/token.actions.githubusercontent.com" },
  "Action": "sts:AssumeRoleWithWebIdentity",
  "Condition": {
    "StringEquals": { "token.actions.githubusercontent.com:aud": "sts.amazonaws.com" },
    "StringLike": { "token.actions.githubusercontent.com:sub": "repo:okamoto53515606/ttp:ref:refs/heads/main" }
  }
}
```

## 5. デプロイ

`.github/workflows/lp-deploy.yml` が `lp/**` の変更で自動実行される。
手動で流す場合は Actions から `Deploy LP` を `Run workflow`。

デプロイ前に次の2つを検証している。

- `lp/index.html` の JSON-LD `softwareVersion` が `pyproject.toml` と一致するか
- `lp/**/*.html` にインラインスタイル（`style="` / `<style`）が無いか
  （CSP が `style-src 'self'` のため、あると適用されない）

## セキュリティヘッダー

CloudFront の Response Headers Policy で付与する。S3 単体では付けられない。

| ヘッダー | 値 |
|---|---|
| `Content-Security-Policy` | `default-src 'none'` を基本に、`style-src` / `img-src` / `font-src` のみ `'self'` |
| `Strict-Transport-Security` | `max-age=63072000; includeSubDomains` |
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY`（`frame-ancestors 'none'` と併用） |
| `Referrer-Policy` | `no-referrer` |
| `Permissions-Policy` | 利用しない機能をすべて無効化 |
| `Cross-Origin-Opener-Policy` | `same-origin` |
| `Cross-Origin-Resource-Policy` | `same-origin` |
| `Cross-Origin-Embedder-Policy` | `require-corp` |

JavaScript は一切実行しないため `script-src 'none'`。
JSON-LD（`<script type="application/ld+json">`）はデータブロックであり CSP の対象外なので、
この設定でも構造化データは有効（Chromium で CSP 違反 0 件を確認済み）。

## ローカルでの確認

`lp/` をそのままブラウザで開くと、ディレクトリ URL の解決と CSP ヘッダーが再現されない。
確認用に、CSP ヘッダーを付けて配信する簡易サーバーを使う。

```bash
python3 - <<'PY'
import http.server, socketserver, functools, os
CSP = ("default-src 'none'; script-src 'none'; style-src 'self'; img-src 'self'; "
       "font-src 'self'; connect-src 'none'; media-src 'none'; object-src 'none'; "
       "frame-src 'none'; worker-src 'none'; manifest-src 'none'; form-action 'none'; "
       "frame-ancestors 'none'; base-uri 'none'; upgrade-insecure-requests")
class H(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        super().end_headers()
H = functools.partial(H, directory="lp")
socketserver.TCPServer.allow_reuse_address = True
with socketserver.TCPServer(("127.0.0.1", 8765), H) as s:
    s.serve_forever()
PY
```

## 運用メモ

- HTML は CloudFront のキャッシュ TTL を 60 秒にしている。デプロイ時にワークフローが `/*` を無効化するため、公開後はすぐに最新が返る
- `/assets/*` は1年キャッシュ。`style.css` を変更したらファイル名を変えるか、パスを変える
- S3 はバージョニング有効。誤デプロイ時は旧バージョンから復元できる
- `sitemap.xml` の `lastmod` と各ページの `最終更新` は手で更新する
