#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
既存記事 自動チェック（Macローカル版）

・記事リスト.csv に記事URL（と対象キーワード）を書いておく
・実行するたびに「チェックが古い順」に5件ずつ自動診断する
・各記事のSEO改善点（＋AI改善案）を Discord に通知する

権限・課金・ファイル渡し不要。ページを直接読んで診断します。
標準ライブラリのみで動作（AI機能を使う場合のみGemini APIキーが必要）。
"""

import os
import re
import csv
import json
import time
import urllib.request
import urllib.error
from datetime import datetime
from html.parser import HTMLParser

# ========================= 設定 =========================
DISCORD_WEBHOOK = "{{DISCORD_WEBHOOK}}"

DAILY_COUNT = 5     # 1回でチェックする記事数

# 診断のしきい値（日本語の文字数基準）
TITLE_MIN, TITLE_MAX = 20, 35     # タイトルの適正文字数
DESC_MIN,  DESC_MAX  = 70, 140    # メタディスクリプションの適正文字数
CONTENT_MIN          = 1500       # これ未満は「内容が薄い」

# AIによる改善案（任意）。使うなら Google AI Studio の無料APIキーを入れる。
# 空のままなら「ルールベース診断」だけで動く（完全無料・キー不要）。
GEMINI_API_KEY = "{{GEMINI_API_KEY}}"
GEMINI_MODEL   = "gemini-2.5-flash"

BASE  = os.path.dirname(os.path.abspath(__file__))
LIST  = os.path.join(BASE, "記事リスト.csv")
STATE = os.path.join(BASE, "check_state.json")

# AIの無料枠超過（＝これ以上は課金が必要）を検知したらTrueになる。課金アラート用。
QUOTA_HIT = False


# ========================= HTML解析 =========================
class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title = ""
        self._in_title = False
        self.meta_desc = ""
        self.headings = []      # (level, text)
        self._cur_h = None
        self._cur_h_text = []
        self.img_total = 0
        self.img_no_alt = 0
        self.links = 0
        self._skip = 0          # script/style の中
        self.text_parts = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "title":
            self._in_title = True
        elif tag in ("script", "style", "noscript"):
            self._skip += 1
        elif tag == "meta":
            if a.get("name", "").lower() == "description" and a.get("content"):
                self.meta_desc = a.get("content", "")
        elif tag in ("h1", "h2", "h3"):
            self._cur_h = int(tag[1])
            self._cur_h_text = []
        elif tag == "img":
            self.img_total += 1
            if not (a.get("alt") or "").strip():
                self.img_no_alt += 1
        elif tag == "a" and a.get("href"):
            self.links += 1

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag in ("script", "style", "noscript"):
            self._skip = max(0, self._skip - 1)
        elif tag in ("h1", "h2", "h3") and self._cur_h:
            self.headings.append((self._cur_h, "".join(self._cur_h_text).strip()))
            self._cur_h = None

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._cur_h is not None:
            self._cur_h_text.append(data)
        if self._skip == 0 and data.strip():
            self.text_parts.append(data.strip())


def jp_len(s):
    """空白を除いた文字数（日本語の分量目安）"""
    return len(re.sub(r"\s+", "", s or ""))


def fetch_html(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (SEO-check-bot)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        ctype = r.headers.get("Content-Type", "")
    m = re.search(r"charset=([\w\-]+)", ctype, re.I)
    enc = m.group(1) if m else None
    if not enc:
        m2 = re.search(br'charset=["\']?([\w\-]+)', raw[:2000], re.I)
        enc = m2.group(1).decode("ascii", "ignore") if m2 else "utf-8"
    return raw.decode(enc, errors="ignore")


# ========================= 診断 =========================
def audit(url, keyword, html):
    p = PageParser()
    p.feed(html)

    title = p.title.strip()
    desc  = p.meta_desc.strip()
    h1s   = [t for lv, t in p.headings if lv == 1]
    h2s   = [t for lv, t in p.headings if lv == 2]
    body  = " ".join(p.text_parts)
    clen  = jp_len(body)

    issues = []

    # タイトル
    if not title:
        issues.append("タイトルタグが無い")
    else:
        tl = jp_len(title)
        if tl > TITLE_MAX:
            issues.append(f"タイトルが長い（{tl}字→検索結果で見切れる。{TITLE_MAX}字以内推奨）")
        elif tl < TITLE_MIN:
            issues.append(f"タイトルが短い（{tl}字→キーワードを盛り込む余地あり）")

    # メタディスクリプション
    if not desc:
        issues.append("メタディスクリプションが未設定（CTR改善のため要記述）")
    else:
        dl = jp_len(desc)
        if dl < DESC_MIN:
            issues.append(f"メタディスクリプションが短い（{dl}字→{DESC_MIN}〜{DESC_MAX}字推奨）")
        elif dl > DESC_MAX:
            issues.append(f"メタディスクリプションが長い（{dl}字→{DESC_MAX}字以内で見切れ防止）")

    # 見出し構造
    if len(h1s) == 0:
        issues.append("H1見出しが無い")
    elif len(h1s) > 1:
        issues.append(f"H1が{len(h1s)}個ある（1ページ1個が基本）")
    if len(h2s) == 0:
        issues.append("H2見出しが無い（記事の構造化・目次のために必要）")

    # 内容量
    if clen < CONTENT_MIN:
        issues.append(f"内容が薄い（本文約{clen}字→{CONTENT_MIN}字以上に加筆推奨）")

    # 画像alt
    if p.img_no_alt > 0:
        issues.append(f"alt未設定の画像が{p.img_no_alt}枚（画像SEO・アクセシビリティ低下）")

    # キーワード最適化（複数語KWは語単位で判定）
    if keyword:
        tokens = [t for t in re.split(r"[\s　/]+", keyword.strip()) if t]
        main = tokens[0] if tokens else keyword.strip()
        if title and main not in title:
            issues.append(f"タイトルに主要KW『{main}』が無い")
        if h1s and all(main not in h for h in h1s):
            issues.append(f"H1に主要KW『{main}』が無い")
        missing = [t for t in tokens if t not in body]
        if missing:
            issues.append("本文に不足しているKW語: " + " / ".join(missing))

    return {
        "url": url, "keyword": keyword, "title": title or "(タイトル無し)",
        "chars": clen, "h2count": len(h2s), "issues": issues,
        "body": body,
    }


def ai_suggest(res):
    """Gemini無料枠で内容面の改善案を生成（APIキー未設定ならスキップ）"""
    global QUOTA_HIT
    if not GEMINI_API_KEY:
        return None
    prompt = (
        "あなたはSEOの専門家です。以下の記事について、検索順位を上げるための"
        "具体的な改善案を3つ、箇条書きで簡潔に提案してください。"
        f"\n\n対象キーワード: {res['keyword'] or '（未指定）'}"
        f"\nタイトル: {res['title']}"
        f"\n本文（抜粋）: {res['body'][:6000]}"
    )
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}")
    data = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode("utf-8")
    last = ""
    for attempt in range(3):  # 503など一過性エラーはリトライ
        req = urllib.request.Request(url, data=data,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                out = json.loads(r.read())
            return out["candidates"][0]["content"]["parts"][0]["text"].strip()
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "ignore")[:300]
            except Exception:
                pass
            last = f"HTTP {e.code} {body}"
            # 429 / RESOURCE_EXHAUSTED = 無料枠超過。リトライせず課金アラートを立てる
            if e.code == 429 or "RESOURCE_EXHAUSTED" in body:
                QUOTA_HIT = True
                return "(無料枠の上限に達したためAI改善案をスキップしました)"
            time.sleep(3 * (attempt + 1))
        except Exception as e:
            last = str(e)
            time.sleep(3 * (attempt + 1))
    return f"(AI改善案の取得に失敗: {last})"


# ========================= 通知 =========================
def send_discord(msg):
    for i in range(0, len(msg), 1900):
        data = json.dumps({"content": msg[i:i + 1900]}).encode("utf-8")
        req = urllib.request.Request(
            DISCORD_WEBHOOK, data=data,
            headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
        try:
            urllib.request.urlopen(req, timeout=30)
        except Exception as e:
            print("Discord送信エラー:", e)


# ========================= 対象記事の選定 =========================
def load_list():
    if not os.path.exists(LIST):
        return None
    items = []
    with open(LIST, newline="", encoding="utf-8-sig") as f:
        for row in csv.reader(f):
            if not row or not row[0].strip():
                continue
            url = row[0].strip()
            if not url.lower().startswith("http"):
                continue  # ヘッダー行など
            kw = row[1].strip() if len(row) > 1 else ""
            items.append((url, kw))
    return items


def load_state():
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE, encoding="utf-8"))
        except Exception:
            return {}
    return {}


# ========================= メイン =========================
def main():
    items = load_list()
    if items is None:
        send_discord("⚠️ 『記事リスト.csv』がありません。1列目にURL、2列目に対策KWを入れてください。")
        print("記事リスト.csv なし")
        return
    if not items:
        send_discord("⚠️ 『記事リスト.csv』に有効なURLがありません。")
        print("URLなし")
        return

    state = load_state()
    today = datetime.now().strftime("%Y-%m-%d")

    # チェックが古い順（未チェックは最優先）に並べて先頭からDAILY_COUNT件
    items.sort(key=lambda it: state.get(it[0], "0000-00-00"))
    targets = items[:DAILY_COUNT]

    blocks = [f"📝 **記事チェック（{today}） 全{len(items)}件中 {len(targets)}件を診断**"]
    for url, kw in targets:
        try:
            html = fetch_html(url)
            res = audit(url, kw, html)
        except Exception as e:
            blocks.append(f"\n❌ {url}\n取得失敗: {e}")
            state[url] = today
            continue

        head = f"\n──────────\n**{res['title']}**\n<{url}>"
        head += f"\n本文{res['chars']}字 / H2見出し{res['h2count']}個" + (f" / KW: {kw}" if kw else "")
        if res["issues"]:
            head += "\n🔧 改善点:\n" + "\n".join(f"・{x}" for x in res["issues"])
        else:
            head += "\n✅ 基本SEOは良好"

        ai = ai_suggest(res)
        if ai:
            head += "\n🤖 AI改善案:\n" + ai
        blocks.append(head)
        state[url] = today

    json.dump(state, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    msg = "\n".join(blocks)
    print(msg)
    send_discord(msg)

    # --- 課金アラート：AIの無料枠を超えたら必ず通知 ---
    if QUOTA_HIT:
        alert = (
            "🚨 **【課金アラート】Gemini APIの無料枠上限に達しました**\n"
            "本日のAI改善案は一部スキップされました。\n"
            "このままAI改善案を使い続けるには、Google Cloud での**課金設定（従量課金）が必要**になる可能性があります。\n"
            "※課金設定をしない限り自動で請求されることはありません（無料枠を超えると停止するだけです）。\n"
            "→ 対応：AI診断の頻度を下げる／数日待つ／課金を検討、のいずれか。"
        )
        print(alert)
        send_discord(alert)

    print("\n✅ 完了しました。")


if __name__ == "__main__":
    main()
