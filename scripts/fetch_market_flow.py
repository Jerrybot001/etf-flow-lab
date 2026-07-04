#!/usr/bin/env python3
"""
Fetch TWSE institutional flows and TAIFEX foreign futures positions.

Rules:
- Use official TWSE / TAIFEX sources only.
- If today's official data is not available, keep the latest previous available snapshot.
- Never relabel previous data as today's value.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "market-flow" / "history.json"
TZ = dt.timezone(dt.timedelta(hours=8))

TWSE_BFI82U = "https://www.twse.com.tw/rwd/zh/fund/BFI82U?dayDate={date}&type=day&response=json"
TAIFEX_CANDIDATES = [
    "https://www.taifex.com.tw/cht/3/futContractsDateDown?queryStartDate={date_slash}&queryEndDate={date_slash}",
    "https://www.taifex.com.tw/cht/3/futContractsDateDown?queryDate={date_slash}",
]


def today_tw() -> dt.date:
    return dt.datetime.now(TZ).date()


def ntd_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    s = str(value).replace(",", "").replace(" ", "").strip()
    if not s or s in {"-", "--", "—"}:
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def fetch_text(url: str) -> str:
    req = Request(url, headers={"User-Agent": "ETF-Flow-Lab/1.0"})
    with urlopen(req, timeout=30) as resp:
        raw = resp.read()
    for enc in ("utf-8-sig", "utf-8", "big5", "cp950"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", errors="ignore")


def fetch_json(url: str) -> Dict[str, Any]:
    return json.loads(fetch_text(url))


def date_range_back(days: int = 10) -> List[dt.date]:
    base = today_tw()
    return [base - dt.timedelta(days=i) for i in range(days)]


def fetch_twse_one(date: dt.date) -> Optional[Dict[str, Any]]:
    url = TWSE_BFI82U.format(date=date.strftime("%Y%m%d"))
    try:
        payload = fetch_json(url)
    except Exception:
        return None
    rows = payload.get("data") or []
    if not rows:
        return None

    result: Dict[str, Any] = {
        "date": date.isoformat(),
        "sourceName": "TWSE 三大法人買賣金額統計表",
        "sourceUrl": url,
        "foreignInvestor": None,
        "investmentTrust": None,
        "dealer": None,
        "total": None,
        "rawRows": rows,
    }

    foreign_sum = 0
    foreign_seen = False
    dealer_sum = 0
    dealer_seen = False

    for row in rows:
        if len(row) < 4:
            continue
        name = str(row[0]).strip()
        diff = ntd_int(row[3])
        if diff is None:
            continue

        # TWSE usually has both:
        # - 外資及陸資(不含外資自營商)
        # - 外資自營商
        # These belong to foreign investor flow and must be summed. Do not let
        # the later 外資自營商 row overwrite the main 外資及陸資 row.
        if name.startswith("外資及陸資") or name.startswith("外資自營商"):
            foreign_sum += diff
            foreign_seen = True
        elif name == "投信" or name.startswith("投信"):
            result["investmentTrust"] = diff
        elif name.startswith("自營商") and "合計" not in name:
            dealer_sum += diff
            dealer_seen = True
        elif "合計" in name:
            result["total"] = diff

    if foreign_seen:
        result["foreignInvestor"] = foreign_sum
    if dealer_seen:
        result["dealer"] = dealer_sum

    if any(result.get(k) is not None for k in ["foreignInvestor", "investmentTrust", "dealer", "total"]):
        return result
    return None


def fetch_latest_twse() -> Optional[Dict[str, Any]]:
    for d in date_range_back(12):
        item = fetch_twse_one(d)
        if item:
            return item
    return None


def parse_taifex_rows(text: str) -> List[List[str]]:
    rows = []
    for row in csv.reader(io.StringIO(text)):
        cleaned = [c.strip() for c in row]
        if any(cleaned):
            rows.append(cleaned)
    return rows


def normalize(text: str) -> str:
    return re.sub(r"\s+", "", str(text))


def find_col(headers: List[str], names: List[str]) -> Optional[int]:
    normalized = [normalize(h) for h in headers]
    for name in names:
        n = normalize(name)
        for i, h in enumerate(normalized):
            if n in h:
                return i
    return None


def parse_taifex(text: str, date: dt.date, url: str) -> Optional[Dict[str, Any]]:
    rows = parse_taifex_rows(text)
    if not rows:
        return None
    header_i = None
    for i, row in enumerate(rows[:20]):
        joined = "|".join(row)
        if "身份別" in joined or "身分別" in joined:
            header_i = i
            break
    if header_i is None:
        return None
    headers = rows[header_i]
    product_col = find_col(headers, ["商品名稱", "契約名稱", "商品"])
    role_col = find_col(headers, ["身份別", "身分別"])
    long_oi_col = find_col(headers, ["多方未平倉口數", "多方未平倉"])
    short_oi_col = find_col(headers, ["空方未平倉口數", "空方未平倉"])
    net_oi_col = find_col(headers, ["多空未平倉口數淨額", "未平倉口數淨額", "多空未平倉淨額"])
    if role_col is None:
        return None

    for row in rows[header_i + 1:]:
        joined = "|".join(row)
        if "外資" not in joined:
            continue
        if product_col is not None and product_col < len(row):
            product = row[product_col]
            if not ("臺股期貨" in product or "台股期貨" in product or product.strip().upper() == "TX"):
                continue
        long_oi = ntd_int(row[long_oi_col]) if long_oi_col is not None and long_oi_col < len(row) else None
        short_oi = ntd_int(row[short_oi_col]) if short_oi_col is not None and short_oi_col < len(row) else None
        net_oi = ntd_int(row[net_oi_col]) if net_oi_col is not None and net_oi_col < len(row) else None
        if net_oi is None and long_oi is not None and short_oi is not None:
            net_oi = long_oi - short_oi
        if net_oi is not None:
            return {
                "date": date.isoformat(),
                "sourceName": "TAIFEX 三大法人期貨未平倉",
                "sourceUrl": url,
                "product": "臺股期貨",
                "foreignLongOpenInterest": long_oi,
                "foreignShortOpenInterest": short_oi,
                "foreignNetOpenInterest": net_oi,
                "rawRow": row,
            }
    return None


def fetch_taifex_one(date: dt.date) -> Optional[Dict[str, Any]]:
    slash = quote(date.strftime("%Y/%m/%d"), safe="")
    for tmpl in TAIFEX_CANDIDATES:
        url = tmpl.format(date_slash=slash)
        try:
            text = fetch_text(url)
            item = parse_taifex(text, date, url)
            if item:
                return item
        except Exception:
            continue
    return None


def fetch_latest_taifex() -> Optional[Dict[str, Any]]:
    for d in date_range_back(12):
        item = fetch_taifex_one(d)
        if item:
            return item
    return None


def load_existing() -> Dict[str, Any]:
    if OUT.exists():
        return json.loads(OUT.read_text(encoding="utf-8"))
    return {"history": []}


def fmt_int(value: Optional[int]) -> Optional[str]:
    if value is None:
        return None
    return f"{value:,}"


def main() -> int:
    existing = load_existing()
    twse = fetch_latest_twse()
    taifex = fetch_latest_taifex()
    now = dt.datetime.now(TZ).isoformat()
    base_date = today_tw().isoformat()

    if twse is None:
        twse = existing.get("institutional", {})
    if taifex is None:
        taifex = existing.get("futures", {})

    history = existing.get("history", [])
    if twse and twse.get("date"):
        history = [h for h in history if not (h.get("type") == "institutional" and h.get("date") == twse.get("date"))]
        history.append({"type": "institutional", "date": twse.get("date"), "data": twse})
    if taifex and taifex.get("date"):
        history = [h for h in history if not (h.get("type") == "futures" and h.get("date") == taifex.get("date"))]
        history.append({"type": "futures", "date": taifex.get("date"), "data": taifex})
    history.sort(key=lambda x: (x.get("date", ""), x.get("type", "")))

    output = {
        "lastUpdatedAt": now,
        "displayStage": "market-flow official fetch",
        "runDate": base_date,
        "rule": "首頁可顯示前一可用資料，但必須標示資料日；不可把前一日或舊資料當成今日值。",
        "institutional": twse,
        "futures": taifex,
        "summary": {
            "institutionalLabel": f"TWSE 三大法人 {twse.get('date')}" if twse and twse.get("date") else "TWSE 三大法人待接資料",
            "futuresLabel": f"TAIFEX 外資期貨 {taifex.get('date')}" if taifex and taifex.get("date") else "TAIFEX 外資期貨待接資料",
            "foreignInvestorText": fmt_int(twse.get("foreignInvestor")) if twse else None,
            "investmentTrustText": fmt_int(twse.get("investmentTrust")) if twse else None,
            "dealerText": fmt_int(twse.get("dealer")) if twse else None,
            "foreignFuturesNetText": fmt_int(taifex.get("foreignNetOpenInterest")) if taifex else None,
        },
        "history": history,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("market flow updated", output["summary"])
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
