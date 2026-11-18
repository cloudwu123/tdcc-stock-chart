import csv
import io
import json
import os
import re
from datetime import date
 
import requests
 
 
# 要追蹤的股票：股票代號: 顯示名稱
# 增加股票時，只要在這裡多加一組即可。
STOCKS = {
    "2330": "台積電",
    "2308": "台達電",
    "2345": "智邦",
}
 
# 持股級距 12–15：作為 400 張以上的觀察值
FIRST_BIG_HOLDER_LEVEL = 12
LAST_BIG_HOLDER_LEVEL = 15
 
# TDCC 最新一期 CSV
TDCC_URL = "https://opendata.tdcc.com.tw/getOD.ashx?id=1-5"
 
# 公開歷史快照歸檔
ARCHIVE_TREE_API = (
    "https://api.github.com/repos/"
    "wirelessr/tdcc-opendata-archive/git/trees/main?recursive=1"
)
ARCHIVE_RAW_BASE = (
    "https://raw.githubusercontent.com/"
    "wirelessr/tdcc-opendata-archive/main/"
)
 
 
def six_months_ago(d):
    """取得六個日曆月前的日期。"""
    import calendar
 
    year = d.year
    month = d.month - 6
 
    while month <= 0:
        year -= 1
        month += 12
 
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)
 
 
def parse_date(value):
    """將 YYYYMMDD 或 YYYY-MM-DD 轉為 date。"""
    value = str(value).strip()
 
    if re.fullmatch(r"\d{8}", value):
        return date(
            int(value[:4]),
            int(value[4:6]),
            int(value[6:8]),
        )
 
    return date.fromisoformat(value)
 
 
def read_snapshot(csv_bytes, start_date, end_date, results):
    """讀取一份 TDCC CSV，累計各股票各日期的大戶級距比例。
 
    同一份 CSV 內會加總指定級距；
    若不同 CSV 有相同日期，後讀入的快照會覆蓋先前資料。
    """
    text = csv_bytes.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
 
    # 先在目前這份 CSV 內加總各級距，
    # 避免覆蓋掉同一份檔案內尚未加總完的級距。
    snapshot_totals = {}
 
    for row in reader:
        # 去除 CSV 欄位名稱和欄位值前後空白
        row = {
            (key or "").strip(): (value or "").strip()
            for key, value in row.items()
        }
 
        code = row.get("證券代號", "").strip()
 
        if code not in results:
            continue
 
        try:
            snapshot_date = parse_date(row["資料日期"])
            level = int(float(row["持股分級"]))
            ratio = float(
                row["占集保庫存數比例%"]
                .replace(",", "")
                .replace("%", "")
            )
        except (KeyError, ValueError):
            continue
 
        if snapshot_date < start_date or snapshot_date > end_date:
            continue
 
        # 只加總大戶持股級距，略過其他級距、差異調整與合計列
        if FIRST_BIG_HOLDER_LEVEL <= level <= LAST_BIG_HOLDER_LEVEL:
            date_key = snapshot_date.isoformat()
            key = (code, date_key)
            snapshot_totals[key] = snapshot_totals.get(key, 0.0) + ratio
 
    # 寫回結果。
    # 若這個日期已由先前的歸檔資料寫入，便以目前這份 CSV 覆蓋。
    for (code, date_key), total in snapshot_totals.items():
        results[code][date_key] = total
 
 
def main():
    if not STOCKS:
        raise RuntimeError("請先在 STOCKS 設定至少一檔股票。")
 
    today = date.today()
    start_date = six_months_ago(today)
 
    session = requests.Session()
    session.headers.update({
        "User-Agent": "tdcc-multi-stock-chart/1.0"
    })
 
    results = {code: {} for code in STOCKS}
 
    # 取得歷史快照檔案清單
    response = session.get(ARCHIVE_TREE_API, timeout=30)
    response.raise_for_status()
    tree = response.json()
 
    archive_files = []
 
    for item in tree.get("tree", []):
        path = item.get("path", "")
 
        match = re.fullmatch(
            r"snapshots/\d{4}/(\d{4}-\d{2}-\d{2})\.csv",
            path,
        )
 
        if not match:
            continue
 
        snapshot_date = date.fromisoformat(match.group(1))
 
        if start_date <= snapshot_date <= today:
            archive_files.append((snapshot_date, path))
 
    # 依日期順序下載近六個月歸檔快照
    for _, path in sorted(archive_files):
        url = ARCHIVE_RAW_BASE + path
        response = session.get(url, timeout=60)
        response.raise_for_status()
 
        read_snapshot(
            response.content,
            start_date,
            today,
            results,
        )
 
    # 最後讀取 TDCC 最新資料。
    # 若日期與歸檔快照重複，最新資料會覆蓋歸檔資料。
    response = session.get(TDCC_URL, timeout=60)
    response.raise_for_status()
 
    read_snapshot(
        response.content,
        start_date,
        today,
        results,
    )
 
    # 建立網站資料目錄
    os.makedirs("site", exist_ok=True)
 
    # 產生網頁下拉選單使用的股票清單
    with open("site/stocks.json", "w", encoding="utf-8") as f:
        json.dump(STOCKS, f, ensure_ascii=False, indent=2)
 
    # 每檔股票各自輸出一份 CSV
    for code, name in STOCKS.items():
        stock_results = results[code]
 
        if not stock_results:
            raise RuntimeError(
                f"找不到股票 {code}（{name}）的資料。"
                "請確認股票代號，或檢查歷史資料來源。"
            )
 
        output_path = f"site/trend-{code}.csv"
 
        with open(
            output_path,
            "w",
            newline="",
            encoding="utf-8-sig",
        ) as f:
            writer = csv.writer(f)
            writer.writerow(["date", "big_holder_percent"])
 
            for snapshot_date in sorted(stock_results):
                writer.writerow([
                    snapshot_date,
                    f"{stock_results[snapshot_date]:.2f}",
                ])
 
        print(
            f"{code} {name}：已輸出 "
            f"{len(stock_results)} 期資料"
        )
 
    print(f"完成，資料期間：{start_date} 至 {today}")
 
 
if __name__ == "__main__":
    main()