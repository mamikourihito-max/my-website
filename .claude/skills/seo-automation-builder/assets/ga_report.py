#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GA4 週次レポート（Macローカル版）

使い方:
  1. GA4「ページとスクリーン」レポートをCSVでダウンロード
  2. そのCSVを『取込』フォルダに入れる
  3. このスクリプト（またはレポート実行.command）を実行する
  4. 自動で分析して Discord に通知される

Googleアカウント連携・課金は一切不要。標準ライブラリのみで動作します。
"""

import os
import re
import csv
import glob
import json
import shutil
import urllib.request
from datetime import datetime

# ========================= 設定 =========================
DISCORD_WEBHOOK = "{{DISCORD_WEBHOOK}}"

VIEW_MIN    = 30    # これ未満のPVのページは「急上昇/急降下」判定から除外（ノイズ対策）
SPIKE_RATIO = 0.5   # 前週比でPVが±50%動いたら急変とみなす

# --- CRO（コンバージョン改善）アラートのしきい値 ---
CRO_MIN_VIEWS  = 100   # これ以上PVがある「集客できてる」ページだけを対象にする
ENGAGE_LOW_SEC = 30    # 平均エンゲージメント時間がこれ(秒)未満なら「動向が悪い」

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


def to_seconds(v):
    """平均エンゲージメント時間を秒に変換（'0:01:23' '1分23秒' '83' いずれもOK）"""
    s = str(v).strip()
    if not s:
        return None
    if ":" in s:  # 0:01:23 形式
        sec = 0
        for p in s.split(":"):
            sec = sec * 60 + to_num(p)
        return sec
    m = re.search(r"(?:(\d+)\s*分)?\s*(\d+)\s*秒", s)  # 1分23秒 形式
    if m:
        return (to_num(m.group(1)) * 60 if m.group(1) else 0) + to_num(m.group(2))
    return to_num(s)  # ただの秒数


def send_discord(msg):
    for i in range(0, len(msg), 1900):  # Discordは1通2000字まで
        chunk = msg[i:i + 1900]
        data = json.dumps({"content": chunk}).encode("utf-8")
        req = urllib.request.Request(
            DISCORD_WEBHOOK, data=data,
            headers={"Content-Type": "application/json",
                     "User-Agent": "Mozilla/5.0"})  # 無いとDiscordに403される
        try:
            urllib.request.urlopen(req, timeout=30)
        except Exception as e:
            print("Discord送信エラー:", e)


def find_col(header, keys):
    """ヘッダー行から、keysのいずれかを含む列の番号を返す（無ければNone）"""
    for i, h in enumerate(header):
        norm = str(h).replace(" ", "").lower()
        if any(k in norm for k in keys):
            return i
    return None


def parse_ga4_csv(path):
    """GA4のCSV（先頭に#注釈あり）を読む。
    返り値: (ページごとのdictリスト, どの指標列が存在したかのフラグdict)"""
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))

    header, start = None, 0
    for idx, row in enumerate(rows):
        if not row or str(row[0]).strip().startswith("#") or all(c == "" for c in row):
            continue  # 注釈行・空行はスキップ
        header, start = row, idx + 1  # 最初の実データ行＝ヘッダー
        break
    if header is None:
        return [], {}

    v_col = find_col(header, ["表示回数", "ビュー", "views", "screenpageviews"])
    u_col = find_col(header, ["ユーザー", "users"])
    e_col = find_col(header, ["エンゲージメント時間", "engagementtime", "engagementduration"])
    c_col = find_col(header, ["コンバージョン", "キーイベント", "conversions", "keyevent"])
    if v_col is None:
        v_col = 1

    flags = {"eng": e_col is not None, "conv": c_col is not None}

    result = []
    for row in rows[start:]:
        if not row or all(c == "" for c in row):
            break  # 表の終わり（GA4は表の区切りに空行が入る）
        page = str(row[0]).strip()
        if not page:
            continue
        result.append({
            "page":  page,
            "views": to_num(row[v_col]) if len(row) > v_col else 0,
            "users": to_num(row[u_col]) if (u_col is not None and len(row) > u_col) else 0,
            "eng":   to_seconds(row[e_col]) if (e_col is not None and len(row) > e_col) else None,
            "conv":  to_num(row[c_col]) if (c_col is not None and len(row) > c_col) else None,
        })
    return result, flags


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
        msg = "⚠️ 『取込』フォルダにCSVがありません。GA4のページCSVを入れてから実行してください。"
        print(msg)
        send_discord(msg)
        return

    today = datetime.now().strftime("%Y-%m-%d")

    # --- 取込CSVを集約（ページごとに合算）---
    current = {}
    flags = {"eng": False, "conv": False}
    for path in files:
        rows, f = parse_ga4_csv(path)
        flags["eng"]  = flags["eng"]  or f.get("eng", False)
        flags["conv"] = flags["conv"] or f.get("conv", False)
        for r in rows:
            page = r["page"]
            if page not in current:
                current[page] = {"page": page, "views": 0.0, "users": 0.0,
                                 "eng": None, "conv": None}
            current[page]["views"] += r["views"]
            current[page]["users"] += r["users"]
            if r["eng"] is not None:
                current[page]["eng"] = r["eng"]        # 平均値なので上書き
            if r["conv"] is not None:
                current[page]["conv"] = (current[page]["conv"] or 0) + r["conv"]

    if not current:
        msg = "⚠️ CSVからデータを読めませんでした。GA4「ページとスクリーン」のCSVか確認してください。"
        print(msg)
        send_discord(msg)
        return

    rows_list = list(current.values())

    # --- 前回取込分を比較用に読み込む ---
    hist = load_history()
    body = hist[1:] if hist else []
    dates = sorted({r[0] for r in body if r and r[0] and r[0] != today})
    prev_date = dates[-1] if dates else None
    prev_map = {}
    if prev_date:
        for r in body:
            if len(r) >= 3 and r[0] == prev_date:
                prev_map[str(r[1]).strip()] = to_num(r[2])  # ページ→PV

    # --- 履歴に今回分を追記 ---
    write_header = not os.path.exists(HIST)
    with open(HIST, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(["取込日", "ページ", "表示回数", "ユーザー数"])
        for c in rows_list:
            w.writerow([today, c["page"], int(c["views"]), int(c["users"])])

    # --- 集計・分析 ---
    total_views = int(sum(c["views"] for c in rows_list))
    total_users = int(sum(c["users"] for c in rows_list))

    spikes, drops, news = [], [], []
    if prev_date:
        for c in rows_list:
            if c["views"] < VIEW_MIN:
                continue
            if c["page"] not in prev_map:
                news.append((c["page"], c["views"]))
                continue
            prev = prev_map[c["page"]]
            if prev <= 0:
                continue
            ratio = (c["views"] - prev) / prev
            if ratio >= SPIKE_RATIO:
                spikes.append((c["page"], prev, c["views"], ratio))
            elif ratio <= -SPIKE_RATIO:
                drops.append((c["page"], prev, c["views"], ratio))
        spikes.sort(key=lambda x: -x[3])
        drops.sort(key=lambda x: x[3])
        news.sort(key=lambda x: -x[1])

    # --- CRO施策アラート（集客できてるのに動向が悪いページ）---
    cro = []
    for c in rows_list:
        if c["views"] < CRO_MIN_VIEWS:
            continue
        reasons = []
        if c["eng"] is not None and c["eng"] < ENGAGE_LOW_SEC:
            reasons.append(f"滞在{c['eng']:.0f}秒")
        if c["conv"] is not None and c["users"] > 0 and c["conv"] == 0:
            reasons.append("CV0件")
        if reasons:
            cro.append((c["page"], int(c["views"]), reasons))
    cro.sort(key=lambda x: -x[1])

    top = sorted(rows_list, key=lambda c: -c["views"])[:8]

    # --- Discordメッセージ ---
    m = f"📈 **GA4週次レポート（{today}）**\n"
    m += f"対象ページ数: {len(rows_list)} / 合計PV: {total_views} / 合計ユーザー: {total_users}\n"
    m += (f"前回取込: {prev_date} と比較\n" if prev_date
          else "※初回取込。次回からPV変動を比較します。\n")

    if prev_date:
        m += "\n🚀 **急上昇ページ**\n"
        m += ("\n".join(f"・{p}：{int(a)}→{int(b)}PV（+{r*100:.0f}%）"
                        for p, a, b, r in spikes[:8]) if spikes else "なし")
        m += "\n\n🆕 **新規ページ**\n"
        m += ("\n".join(f"・{p}：{int(v)}PV" for p, v in news[:5]) if news else "なし")
        m += "\n\n📉 **急降下ページ（テコ入れ候補）**\n"
        m += ("\n".join(f"・{p}：{int(a)}→{int(b)}PV（{r*100:.0f}%）"
                        for p, a, b, r in drops[:8]) if drops else "なし 👍")

    # CROアラート
    m += "\n\n🛠 **CRO施策が必要なページ（集客できてるのに動向が悪い）**\n"
    if not flags["eng"] and not flags["conv"]:
        m += "判定できません（CSVに『平均エンゲージメント時間』か『キーイベント/CV』列を含めてください）"
    else:
        m += ("\n".join(f"・{p}：{v}PV｜{' / '.join(rs)}" for p, v, rs in cro[:8])
              if cro else "なし 👍")

    m += "\n\n🏆 **PV TOP記事**\n"
    m += "\n".join(f"・{c['page']}：{int(c['views'])}PV / {int(c['users'])}人" for c in top)

    print(m)
    send_discord(m)

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
