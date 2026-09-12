#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
競合サイト 新着記事チェック（Macローカル版・週次）

各競合のsitemap.xmlを取得して全URLを記録し、前回との差分＝新しく公開された記事を
検知してDiscordに通知する。権限・課金不要。標準ライブラリのみ。

初回実行: 全URLをベースライン登録（通知なし）
2回目以降: 前回に無かった新着URLだけ通知
"""

import os
import re
import sys
import gzip
import html
import json
import urllib.request
import urllib.error
from datetime import datetime

# ========================= 設定 =========================
DISCORD_WEBHOOK = "{{DISCORD_WEBHOOK}}"

# 競合URLは competitors.txt（1行1URL）から読み込む。
# 下のリストは補助的なフォールバック（通常は空のままでOK）。
COMPETITORS = []

MAX_REPORT     = 15   # 1サイトあたり通知する新着URLの最大数
MAX_SITEMAPS   = 30   # 1サイトで辿るサブサイトマップの上限（暴走防止）
UA = "Mozilla/5.0 (compatible; competitor-watch/1.0)"

BASE  = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(BASE, "seen_urls.json")
LIST_FILE = os.path.join(BASE, "competitors.txt")


def load_competitors():
    """competitors.txt（1行1URL・#始まりはコメント）＋スクリプト内COMPETITORSを統合"""
    urls = list(COMPETITORS)
    if os.path.exists(LIST_FILE):
        with open(LIST_FILE, encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if s and not s.startswith("#"):
                    urls.append(s)
    seen, out = set(), []      # 重複除去・順序維持
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


# ========================= 取得系 =========================
def fetch_bytes(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    if url.endswith(".gz") or raw[:2] == b"\x1f\x8b":  # gzip対応
        raw = gzip.decompress(raw)
    return raw


def fetch_text(url):
    return fetch_bytes(url).decode("utf-8", "ignore")


def discover_sitemaps(base):
    """robots.txt → 一般的なパスの順にsitemapを探す"""
    found = []
    try:
        robots = fetch_text(base.rstrip("/") + "/robots.txt")
        for line in robots.splitlines():
            m = re.match(r"(?i)\s*sitemap\s*:\s*(\S+)", line)  # 'Sitemap :' のスペースにも対応
            if m:
                found.append(m.group(1).strip())
    except Exception:
        pass
    if not found:
        for p in ("/sitemap.xml", "/sitemap_index.xml", "/wp-sitemap.xml", "/sitemap-index.xml"):
            u = base.rstrip("/") + p
            try:
                t = fetch_text(u)
                if "<loc" in t.lower() or "<urlset" in t.lower() or "<sitemapindex" in t.lower():
                    found.append(u)
                    break
            except Exception:
                pass
    return found


def collect_urls(base):
    """サイトの全ページURL（loc→lastmod）を集める。sitemapindexは再帰的に辿る"""
    result = {}
    seen_sm = set()
    queue = discover_sitemaps(base)
    processed = 0
    while queue and processed < MAX_SITEMAPS:
        sm = queue.pop(0)
        if sm in seen_sm:
            continue
        seen_sm.add(sm)
        processed += 1
        try:
            text = fetch_text(sm)
        except Exception:
            continue

        if "<sitemapindex" in text.lower():
            for s in re.findall(r"<loc>\s*(.*?)\s*</loc>", text, re.S | re.I):
                child = html.unescape(s.strip())
                cl = child.lower()
                # 子が本当にsitemap（.xml等）なら再帰、そうでなければ実ページURLとして記録
                if cl.endswith(".xml") or cl.endswith(".xml.gz") or "sitemap" in cl:
                    if child not in seen_sm:
                        queue.append(child)
                else:
                    result.setdefault(child, "")
            continue

        blocks = re.findall(r"<url>(.*?)</url>", text, re.S | re.I)
        if blocks:
            for b in blocks:
                mloc = re.search(r"<loc>\s*(.*?)\s*</loc>", b, re.S | re.I)
                if not mloc:
                    continue
                loc = html.unescape(mloc.group(1).strip())
                mlm = re.search(r"<lastmod>\s*(.*?)\s*</lastmod>", b, re.S | re.I)
                result[loc] = mlm.group(1).strip() if mlm else ""
        else:  # <url>ブロックが無い形式はlocだけ拾う
            for s in re.findall(r"<loc>\s*(.*?)\s*</loc>", text, re.S | re.I):
                result.setdefault(html.unescape(s.strip()), "")
    return result


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


def load_state():
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE, encoding="utf-8"))
        except Exception:
            return {}
    return None  # None＝初回


# ========================= 疎通チェック（登録前の確認用）=========================
def check_mode():
    """competitor_watch.py check で実行。各競合のsitemapが取得できるかだけ確認する。
    stateもDiscordも触らないので、URLを確定する前の下見に使える。"""
    comps = load_competitors()
    if not comps:
        print("competitors.txt が空です。URLを1行ずつ書いてから確認してください。")
        return
    print("=== 競合sitemap疎通チェック ===")
    for site in comps:
        try:
            n = len(collect_urls(site))
            print(f"{'✅' if n > 0 else '❌'} {site} : {n} URL")
        except Exception as e:
            print(f"❌ {site} : エラー {e}")
    print("\n❌や0件のサイトは sitemap が標準の場所に無い/未提供です。別サイトへ差し替えてください。")


# ========================= メイン =========================
def main():
    comps = load_competitors()
    if not comps:
        send_discord("⚠️ competitors.txt に競合URLがありません。トップページURLを1行ずつ書いてください。")
        print("competitors.txt が空です。")
        return

    state = load_state()
    first_run = state is None
    if state is None:
        state = {}
    today = datetime.now().strftime("%Y-%m-%d")

    header = f"🕵️ **競合 新着記事チェック（週次 {today}）**"
    blocks = []
    any_new = False

    for site in comps:
        try:
            urls = collect_urls(site)
        except Exception as e:
            blocks.append(f"\n❌ {site}\n取得失敗: {e}")
            continue

        if not urls:
            blocks.append(f"\n⚠️ {site}\nsitemapが見つからず取得0件")
            continue

        seen = set(state.get(site, []))
        current = set(urls.keys())
        state[site] = sorted(seen | current)  # 既知URLは蓄積（再通知しない）

        if first_run:
            blocks.append(f"\n🔹 {site}\n初回登録: {len(current)}URL（次回から新着を通知）")
            continue

        new = current - seen
        if new:
            any_new = True
            newl = sorted(new, key=lambda u: urls.get(u, ""), reverse=True)  # lastmod新しい順
            lines = "\n".join(
                f"・{u}" + (f"（{urls[u][:10]}）" if urls.get(u) else "")
                for u in newl[:MAX_REPORT])
            more = f"\n…ほか{len(new) - MAX_REPORT}件" if len(new) > MAX_REPORT else ""
            blocks.append(f"\n🆕 **{site}：新着{len(new)}件**\n{lines}{more}")
        else:
            blocks.append(f"\n✅ {site}：新着なし")

    json.dump(state, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    if first_run:
        header += "\n（初回のためベースライン登録のみ。来週から新着記事を通知します）"
    elif not any_new:
        header += "\n今週の新着はありませんでした。"

    msg = header + "".join(blocks)
    print(msg)
    send_discord(msg)
    print("\n✅ 完了しました。")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "check":
        check_mode()      # 登録前の疎通チェック（Discordには送らない）
    else:
        main()
