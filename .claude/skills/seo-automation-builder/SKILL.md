---
name: seo-automation-builder
description: >-
  ブログ・SEOメディア向けの自動化ツール一式を、ユーザーのサイト用にゼロから構築するスキル。
  Search Console（サチコ）週次レポート（順位下落/上昇・急上昇KW・CTR改善候補・下落KWへのAI対策提示）、
  GA4週次レポート（急上昇/急降下ページ・PV TOP・CROアラート）、既存記事の自動SEO診断+AI改善案、
  競合サイトの新着記事検知、macOS launchdによる定期自動実行、Discord通知までを段階的にセットアップする。
  ユーザーが「SEOを自動化したい」「サチコ/サーチコンソールのレポートを自動化」「GA4の分析を通知したい」
  「記事を自動でチェックしたい」「リライト候補を自動で見つけたい」「競合の新着記事を監視したい」
  「順位下落を検知したい」などと言ったら、たとえ一部の機能だけの要望でも必ずこのスキルを使うこと。
  サイトURL1つあれば構築を始められる。
---

# SEO自動化システム構築スキル

ユーザー（多くは非エンジニアのブログ運営者・SEO担当者）のサイト用に、
**Macローカルで動く無料のSEO自動化ツール一式**を構築する。

## 全体像

構築するのは最大4つのツール。すべて **Python標準ライブラリのみ**（pip不要）で動き、
結果は **Discord Webhook** に通知される。

| ツール | 内容 | 実行 |
|---|---|---|
| `seo-report/` | サチコCSVを分析：順位下落/上昇・急上昇KW・CTR改善候補・下落KWへのAI対策 | 週1・手動（CSV取込） |
| `ga-report/` | GA4のCSVを分析：急上昇/急降下ページ・新規ページ・PV TOP・CROアラート | 週1・手動（CSV取込） |
| `article-check/` | 記事リストを毎回5件ずつ巡回してSEO診断＋AI改善案 | 毎日・自動（launchd） |
| `competitor-watch/` | 競合サイトのsitemap差分から新着記事を検知 | 週1・自動（launchd） |

サチコ/GA4はAPI権限が不要な「CSV手動エクスポート→取込フォルダに置く→実行」方式。
権限をもらえないクライアントサイトでも動くのがこの設計の狙い。
article-check と competitor-watch は権限不要でページを直接読むため完全自動化できる。

## 進め方の原則

- **1ステップずつ、動作確認してから次へ進む。** 一気に全部作ると、どこで壊れたか
  ユーザーが切り分けられない。最初にDiscord通知（土台）を通し、以降ツールを1つずつ足す。
- **テンプレートを使う。ゼロから書き直さない。** `assets/` のスクリプトは検証済みの完成品。
  プレースホルダー置換だけで動く。独自に書き直すとDiscordの403対策やsitemapの
  非標準形式対応などの蓄積が失われる。
- **非エンジニアを想定した説明をする。** 専門用語は短く補足し、操作は画面の順番で案内する。
  エラーが出たら「文言をそのまま貼ってください」と促す。
- **ユーザーの秘密情報（Webhook URL・APIキー）は、生成するスクリプト内にのみ書き込む。**
  会話で復唱しない。共有時の注意（Webhookはパスワードと同じ）を一度伝える。

## Step 1: ヒアリング

最初に以下を確認する。全部そろわなくても、そろった分から着手してよい
（最低限 Discord Webhook が1つあれば土台が作れる）。

1. **サイトURL**（例: `https://example.com/`）
2. **サイトの内容ひとこと**（例:「子育てメディア」）→ AIプロンプトの文脈に使う
3. **Discord Webhook URL**（1〜3個。通知を分けたい場合はメイン/記事/競合で別チャンネル推奨）
   - 作り方が分からなければ `references/operations-guide.md` の手順を案内する
4. **どのツールが必要か**（全部 or 一部。迷っていたら全部を推奨）
5. **競合サイトURL**（competitor-watch を作る場合。2〜5サイト）
6. **Gemini APIキー**（任意。AI改善案・AI対策を使う場合。無料で取得可能。
   無ければ空のままでも動く＝ルールベース診断のみになる）
7. **記事リスト**（article-check 用。無ければ後述の方法でサイトマップから自動生成できる）

## Step 2: 土台（Discordテスト）

1. プロジェクトフォルダを作る（場所はユーザーに確認。デフォルト提案: `~/Desktop/<サイト名>-seo/`）
2. まずDiscordに1通テスト送信して土台を確認する:

```python
import urllib.request, json
url = "ユーザーのWebhook URL"
data = json.dumps({"content": "✅ SEO自動化テスト通知です"}).encode()
req = urllib.request.Request(url, data=data,
    headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
print(urllib.request.urlopen(req, timeout=30).status)  # 204なら成功
```

**重要:** `User-Agent` ヘッダーが無いとDiscordは **403** を返す。テンプレートには
対策済みだが、独自コードを書くときも必ず付けること。

## Step 3: ツール構築（1つずつ）

各ツールについて: `assets/` からテンプレートをコピー → プレースホルダー置換 →
サンプルデータで動作テスト → テストデータをリセット → ユーザーに使い方を説明、の順で進める。

### プレースホルダー一覧

| トークン | 内容 | 対象ファイル |
|---|---|---|
| `{{DISCORD_WEBHOOK}}` | 通知先Webhook URL | 全スクリプト（ツールごとに別URLでも可） |
| `{{SITE_DESCRIPTION}}` | サイトの説明（例: 子育てメディア） | seo_report.py |
| `{{GEMINI_API_KEY}}` | GeminiのAPIキー（無ければ空文字 `""`） | seo_report.py / article_check.py |
| `{{SCRIPT_NAME}}` | 実行するpyファイル名 | run_report.command |
| `{{PYTHON3_PATH}}` | `which python3` の結果 | plist 2種 |
| `{{PROJECT_DIR}}` | ツールフォルダの絶対パス | plist 2種 |
| `{{LABEL}}` | launchdラベル（例: com.myblog.article-check） | plist 2種 |

競合URLはスクリプトに直書きせず、`competitor-watch/competitors.txt`（1行1URL）に書く。
`competitor_watch.py` 自体にはプレースホルダーが無い（そのままコピーでよい）。

### 各ツールの構築とテスト

**seo-report（サチコ週次）** — `assets/seo_report.py`
- フォルダ内に `取込/` `済み/` を作成。`assets/run_report.command` から起動用コマンドも生成し `chmod +x`
- テスト: サチコ形式のサンプルCSV（列: クエリ/クリック数/表示回数/CTR/掲載順位）を
  `取込/` に置いて実行。初回は「※初回取込」となる。前週比較（下落/上昇/急上昇KW/AI対策）を
  検証するなら、1回実行後に `履歴.csv` の日付を過去日に書き換えてから、別の値のCSVで2回目を実行する。
  日付の書き換え例（履歴の日付を全部 2025-01-01 にする）:
  `python3 -c "import re,io; p='履歴.csv'; s=open(p,encoding='utf-8-sig').read(); open(p,'w',encoding='utf-8-sig').write(re.sub(r'\d{4}-\d{2}-\d{2}','2025-01-01',s))"`
- **テスト後は `履歴.csv`・`済み/`・`取込/` を必ず削除**して本番はまっさらな状態で渡す

**ga-report（GA4週次）** — `assets/ga_report.py`
- GA4のCSVは先頭に `#` 注釈行が入る特殊形式。テンプレートは対応済み
- CROアラートは「平均エンゲージメント時間」「キーイベント」列があるときだけ判定される
- テスト方法・リセットは seo-report と同じ

**article-check（記事巡回診断）** — `assets/article_check.py`
- `記事リスト.csv`（1列目URL、2列目対策KW）が必要。ユーザーが持っていなければ:
  - **サイトマップから自動生成**: ユーザーのサイトの `sitemap.xml` を取得して記事URLを抽出し、
    CSVの雛形を作って渡す（KW列はユーザーが後で埋める。空でも動く）
  - Numbers/Excelのリストがあれば変換して取り込む（列の向き・相対パスに注意）
- テスト: ユーザーの実サイトのURLを1件リストに入れて実行し、診断結果とAI改善案
  （キーがあれば）がDiscordに届くのを確認。その後 `check_state.json` を削除してリセット

**competitor-watch（競合新着検知）** — `assets/competitor_watch.py`
- 競合URLは `competitors.txt` に1行1URLで書く（`assets/competitors.txt` をコピーして使う）。
  スクリプトは直接編集させない＝非エンジニアがコードに触れずに済む
- `assets/check_competitors.command` もコピーして `chmod +x`（疎通チェック用）
- **URLを確定する前に必ず疎通チェックする**: `python3 competitor_watch.py check` を実行し、
  各サイトが「✅ ... : N URL」になるか確認する。**0件（❌）のサイトは監視できない**ので別サイトに差し替える。
  実際、一般的な暮らし系・ニュース系メディアでも標準の `sitemap.xml` を提供していないサイトは
  珍しくない（過去テストで候補の半数以上が0件だった）。この確認を飛ばすと「毎週新着0件」の
  ハリボテになるため、ここは省略しない
- 初回の本実行はベースライン登録のみ（全URL記録・新着通知なし）。これは正常だと必ず説明する
- 差分検知のテスト: `seen_urls.json` から数件URLを削って再実行→「🆕新着」と出れば成功
- sitemapの非標準形式（sitemapindexに実ページURLが直接入っている等）・gzip・robotsの表記ゆれには
  対応済み。それでも0件なら robots.txt と sitemap構造を実際に見て切り分ける（RSSや
  記事一覧ページの差分方式への切り替えも検討）

## Step 4: 自動実行（launchd）

article-check（毎日）と competitor-watch（週1）を `assets/launchd-daily.plist` /
`assets/launchd-weekly.plist` から生成して登録する。実行時刻はユーザーに確認
（デフォルト提案: 毎日8:00 / 毎週月曜9:00）。

```bash
cp <plist> ~/Library/LaunchAgents/<label>.plist
launchctl bootout gui/$(id -u)/<label> 2>/dev/null || true
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/<label>.plist
launchctl list | grep <label>            # 登録確認
launchctl kickstart -k gui/$(id -u)/<label>   # 手動キックで動作テスト
```

キック後は各フォルダの `自動実行ログ.txt` を確認し、Discordに届いたことまで見届ける。
注意: Macがスリープ中は実行されない（次の起動時にはまとめて実行されない点も伝える）。

## Step 5: 課金アラートの説明（必ず伝える）

このシステムで費用が発生しうるのは Gemini API のみ。テンプレートには
**無料枠超過（429/RESOURCE_EXHAUSTED）を検知したら🚨課金アラートをDiscordに送る**
仕組みが組み込み済み。「課金設定をしない限り勝手に請求されることはない（超過時は
止まるだけ）」という事実とセットで必ず説明する。

## Step 6: 運用マニュアルの生成

最後に、そのユーザーの構成に合わせた `運用マニュアル.md` をプロジェクト直下に生成する。
内容: 週次でやること（CSVエクスポート手順つき）、自動で動いているもの、通知の読み方、
しきい値の変え方、トラブル時の対処。汎用手順は `references/operations-guide.md` を
下敷きに、ユーザーのフォルダ構成・チャンネル構成に合わせて書き換えること。

## ハマりどころ（過去に実際に起きた問題）

- **Discord 403** → User-Agentヘッダー必須（対策済み。独自コード時は注意）
- **Discord 2000字制限** → テンプレートは1900字で分割送信済み
- **GA4 CSVの `#` 注釈行・表末尾の空行** → パーサー対応済み
- **サチコCSVの列名・CTRの表記ゆれ（`2.5%` / `0.025`）** → 変換関数対応済み
- **sitemapindexの非標準構造 / robots.txtの `Sitemap :`（空白入り）/ .gz圧縮** → 対応済み
- **多くのサイトは標準の sitemap.xml を提供していない** → 競合登録前に `competitor_watch.py check`
  で0件でないか必ず確認。0件サイトを入れると「毎週新着0件」の空監視になる
- **`.command` 初回実行時のGatekeeper警告** → 右クリック→「開く」で一度許可、と案内
- **Numbersファイルの記事リスト** → `osascript` でNumbersにCSVエクスポートさせて変換できる
- **Gemini 503** → 一過性。テンプレートは3回リトライ済み。429は課金アラートへ
- **launchd登録後に動かない** → `launchctl list | grep <label>` で登録確認、
  `自動実行ログ.txt` でエラー確認、python3のパスがplistと一致しているか確認

## 参照ファイル

- `assets/` … 検証済みスクリプトテンプレート一式（必ずこれを使う）
- `references/operations-guide.md` … Webhook作成・CSVエクスポート・Geminiキー取得の
  画面手順、トラブルシューティング詳細。ユーザーへの操作案内やマニュアル生成時に読む
