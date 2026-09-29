from __future__ import annotations

import json
import math
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Anyimport pandas as pd
import requests
import pandas_market_calendars as mcal
import yfinance as yf


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "docs" / "data"
WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
UTC = timezone.utc


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def get_constituents() -> list[str]:
    """Fetch the current index members; use the last cached list if Wikipedia is unavailable."""
    cache = DATA_DIR / "constituents.json"
    try:
        response = requests.get(WIKI_URL, headers={"User-Agent": "Mozilla/5.0 market-breadth-dashboard"}, timeout=30)
        response.raise_for_status()
        table = pd.read_html(response.text, attrs={"id": "constituents"})[0]
        tickers = sorted(table["Symbol"].astype(str).str.replace(".", "-", regex=False).tolist())
        if len(tickers) < 490:
            raise RuntimeError(f"unexpected constituent count: {len(tickers)}")
        atomic_json(cache, {"updated_at": datetime.now(UTC).isoformat(), "tickers": tickers})
        return tickers
    except Exception as exc:
        cached = read_json(cache, {}).get("tickers", [])
        if cached:
            print(f"Constituent refresh failed; using cache: {exc}")
            return cached
        raise


def market_context(now: datetime) -> dict[str, Any]:
    nyse = mcal.get_calendar("NYSE")
    start = (now - timedelta(days=10)).date()
    end = (now + timedelta(days=2)).date()
    schedule = nyse.schedule(start_date=start, end_date=end)
    completed = schedule[schedule["market_close"] <= pd.Timestamp(now)]
    active = schedule[(schedule["market_open"] <= pd.Timestamp(now)) & (schedule["market_close"] > pd.Timestamp(now))]

    if not active.empty:
        row = active.iloc[-1]
        return {"status": "intraday", "session": active.index[-1].date().isoformat(), "close": row.market_close.to_pydatetime()}
    if not completed.empty:
        row = completed.iloc[-1]
        return {"status": "closed", "session": completed.index[-1].date().isoformat(), "close": row.market_close.to_pydatetime()}
    raise RuntimeError("Could not determine a recent NYSE session")


def download_prices(tickers: list[str], attempts: int = 3) -> pd.DataFrame:
    """Download in chunks so a transient Yahoo failure does not lose the whole index."""
    frames: list[pd.DataFrame] = []
    for offset in range(0, len(tickers), 100):
        chunk = tickers[offset : offset + 100]
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                raw = yf.download(
                    chunk,
                    period="10d",
                    interval="1d",
                    auto_adjust=False,
                    progress=False,
                    threads=True,
                    group_by="column",
                    timeout=30,
                )
                close = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]].rename(columns={"Close": chunk[0]})
                if close.empty:
                    raise RuntimeError("empty response")
                frames.append(close)
                break
            except Exception as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    time.sleep(5 * (attempt + 1))
        else:
            print(f"Chunk failed ({chunk[0]}..{chunk[-1]}): {last_error}")
    if not frames:
        raise RuntimeError("Yahoo returned no usable price data")
    return pd.concat(frames, axis=1).loc[:, ~pd.concat(frames, axis=1).columns.duplicated()]


def calculate(close: pd.DataFrame, session: str) -> dict[str, Any]:
    session_date = pd.Timestamp(session).date()
    up = down = flat = missing = 0
    examples: list[dict[str, Any]] = []

    for ticker in close.columns:
        series = close[ticker].dropna()
        dated = [(pd.Timestamp(idx).date(), float(value)) for idx, value in series.items() if math.isfinite(float(value))]
        current = next(((d, v) for d, v in reversed(dated) if d <= session_date), None)
        previous = next(((d, v) for d, v in reversed(dated) if d < session_date), None)
        if current is None or previous is None or previous[1] == 0:
            missing += 1
            continue
        change = (current[1] / previous[1] - 1) * 100
        if change > 0.000001:
            up += 1
        elif change < -0.000001:
            down += 1
        else:
            flat += 1
        if len(examples) < 8:
            examples.append({"ticker": ticker, "price": round(current[1], 4), "change_pct": round(change, 3)})

    valid = up + down + flat
    if valid < 450:
        raise RuntimeError(f"Only {valid} constituents had usable data; refusing to publish")
    return {
        "up": up,
        "down": down,
        "flat": flat,
        "missing": missing,
        "total": valid,
        "up_pct": round(up / valid * 100, 2),
        "down_pct": round(down / valid * 100, 2),
        "examples": examples,
    }


def upsert(records: list[dict[str, Any]], item: dict[str, Any], key: str) -> list[dict[str, Any]]:
    records = [row for row in records if row.get(key) != item.get(key)]
    records.append(item)
    return sorted(records, key=lambda row: row.get(key, ""))


def main() -> None:
    now = datetime.now(UTC)
    context = market_context(now)
    tickers = get_constituents()
    close = download_prices(tickers)
    result = calculate(close, context["session"])
    point = {
        "session": context["session"],
        "status": context["status"],
        "generated_at": now.isoformat(timespec="seconds"),
        **result,
    }

    history_path = DATA_DIR / "history.json"
    intraday_path = DATA_DIR / "intraday.json"
    history = read_json(history_path, [])
    intraday = read_json(intraday_path, [])

    if context["status"] == "intraday":
        minute_key = now.strftime("%Y-%m-%dT%H:%M")
        point["minute"] = minute_key
        intraday = [row for row in intraday if row.get("session") == context["session"]]
        intraday = upsert(intraday, point, "minute")[-40:]
        atomic_json(intraday_path, intraday)
    else:
        history = upsert(history, point, "session")[-750:]
        atomic_json(history_path, history)
        atomic_json(intraday_path, [])

    atomic_json(DATA_DIR / "latest.json", point)
    print(json.dumps(point, ensure_ascii=False))


if __name__ == "__main__":
    main()

