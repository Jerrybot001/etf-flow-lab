#!/usr/bin/env python3
"""
Fetch 00981A official daily holdings and append to local history.

Data rule:
- Only official Uni-President SITC / UPAMC disclosure URLs are accepted.
- If no official endpoint is configured, do not write fake holdings.
- The frontend reads data/00981A/holdings-history.json only.

To activate:
1. Put the official 00981A daily holdings URL in data/00981A/source-policy.json:
   primarySource.url = "https://..."
2. Run: python scripts/fetch_00981A_official.py
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
import sys
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
HISTORY_PATH = ROOT / "data" / "00981A" / "holdings-history.json"
POLICY_PATH = ROOT / "data" / "00981A" / "source-policy.json"
FUND_CODE = "00981A"
TZ = dt.timezone(dt.timedelta(hours=8))


class SimpleTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_cell = False
        self.in_row = False
        self.current_cell: List[str] = []
        self.current_row: List[str] = []
        self.rows: List[List[str]] = []

    def handle_starttag(self, tag: str, attrs: List[tuple[str, Optional[str]]]) -> None:
        if tag.lower() == "tr":
            self.in_row = True
            self.current_row = []
        if tag.lower() in {"td", "th"} and self.in_row:
            self.in_cell = True
            self.current_cell = []

    def handle_data(self, data: str) -> None:
        if self.in_cell:
            self.current_cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"td", "th"} and self.in_cell:
            text = "".join(self.current_cell).strip()
            text = re.sub(r"\s+", " ", text)
            self.current_row.append(text)
            self.in_cell = False
        if tag.lower() == "tr" and self.in_row:
            if any(cell for cell in self.current_row):
                self.rows.append(self.current_row)
            self.in_row = False


def load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def official_url() -> Optional[str]:
    policy = load_json(POLICY_PATH)
    primary = policy.get("primarySource") or {}
    url = primary.get("url")
    if isinstance(url, str) and url.startswith("http"):
        return url
    return None


def fetch_bytes(url: str) -> tuple[bytes, str]:
    req = Request(url, headers={"User-Agent": "ETF-Flow-Lab/1.0 (+GitHub Actions)"})
    with urlopen(req, timeout=30) as resp:
        content_type = resp.headers.get("content-type", "")
        return resp.read(), content_type


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


def normalize_header(s: str) -> str:
    return re.sub(r"\s+", "", str(s).lower())


def row_to_holding(headers: List[str], row: List[str], rank_fallback: int) -> Optional[Dict[str, Any]]:
    data = {normalize_header(h): (row[i].strip() if i < len(row) else "") for i, h in enumerate(headers)}
    code_keys = ["股票代號", "證券代號", "代號", "stockcode", "code"]
    name_keys = ["股票名稱", "證券名稱", "名稱", "成分股", "stockname", "name"]
    weight_keys = ["權重%", "權重", "投資比例", "比例", "weight", "weightpct"]
    share_keys = ["股數", "張數", "持有股數", "shares", "share"]
    value_keys = ["市值", "持有市值", "金額", "marketvalue", "value"]
    rank_keys = ["排名", "rank", "序號", "#"]

    def pick(keys: Iterable[str]) -> str:
        for k in keys:
            nk = normalize_header(k)
            if nk in data and data[nk]:
                return data[nk]
        return ""

    code = pick(code_keys)
    name = pick(name_keys)
    if not code and row:
        maybe_code = re.sub(r"\D", "", row[0])
        if len(maybe_code) in {4, 5, 6}:
            code = maybe_code
    if not name and len(row) >= 2:
        name = row[1].strip()
    if not code and not name:
        return None

    return {
        "rank": int(clean_number(pick(rank_keys)) or rank_fallback),
        "stockCode": code,
        "stockName": name,
        "shares": clean_number(pick(share_keys)),
        "weightPct": clean_number(pick(weight_keys)),
        "marketValue": clean_number(pick(value_keys)),
    }


def parse_csv_bytes(raw: bytes) -> List[Dict[str, Any]]:
    text = None
    for enc in ("utf-8-sig", "big5", "cp950"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            pass
    if text is None:
        text = raw.decode("utf-8", errors="ignore")
    rows = list(csv.reader(io.StringIO(text)))
    return rows_to_holdings(rows)


def parse_html_bytes(raw: bytes) -> List[Dict[str, Any]]:
    text = raw.decode("utf-8", errors="ignore")
    parser = SimpleTableParser()
    parser.feed(text)
    return rows_to_holdings(parser.rows)


def parse_xlsx_bytes(raw: bytes) -> List[Dict[str, Any]]:
    try:
        import openpyxl  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("openpyxl is required for xlsx parsing") from exc
    wb = openpyxl.load_workbook(io.BytesIO(raw), data_only=True, read_only=True)
    all_rows: List[List[str]] = []
    for ws in wb.worksheets:
        for row in ws.iter_rows(values_only=True):
            vals = ["" if v is None else str(v).strip() for v in row]
            if any(vals):
                all_rows.append(vals)
    return rows_to_holdings(all_rows)


def rows_to_holdings(rows: List[List[str]]) -> List[Dict[str, Any]]:
    header_idx = None
    for idx, row in enumerate(rows[:30]):
        joined = "|".join(row)
        if any(k in joined for k in ["股票代號", "證券代號", "成分股", "權重", "投資比例"]):
            header_idx = idx
            break
    if header_idx is None:
        return []
    headers = rows[header_idx]
    holdings: List[Dict[str, Any]] = []
    for raw_row in rows[header_idx + 1 :]:
        if not any(str(x).strip() for x in raw_row):
            continue
        holding = row_to_holding(headers, raw_row, len(holdings) + 1)
        if holding:
            holdings.append(holding)
    return holdings


def parse_holdings(raw: bytes, content_type: str, url: str) -> List[Dict[str, Any]]:
    lower_url = url.lower()
    lower_type = content_type.lower()
    if lower_url.endswith(".xlsx") or "spreadsheet" in lower_type:
        return parse_xlsx_bytes(raw)
    if lower_url.endswith(".csv") or "csv" in lower_type:
        return parse_csv_bytes(raw)
    if lower_url.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            for name in zf.namelist():
                data = zf.read(name)
                if name.lower().endswith(".xlsx"):
                    return parse_xlsx_bytes(data)
                if name.lower().endswith(".csv"):
                    return parse_csv_bytes(data)
    return parse_html_bytes(raw)


def today_tw() -> str:
    return dt.datetime.now(TZ).date().isoformat()


def compute_changes(snapshots: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    official = [s for s in snapshots if s.get("status") == "official_snapshot_saved" and s.get("holdings")]
    official.sort(key=lambda s: s["date"])
    changes: List[Dict[str, Any]] = []
    for prev, cur in zip(official, official[1:]):
        prev_map = {h.get("stockCode") or h.get("stockName"): h for h in prev.get("holdings", [])}
        cur_map = {h.get("stockCode") or h.get("stockName"): h for h in cur.get("holdings", [])}
        keys = sorted(set(prev_map) | set(cur_map))
        for key in keys:
            p = prev_map.get(key)
            c = cur_map.get(key)
            base = c or p or {}
            if p and not c:
                change_type = "移出"
            elif c and not p:
                change_type = "新增"
            else:
                change_type = "續抱"
            changes.append({
                "date": cur["date"],
                "stockCode": base.get("stockCode"),
                "stockName": base.get("stockName"),
                "changeType": change_type,
                "weightChangePctPoint": round((c or {}).get("weightPct", 0) - (p or {}).get("weightPct", 0), 4) if c and p and c.get("weightPct") is not None and p.get("weightPct") is not None else None,
                "shareChange": round((c or {}).get("shares", 0) - (p or {}).get("shares", 0), 4) if c and p and c.get("shares") is not None and p.get("shares") is not None else None,
            })
    return changes


def main() -> int:
    hist = load_json(HISTORY_PATH)
    hist.setdefault("fundCode", FUND_CODE)
    hist.setdefault("fundName", "主動統一台股增長")
    hist.setdefault("issuer", "統一投信")
    hist.setdefault("snapshots", [])

    url = official_url()
    if not url:
        hist["sourceStatus"] = "awaiting_official_endpoint"
        hist["lastUpdatedAt"] = dt.datetime.now(TZ).isoformat()
        hist.setdefault("summary", {})
        hist["summary"].update({
            "latestStatusText": "尚未設定統一投信 00981A 官方每日持股固定端點",
            "officialSnapshotsSaved": len([s for s in hist.get("snapshots", []) if s.get("status") == "official_snapshot_saved"]),
            "pendingSnapshots": len([s for s in hist.get("snapshots", []) if s.get("status") != "official_snapshot_saved"]),
        })
        save_json(HISTORY_PATH, hist)
        print("No official endpoint configured. History file left without fake holdings.")
        return 0

    raw, content_type = fetch_bytes(url)
    holdings = parse_holdings(raw, content_type, url)
    if not holdings:
        raise RuntimeError(f"No holdings parsed from official endpoint: {url}")

    date = today_tw()
    fetched_at = dt.datetime.now(TZ).isoformat()
    snapshot = {
        "date": date,
        "status": "official_snapshot_saved",
        "sourceName": "統一投信官網",
        "sourceUrl": url,
        "fetchedAt": fetched_at,
        "note": f"由官方端點自動抓取，共 {len(holdings)} 筆。",
        "holdings": holdings,
    }

    snapshots = [s for s in hist.get("snapshots", []) if s.get("date") != date]
    snapshots.append(snapshot)
    snapshots.sort(key=lambda s: s.get("date", ""))
    hist["snapshots"] = snapshots
    hist["lastSnapshotDate"] = date
    hist["lastUpdatedAt"] = fetched_at
    hist["sourceStatus"] = "official_endpoint_connected"
    hist["changes"] = compute_changes(snapshots)
    hist["summary"] = {
        "range": f"{hist.get('historyStartDate', '2026-07-01')} ~ {date}",
        "officialSnapshotsSaved": len([s for s in snapshots if s.get("status") == "official_snapshot_saved"]),
        "pendingSnapshots": len([s for s in snapshots if s.get("status") != "official_snapshot_saved"]),
        "latestStatusText": f"已抓取 {date} 統一投信官方快照，共 {len(holdings)} 筆",
        "displayMode": "顯示官方持股與異動。",
    }
    save_json(HISTORY_PATH, hist)
    print(f"Saved {len(holdings)} official holdings for {date}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
