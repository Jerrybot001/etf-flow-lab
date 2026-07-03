#!/usr/bin/env python3
"""
Import an official 00981A holdings file into data/00981A/holdings-history.json.

Usage:
  python scripts/import_00981A_snapshot.py --date 2026-07-01 --file official/00981A/2026-07-01.csv --source-url https://official-url

Accepted file formats:
  .csv, .xlsx
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
HISTORY_PATH = ROOT / "data" / "00981A" / "holdings-history.json"
TZ = dt.timezone(dt.timedelta(hours=8))


def clean_number(value: Any) -> Optional[float]:
    if value is None:
        return None
    s = str(value).strip().replace(",", "").replace("%", "")
    if not s or s in {"-", "—", "--"}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def norm(text: str) -> str:
    return re.sub(r"\s+", "", str(text).lower())


def read_csv(path: Path) -> List[List[str]]:
    raw = path.read_bytes()
    text = None
    for enc in ("utf-8-sig", "big5", "cp950", "utf-8"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            pass
    if text is None:
        text = raw.decode("utf-8", errors="ignore")
    return [[str(c).strip() for c in row] for row in csv.reader(io.StringIO(text))]


def read_xlsx(path: Path) -> List[List[str]]:
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    rows: List[List[str]] = []
    for ws in wb.worksheets:
        for row in ws.iter_rows(values_only=True):
            vals = ["" if v is None else str(v).strip() for v in row]
            if any(vals):
                rows.append(vals)
    return rows


def find_header(rows: List[List[str]]) -> Optional[int]:
    keys = ["股票代號", "證券代號", "代號", "股票名稱", "證券名稱", "權重", "投資比例", "持股"]
    for i, row in enumerate(rows[:40]):
        joined = "|".join(row)
        if any(k in joined for k in keys):
            return i
    return None


def pick(data: Dict[str, str], keys: List[str]) -> str:
    for key in keys:
        value = data.get(norm(key), "")
        if value:
            return value
    return ""


def parse_rows(rows: List[List[str]]) -> List[Dict[str, Any]]:
    header_i = find_header(rows)
    if header_i is None:
        raise ValueError("找不到標題列，請確認官方檔案內含股票代號/名稱/權重等欄位")
    headers = rows[header_i]
    holdings: List[Dict[str, Any]] = []
    for row in rows[header_i + 1:]:
        if not any(row):
            continue
        data = {norm(h): row[i].strip() if i < len(row) else "" for i, h in enumerate(headers)}
        code = pick(data, ["股票代號", "證券代號", "代號", "stockCode", "code"])
        name = pick(data, ["股票名稱", "證券名稱", "名稱", "成分股", "stockName", "name"])
        weight = pick(data, ["權重%", "權重", "投資比例", "比例", "weight", "weightPct"])
        shares = pick(data, ["股數", "張數", "持有股數", "shares", "share"])
        value = pick(data, ["市值", "持有市值", "金額", "marketValue", "value"])
        rank = pick(data, ["排名", "序號", "rank"])
        if not code and len(row) >= 1:
            maybe = re.sub(r"\D", "", row[0])
            if len(maybe) in {4, 5, 6}:
                code = maybe
        if not name and len(row) >= 2:
            name = row[1].strip()
        if not code and not name:
            continue
        holdings.append({
            "rank": int(clean_number(rank) or len(holdings) + 1),
            "stockCode": code,
            "stockName": name,
            "shares": clean_number(shares),
            "weightPct": clean_number(weight),
            "marketValue": clean_number(value)
        })
    if not holdings:
        raise ValueError("官方檔案解析後沒有持股資料")
    return holdings


def load_history() -> Dict[str, Any]:
    return json.loads(HISTORY_PATH.read_text(encoding="utf-8"))


def save_history(data: Dict[str, Any]) -> None:
    HISTORY_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def compute_changes(snapshots: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    official = [s for s in snapshots if s.get("status") == "official_snapshot_saved" and s.get("holdings")]
    official.sort(key=lambda s: s["date"])
    changes: List[Dict[str, Any]] = []
    for prev, cur in zip(official, official[1:]):
        prev_map = {h.get("stockCode") or h.get("stockName"): h for h in prev.get("holdings", [])}
        cur_map = {h.get("stockCode") or h.get("stockName"): h for h in cur.get("holdings", [])}
        for key in sorted(set(prev_map) | set(cur_map)):
            p = prev_map.get(key)
            c = cur_map.get(key)
            base = c or p or {}
            if p and not c:
                kind = "移出"
            elif c and not p:
                kind = "新增"
            else:
                kind = "續抱"
            wchg = None
            schg = None
            if p and c and p.get("weightPct") is not None and c.get("weightPct") is not None:
                wchg = round(c["weightPct"] - p["weightPct"], 4)
            if p and c and p.get("shares") is not None and c.get("shares") is not None:
                schg = round(c["shares"] - p["shares"], 4)
            changes.append({
                "date": cur["date"],
                "stockCode": base.get("stockCode"),
                "stockName": base.get("stockName"),
                "changeType": kind,
                "weightChangePctPoint": wchg,
                "shareChange": schg
            })
    return changes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--file", required=True)
    parser.add_argument("--source-url", default=None)
    args = parser.parse_args()

    path = Path(args.file)
    if path.suffix.lower() == ".csv":
        rows = read_csv(path)
    elif path.suffix.lower() in {".xlsx", ".xlsm"}:
        rows = read_xlsx(path)
    else:
        raise ValueError("只支援 CSV / XLSX")

    holdings = parse_rows(rows)
    history = load_history()
    snapshots = [s for s in history.get("snapshots", []) if s.get("date") != args.date]
    snapshots.append({
        "date": args.date,
        "status": "official_snapshot_saved",
        "sourceName": "統一投信官網",
        "sourceUrl": args.source_url,
        "fetchedAt": dt.datetime.now(TZ).isoformat(),
        "note": f"匯入官方持股檔，共 {len(holdings)} 筆。",
        "holdings": holdings
    })
    snapshots.sort(key=lambda x: x.get("date", ""))
    history["snapshots"] = snapshots
    history["lastSnapshotDate"] = args.date
    history["lastUpdatedAt"] = dt.datetime.now(TZ).isoformat()
    history["sourceStatus"] = "official_snapshot_imported"
    history["changes"] = compute_changes(snapshots)
    history["summary"] = {
        "range": f"{history.get('historyStartDate', '2026-07-01')} ~ {args.date}",
        "officialSnapshotsSaved": len([s for s in snapshots if s.get("status") == "official_snapshot_saved"]),
        "pendingSnapshots": len([s for s in snapshots if s.get("status") != "official_snapshot_saved"]),
        "latestStatusText": f"已匯入 {args.date} 官方快照，共 {len(holdings)} 筆",
        "displayMode": "顯示官方持股與異動"
    }
    save_history(history)
    print(f"Imported {len(holdings)} holdings for {args.date}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
