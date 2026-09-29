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
CHUNK = 200


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
    """Nasdaq Trader 的 ACT 代號 → Yahoo 代號。特別股 ABR$D → ABR-PD、權證 X.WS → X-WT、B 股 BRK.B → BRK-B。"""
    s = sym.strip().upper()
    if not s or any(c in s for c in "^=~# "):
        return None
    s = s.replace("$", "-P").replace(".WS", "-WT").replace(".U", "-U").replace(".", "-")
    return s


def classify(name: str, act: str, etf: str) -> str:
    n = name.lower()
    if etf == "Y":
        return "etf"
    if "warrant" in n:
        return "warrant"
    if " right" in n:
        return "right"
    if " unit" in n:
        return "unit"
    if "$" in act or "preferred" in n or "depositary share" in n:
        return "preferred"
    return "common"


# 要比對的幾種範圍（哪些證券算進漲跌家數）；PREFERRED 為頁面採用的版本
VARIANTS = {
    "common": {"common"},
    "common_pref": {"common", "preferred"},
    "no_etf": {"common", "preferred", "warrant", "right", "unit"},
    "all": {"common", "preferred", "warrant", "right", "unit", "etf"},
}
# 與 StockCharts 比對（2026/9/28）：NYSE 普通股 −618 vs $NYSI −614；Nasdaq 含 ETF −536 vs $NASI −517
PREFERRED = {"nyse": "common", "nasdaq": "all"}


def universes() -> dict[str, dict[str, str]]:
    """回傳 {市場: {yahoo 代號: 類別}}。"""
    nq = read_symdir("nasdaqlisted")
    nq = nq[nq["Test Issue"] == "N"]
    ot = read_symdir("otherlisted")
    ot = ot[(ot["Exchange"] == "N") & (ot["Test Issue"] == "N")]
    out = {"nasdaq": {}, "nyse": {}}
    for r in nq.itertuples():
        y = yahoo_symbol(r.Symbol)
        if y:
            out["nasdaq"][y] = classify(r._2, r.Symbol, r.ETF)
    for r in ot.itertuples():
        y = yahoo_symbol(r._1)
        if y:
            out["nyse"][y] = classify(r._2, r._1, r.ETF)
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


def update_counts(cache: dict, key: str, uni: dict[str, str]) -> dict[str, pd.DataFrame]:
    """下載一次價格，同時算出各種範圍的每日漲跌家數；回傳 {範圍: DataFrame}。"""
    mk = cache.setdefault(key, {})
    if mk and not isinstance(next(iter(mk.values())), dict) or any(v not in mk for v in VARIANTS):
        mk.clear()                               # 舊格式或缺範圍 → 重抓完整歷史
    have_days = min((len(mk.get(v, {})) for v in VARIANTS), default=0)
    period = "1mo" if have_days >= 300 else "3y"
    tickers = sorted(uni)
    print(f"{key}: {len(tickers)} 檔，下載 {period} ...")
    close = download(tickers, period)
    # Yahoo 偶爾會暫時限制下載頻率，抓不到的等一下再補抓（最多兩輪）
    for rnd in (1, 2):
        miss = [t for t in tickers if t not in close.columns or close[t].isna().all()]
        if len(miss) < max(20, len(tickers) * 0.03):
            break
        print(f"{key}: 第 {rnd} 輪補抓 {len(miss)} 檔 ...")
        time.sleep(45 * rnd)
        again = download(miss, period)
        keep = [c for c in again.columns if again[c].notna().any()]
        close = close.drop(columns=[c for c in keep if c in close.columns]).join(again[keep], how="outer")
    ok = set(close.columns[close.notna().any()])
    by_cat = {}
    for t, c in uni.items():
        by_cat.setdefault(c, [0, 0]); by_cat[c][0] += 1; by_cat[c][1] += t in ok
    gh("notice", f"{key}: 期間 {period}；各類 清單/抓到：" + "，".join(f"{c} {n}/{g}" for c, (n, g) in sorted(by_cat.items())))
    common_n, common_ok = by_cat.get("common", [0, 0])
    if common_ok < common_n * 0.6:
        miss = [t for t, c in uni.items() if c == "common" and t not in ok][:12]
        raise RuntimeError(f"{key} 普通股只抓到 {common_ok}/{common_n} 檔，這次不更新；例如缺 {miss}")
    out = {}
    for v, cats in VARIANTS.items():
        cols = [t for t, c in uni.items() if c in cats and t in ok]
        new = count_updown(close[cols]).iloc[1:]     # 第一天沒有前一日可比
        have = mk.setdefault(v, {})
        for d, x in new.iterrows():
            have[d] = [int(x.up), int(x.down), int(x.flat), int(x.n)]
        mk[v] = dict(sorted(have.items()))
        out[v] = pd.DataFrame.from_dict(mk[v], orient="index", columns=["up", "down", "flat", "n"]).sort_index()
    return out


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
            if uni:
                variants = update_counts(cache, key, uni[key])
            else:
                variants = {v: pd.DataFrame.from_dict(d, orient="index", columns=["up", "down", "flat", "n"]).sort_index()
                            for v, d in cache.get(key, {}).items() if isinstance(d, dict)}
            df = variants[PREFERRED[key]]
            if len(df) <= WARMUP:
                raise RuntimeError(f"{name} 歷史只有 {len(df)} 天，不足以暖機")
            m = mcclellan(df)
            result["markets"][key] = {"name": name, "universe": {"common": f"{name} 上市普通股", "all": f"{name} 全部上市證券（含 ETF）"}.get(PREFERRED[key], f"{name} 上市證券"), "series": to_series(m)}
            # 各範圍比對：列出最近幾天的總和指標，方便和 StockCharts 對照
            cmp = []
            for v, vdf in variants.items():
                mm = mcclellan(vdf)
                cmp.append(f"{v}: " + " ".join(f"{d[5:]}={mm.loc[d, 'sum']:.0f}" for d in mm.index[-3:]))
            gh("notice", f"{name} 總和指標（各範圍）｜" + "｜".join(cmp))
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
