#!/bin/bash
# ダブルクリックで competitors.txt の各サイトのsitemapが取得できるか確認します
# （Discordには送りません。登録前の下見用）
cd "$(dirname "$0")"
python3 competitor_watch.py check
echo ""
echo "----------------------------------------"
echo "✅が付いたサイトは監視できます。❌や0件は別サイトに差し替えてください。"
echo "このウィンドウは閉じてOKです。"
echo "----------------------------------------"
