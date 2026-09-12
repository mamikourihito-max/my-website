#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Search Console（サチコ）週次レポート（Macローカル版）

使い方:
  1. サチコからダウンロードした「クエリ」CSVを『取込』フォルダに入れる
  2. このスクリプト（またはレポート実行.command）を実行する
  3. 自動で分析して Discord に通知される

Googleアカウント連携・課金は一切不要。標準ライブラリのみで動作します。
"""

import os
import csv
import glob
import json
import time
import shutil
import urllib.request
import urllib.error
from datetime import datetime

# ========================= 設定 =========================
DISCORD_WEBHOOK = "{{DISCORD_WEBHOOK}}"

RANK_MOVE = 3.0    # 順位がこれ以上動いたら「変動」とみなす
IMP_MIN   = 100    # CTR改善候補: 表示回数がこれ以上
RANK_MAX  = 20.0   # CTR改善候補: 掲載順位がこれ以下（1〜2ページ目）
CTR_LOW   = 2.0    # CTR改善候補: CTRがこれ%未満

# --- 急上昇KW検知 ---
SURGE_RATIO    = 1.0   # 表示回数が前週比+100%（2倍）以上で急上昇とみなす
SURGE_MIN_IMPR = 50    # 現在の表示回数がこれ以上（ノイズ除去）

# --- 下落KWへのAI対策提示（空にすればAIなしで従来通り動く）---
GEMINI_API_KEY = "{{GEMINI_API_KEY}}"
GEMINI_MODEL   = "gemini-2.5-flash"
AI_DROP_LIMIT  = 5     # AI対策を出す下落KWの最大数（無料枠・コスト対策）
SITE_CONTEXT   = "{{SITE_DESCRIPTION}}"
QUOTA_HIT = False      # AI無料枠超過（＝要課金）を検知したらTrueに

BASE  = os.path.dirname(os.path.abspath(__file__))
INBOX = os.path.join(BASE, "取込")
DONE  = os.path.join(BASE, "済み")
HIST  = os.path.join(BASE, "履歴.csv")


# ========================= 補助関数 =========================
def to_num(v):
    try:
        return float(str(v).replace(",", "").strip())
    except Exception:
        return 0.0


def to_ctr(v):
    s = str(v).strip()
    if s.endswith("%"):
        return to_num(s[:-1])
    n = to_num(s)
    return n * 100 if 0 < n <= 1 else n  # 0.035 → 3.5


def is_number(v):
    try:
        float(str(v).replace(",", "").replace("%", "").strip())
        return True
    except Exception:
        return False


def send_discord(msg):
    ok = True
    for i in range(0, len(msg), 1900):  # Discordは1通2000字まで
        chunk = msg[i:i + 1900]
        data = json.dumps({"content": chunk}).encode("utf-8")
        req = urllib.request.Request(
            DISCORD_WEBHOOK, data=data,
            headers={"Content-Type": "application/json",
                     "User-Agent": "Mozilla/5.0"})  # これが無いとDiscordに403される
        try:
            urllib.request.urlopen(req, timeout=30)
        except Exception as e:
            ok = False
            print("Discord送信エラー:", e)
    return ok


def ai_countermeasure(query, prev_pos, cur_pos, clicks, impr_prev, impr_cur):
    """下落したKWについて、原因の推定＋回復対策をAIに生成させる"""
    global QUOTA_HIT
    if not GEMINI_API_KEY:
        return None

    # 表示回数の増減から原因の当たりをつけ、AIへのヒントにする
    if impr_prev and impr_cur >= impr_prev * 0.8:
        hint = "表示回数は維持されているのに順位が下落＝競合に追い抜かれた可能性が高い"
    elif impr_prev and impr_cur < impr_prev * 0.8:
        hint = "順位・表示回数とも下落＝検索需要の季節変動、またはインデックス/技術的問題の可能性"
    else:
        hint = "傾向不明"

    prompt = (
        "あなたはSEOの専門家です。以下の検索キーワードで掲載順位が下落しました。"
        "考えられる原因と、順位を回復させるための具体的な対策を3つ、箇条書きで簡潔に提案してください。\n\n"
        f"サイト: {SITE_CONTEXT}\n"
        f"キーワード: {query}\n"
        f"掲載順位: {prev_pos:.1f}位 → {cur_pos:.1f}位（下落）\n"
        f"表示回数: {int(impr_prev)} → {int(impr_cur)}\n"
        f"直近クリック: {int(clicks)}\n"
        f"推定される状況: {hint}"
    )
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}")
    data = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode("utf-8")
    last = ""
    for attempt in range(3):
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
            if e.code == 429 or "RESOURCE_EXHAUSTED" in body:
                QUOTA_HIT = True
                return "(無料枠上限のためAI対策をスキップ)"
            time.sleep(3 * (attempt + 1))
        except Exception as e:
            last = str(e)
            time.sleep(3 * (attempt + 1))
    return f"(AI対策の取得に失敗: {last})"


def read_csv_rows(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.reader(f))


def load_history():
    if not os.path.exists(HIST):
        return []
    with open(HIST, newline="", encoding="utf-8-sig") as f:
        return list(csv.reader(f))


# ========================= メイン =========================
def main():
    os.makedirs(INBOX, exist_ok=True)
    os.makedirs(DONE, exist_ok=True)

    files = sorted(glob.glob(os.path.join(INBOX, "*.csv")))
    if not files:
        msg = "⚠️ 『取込』フォルダにCSVがありません。サチコのクエリCSVを入れてから実行してください。"
        print(msg)
        send_discord(msg)
        return

    today = datetime.now().strftime("%Y-%m-%d")

    # --- 取込CSVを集約（複数ファイルOK・ヘッダー自動スキップ）---
    current = {}
    for path in files:
        for row in read_csv_rows(path):
            if len(row) < 5:
                continue
            q = str(row[0]).strip()
            if not q or not is_number(row[1]):  # 空行・ヘッダー行を除外
                continue
            current[q] = {
                "query": q,
                "clicks": to_num(row[1]),
                "impr":   to_num(row[2]),
                "ctr":    to_ctr(row[3]),
                "pos":    to_num(row[4]),
            }

    if not current:
        msg = "⚠️ CSVからデータを読めませんでした。列が『クエリ/クリック/表示/CTR/順位』の順か確認してください。"
        print(msg)
        send_discord(msg)
        return

    rows_list = list(current.values())

    # --- 前回取込分を比較用に読み込む ---
    hist = load_history()
    body = hist[1:] if hist else []  # 1行目ヘッダー想定
    dates = sorted({r[0] for r in body if r and r[0] and r[0] != today})
    prev_date = dates[-1] if dates else None
    prev_map = {}
    if prev_date:
        for r in body:
            if len(r) >= 6 and r[0] == prev_date:
                prev_map[str(r[1]).strip()] = {"pos": to_num(r[5]), "clicks": to_num(r[2]),
                                               "impr": to_num(r[3])}

    # --- 履歴に今回分を追記 ---
    write_header = not os.path.exists(HIST)
    with open(HIST, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(["取込日", "クエリ", "クリック数", "表示回数", "CTR", "掲載順位"])
        for c in rows_list:
            w.writerow([today, c["query"], int(c["clicks"]), int(c["impr"]),
                        round(c["ctr"], 2), c["pos"]])

    # --- 集計・分析 ---
    total_clicks = int(sum(c["clicks"] for c in rows_list))
    total_impr   = int(sum(c["impr"] for c in rows_list))

    drops, ups = [], []
    if prev_date:
        for c in rows_list:
            p = prev_map.get(c["query"])
            if not p or not p["pos"] or not c["pos"]:
                continue
            diff = c["pos"] - p["pos"]  # プラス=順位が下がった(悪化)
            if diff >= RANK_MOVE:
                drops.append((c["query"], p["pos"], c["pos"], diff))
            elif diff <= -RANK_MOVE:
                ups.append((c["query"], p["pos"], c["pos"], diff))
        drops.sort(key=lambda x: -x[3])
        ups.sort(key=lambda x: x[3])

    # --- 急上昇KW（表示回数が前週比で急増＝検索需要が伸びている）---
    surges = []
    if prev_date:
        for c in rows_list:
            if c["impr"] < SURGE_MIN_IMPR:
                continue
            p = prev_map.get(c["query"])
            if not p or not p.get("impr"):
                surges.append((c["query"], 0, c["impr"], None, c["pos"]))  # 前週に無い＝新規出現
            else:
                ip = p["impr"]
                if ip <= 0:
                    continue
                ratio = (c["impr"] - ip) / ip
                if ratio >= SURGE_RATIO:
                    surges.append((c["query"], ip, c["impr"], ratio, c["pos"]))
        surges.sort(key=lambda x: -(x[2] - x[1]))  # 表示回数の増加数が大きい順

    ctr_chance = [c for c in rows_list
                  if c["impr"] >= IMP_MIN and c["pos"] <= RANK_MAX and c["ctr"] < CTR_LOW]
    ctr_chance.sort(key=lambda c: -c["impr"])

    # --- Discordメッセージ ---
    m = f"📊 **サチコ週次レポート（{today}）**\n"
    m += f"対象クエリ数: {len(rows_list)}件 / 合計クリック: {total_clicks} / 合計表示: {total_impr}\n"
    m += (f"前回取込: {prev_date} と比較\n" if prev_date
          else "※初回取込。次回から順位変動を比較します。\n")

    if prev_date:
        m += "\n🔻 **順位下落 TOP（要対策）**\n"
        m += ("\n".join(f"・{q}：{a:.1f}位 → {b:.1f}位（+{d:.1f}）"
                        for q, a, b, d in drops[:8]) if drops else "なし 👍")
        m += "\n\n🔺 **順位上昇 TOP**\n"
        m += ("\n".join(f"・{q}：{a:.1f}位 → {b:.1f}位（{d:.1f}）"
                        for q, a, b, d in ups[:8]) if ups else "なし")

        m += "\n\n🚀 **急上昇KW（検索需要が伸びている＝記事強化のチャンス）**\n"
        if surges:
            sl = []
            for q, ip, ic, ratio, pos in surges[:8]:
                if ratio is None:
                    sl.append(f"・{q}：🆕新規 表示{int(ic)}（現在{pos:.1f}位）")
                else:
                    sl.append(f"・{q}：表示 {int(ip)}→{int(ic)}（+{ratio*100:.0f}%）現在{pos:.1f}位")
            m += "\n".join(sl)
        else:
            m += "なし"

    m += "\n\n💡 **CTR改善候補（タイトル見直し）**\n"
    m += ("\n".join(f"・{c['query']}：{c['pos']:.1f}位 / 表示{int(c['impr'])} / CTR{c['ctr']:.1f}%"
                    for c in ctr_chance[:8]) if ctr_chance else "なし")

    # --- 下落KWへのAI対策提示 ---
    if prev_date and drops and GEMINI_API_KEY:
        parts = []
        for q, a, b, d in drops[:AI_DROP_LIMIT]:
            impr_cur  = current.get(q, {}).get("impr", 0)
            impr_prev = prev_map.get(q, {}).get("impr", 0)
            clicks_cur = current.get(q, {}).get("clicks", 0)
            adv = ai_countermeasure(q, a, b, clicks_cur, impr_prev, impr_cur)
            parts.append(f"\n\n【{q}】{a:.1f}位→{b:.1f}位（表示 {int(impr_prev)}→{int(impr_cur)}）\n{adv}")
        m += f"\n\n🛠 **下落KWへのAI対策（上位{len(parts)}件）**" + "".join(parts)

    print(m)
    send_discord(m)

    # --- 課金アラート：AIの無料枠を超えたら必ず通知 ---
    if QUOTA_HIT:
        alert = (
            "🚨 **【課金アラート】Gemini APIの無料枠上限に達しました**\n"
            "下落KWへのAI対策は一部スキップされました。\n"
            "このまま使い続けるにはGoogle Cloudでの課金設定が必要になる可能性があります。\n"
            "※課金設定をしない限り自動で請求されることはありません（超過時は停止するだけ）。"
        )
        print(alert)
        send_discord(alert)

    # --- 処理済みCSVを『済み』へ移動 ---
    for path in files:
        dst = os.path.join(DONE, today + "_" + os.path.basename(path))
        try:
            shutil.move(path, dst)
        except Exception as e:
            print("ファイル移動エラー:", e)

    print("\n✅ 完了しました。")


if __name__ == "__main__":
    main()
