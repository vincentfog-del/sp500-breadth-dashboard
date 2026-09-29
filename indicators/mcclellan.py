"""
McClellan 廣度動能指標（比率調整版）：S&P 500 / NYSE / Nasdaq
------------------------------------------------
每日淨上漲比  RANA = (上漲 − 下跌) ÷ (上漲 + 下跌) × 1000
震盪指標      Osc  = RANA 的 10% 指數平均（約 19 日）− 5% 指數平均（約 39 日）
總和指標      Sum  = 震盪指標逐日累加
  數學上 Sum = 19 × EMA5% − 9 × EMA10%（EMA 由 0 起算時），
  所以只要資料夠長（暖機 150 天以上），數值就與起算日無關，可以跟 StockCharts 的 $NYSI / $NASI 比對。

資料：
  S&P 500：沿用 docs/data/history.json（sp500_updown.py 產生的每日漲跌家數）
  NYSE / Nasdaq：Nasdaq Trader 官方上市清單 → Yahoo Finance 收盤價 → 每日漲跌家數
                 快取在 docs/data/ad_counts.json；第一次抓 3 年，之後每天只補近一個月
  注意：歷史回補只包含「目前仍上市」的股票（存活者偏差），早期數字會略偏多頭；
        每天持續更新後，新資料就是完整的。

輸出：docs/data/mcclellan.json
"""

from __future__ import annotations

import datetime as dt
import io
import json
import os
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "docs" / "data"
OUT = DATA / "mcclellan.json"
COUNTS = DATA / "ad_counts.json"
A_FAST, A_SLOW = 0.10, 0.05
WARMUP = 150          # 暖機天數：之前的數值不輸出
SHOW_DAYS = 520       # 輸出最近幾個交易日
CHUNK = 250


def gh(kind, msg):
    if os.environ.get("GITHUB_ACTIONS"):
        msg = str(msg).replace("%", "%25").replace("\r", "").replace("\n", "%0A")
        print(f"::{kind} title=McClellan::{msg}", flush=True)
    else:
        print(f"[{kind}] {msg}")


# ---------- 計算（純函式） ----------

def mcclellan(counts: pd.DataFrame) -> pd.DataFrame:
    """counts：index = 日期（排序）, 欄位 up, down → 加上 rana, osc, sum。"""
    df = counts.sort_index().copy()
    tot = (df["up"] + df["down"]).where(lambda s: s > 0)
    df["rana"] = (df["up"] - df["down"]) / tot * 1000
    df = df.dropna(subset=["rana"])
    ef = es = s = 0.0
    osc, summ = [], []
    for x in df["rana"]:
        ef += A_FAST * (x - ef)
        es += A_SLOW * (x - es)
        o = ef - es
        s += o
        osc.append(o)
        summ.append(s)
    df["osc"], df["sum"] = osc, summ
    return df


def to_series(df: pd.DataFrame) -> list[dict]:
    df = df.iloc[WARMUP:].tail(SHOW_DAYS)
    r = lambda v: round(float(v), 2)
    return [{"d": d, "up": int(x.up), "down": int(x.down), "rana": r(x.rana), "osc": r(x.osc), "sum": r(x["sum"])}
            for d, x in df.iterrows()]


# ---------- S&P 500 ----------

def sp500_counts() -> pd.DataFrame:
    h = json.loads((DATA / "history.json").read_text(encoding="utf-8"))
    df = pd.DataFrame(h)[["session", "up", "down"]].drop_duplicates("session", keep="last")
    return df.set_index("session").sort_index()


# ---------- NYSE / Nasdaq ----------

SYMDIR = "https://www.nasdaqtrader.com/dynamic/SymDir/{}.txt"


def read_symdir(name: str) -> pd.DataFrame:
    import requests
    txt = requests.get(SYMDIR.format(name), timeout=60, headers={"User-Agent": "Mozilla/5.0 market-breadth-dashboard"}).text
    lines = [l for l in txt.splitlines() if l and not l.startswith("File Creation Time")]
    return pd.read_csv(io.StringIO("\n".join(lines)), sep="|", dtype=str).fillna("")


def yahoo_symbol(sym: str) -> str | None:
    s = sym.strip().upper()
    if not s or any(c in s for c in "$^=~ "):
        return None
    s = s.replace(".PR", "-P").replace(".WS", "-WT").replace(".", "-")
    return s


def universes() -> dict[str, list[str]]:
    nq = read_symdir("nasdaqlisted")
    nq = nq[(nq["Test Issue"] == "N") & (nq["ETF"] == "N")]
    ot = read_symdir("otherlisted")
    ot = ot[(ot["Exchange"] == "N") & (ot["Test Issue"] == "N") & (ot["ETF"] == "N")]
    out = {
        "nasdaq": sorted({y for y in map(yahoo_symbol, nq["Symbol"]) if y}),
        "nyse": sorted({y for y in map(yahoo_symbol, ot["CQS Symbol"]) if y}),
    }
    return out


def download(tickers: list[str], period: str) -> pd.DataFrame:
    import yfinance as yf
    frames = []
    for i in range(0, len(tickers), CHUNK):
        part = tickers[i:i + CHUNK]
        for attempt in range(3):
            try:
                df = yf.download(part, period=period, auto_adjust=True, progress=False, threads=True)["Close"]
                if isinstance(df, pd.Series):
                    df = df.to_frame(part[0])
                frames.append(df)
                break
            except Exception as e:
                if attempt == 2:
                    print(f"chunk {i} 失敗：{e}")
                time.sleep(10 * (attempt + 1))
        time.sleep(1.5)
    if not frames:
        raise RuntimeError("價格完全下載失敗")
    close = pd.concat(frames, axis=1)
    close = close.loc[:, ~close.columns.duplicated()]
    close.index = pd.to_datetime(close.index).tz_localize(None).normalize()
    return close.sort_index()


def count_updown(close: pd.DataFrame) -> pd.DataFrame:
    prev = close.shift(1)
    valid = close.notna() & prev.notna()
    up = ((close > prev) & valid).sum(axis=1)
    down = ((close < prev) & valid).sum(axis=1)
    n = valid.sum(axis=1)
    df = pd.DataFrame({"up": up, "down": down, "flat": n - up - down, "n": n})
    df = df[df["n"] > 0]
    df.index = df.index.strftime("%Y-%m-%d")
    return df


def update_counts(cache: dict, key: str, tickers: list[str]) -> pd.DataFrame:
    have = cache.get(key, {})
    period = "1mo" if len(have) >= 300 else "3y"
    print(f"{key}: {len(tickers)} 檔，下載 {period} ...")
    close = download(tickers, period)
    got = int(close.notna().any().sum())
    gh("notice", f"{key}: 清單 {len(tickers)} 檔，抓到價格 {got} 檔，期間 {period}")
    if got < len(tickers) * 0.6:
        raise RuntimeError(f"{key} 只抓到 {got}/{len(tickers)} 檔價格，太少，這次不更新")
    new = count_updown(close)
    # 最近一天若是盤中（收盤前執行），不寫入
    new = new.iloc[1:]  # 第一天沒有前一日可比
    for d, x in new.iterrows():
        have[d] = [int(x.up), int(x.down), int(x.flat), int(x.n)]
    cache[key] = dict(sorted(have.items()))
    df = pd.DataFrame.from_dict(cache[key], orient="index", columns=["up", "down", "flat", "n"])
    return df.sort_index()


def main():
    now = dt.datetime.now(ZoneInfo("Asia/Taipei"))
    result = {"updated_tw": now.strftime("%Y-%m-%d %H:%M"), "markets": {}}

    sp = mcclellan(sp500_counts())
    result["markets"]["sp500"] = {"name": "S&P 500", "universe": "S&P 500 成分股", "series": to_series(sp)}

    cache = json.loads(COUNTS.read_text(encoding="utf-8")) if COUNTS.exists() else {}
    try:
        uni = universes()
    except Exception as e:
        uni = None
        gh("warning", f"上市清單下載失敗，NYSE/Nasdaq 這次沿用舊資料：{e}")
    for key, name in (("nyse", "NYSE"), ("nasdaq", "Nasdaq")):
        try:
            df = update_counts(cache, key, uni[key]) if uni else \
                pd.DataFrame.from_dict(cache.get(key, {}), orient="index", columns=["up", "down", "flat", "n"]).sort_index()
            if len(df) <= WARMUP:
                raise RuntimeError(f"{name} 歷史只有 {len(df)} 天，不足以暖機")
            m = mcclellan(df)
            result["markets"][key] = {"name": name, "universe": f"{name} 上市股票（不含 ETF）",
                                      "series": to_series(m)}
            last = m.iloc[-1]
            gh("notice", f"{name} {m.index[-1]}: 漲 {int(last.up)} 跌 {int(last.down)} "
                         f"RANA {last.rana:.1f} Osc {last.osc:.1f} Sum {last['sum']:.2f}；"
                         + "；".join(f"{d}: {m.loc[d, 'sum']:.2f}" for d in m.index[-4:-1]))
        except Exception as e:
            gh("error", f"{name} 失敗：{type(e).__name__}: {e}")

    COUNTS.parent.mkdir(parents=True, exist_ok=True)
    COUNTS.write_text(json.dumps(cache, separators=(",", ":")), encoding="utf-8")
    OUT.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    s = result["markets"]["sp500"]["series"][-1]
    print(f"完成 → {OUT}；S&P 500 {s['d']} Sum {s['sum']}")
    if len(result["markets"]) < 3:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
