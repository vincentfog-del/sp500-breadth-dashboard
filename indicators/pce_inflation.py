"""
美國 PCE 通膨儀表板資料
------------------------------------------------
產出三個指標（每月，BEA 公布 PCE 當天更新）：
  1. PCE 年增率（聯準會 2% 目標所用的指標）
  2. 核心 PCE 年增率（排除食品與能源）
  3. PCE 細項中「年增率 > 3%」的比例
     —— Warsh 在 2026 年 Jackson Hole 演講中引用的廣度指標
        （他提到約 54%，疫情前約 32%）

資料來源：BEA API（NIUnderlyingDetail 資料集）
  U20404 = Table 2.4.4U 價格指數（各細項）
  U20405 = Table 2.4.5U 名目支出金額（各細項，用來判斷階層與加權）

怎麼找出「最底層細項」：
  BEA API 不提供表格的縮排階層，所以用名目金額的加總關係反推：
  一個項目的名目金額 = 它下面各子項目的加總（「Less:」開頭的項目為減項）。
  用多個月份同時檢查，避免數字剛好湊到一樣而誤判。
  算不出一棵完整的樹時會直接報錯，不會寫出錯的數字。

用法：
  需要環境變數 BEA_API_KEY（到 https://apps.bea.gov/API/signup/ 免費申請）
  Windows： set BEA_API_KEY=你的金鑰
  Mac/Linux： export BEA_API_KEY=你的金鑰
  python indicators/pce_inflation.py

輸出：data/pce_inflation.json
"""

import datetime as dt
import json
import os
import re
import sys
import time
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "data" / "pce_inflation.json"
API = "https://apps.bea.gov/api/data"
FIRST_YEAR = 2016          # 疫情前基準（2018–2019）需要前一年的資料
BASELINE = ("2018-01", "2019-12")
THRESHOLD = 3.0            # 年增率門檻（%）
SERIES_FROM = "2019-01"    # 儀表板時間序列起點


# ---------- 抓資料 ----------

def fetch_table(table, years, key):
    """回傳 DataFrame：line, desc, period(YYYY-MM), value。一年一次請求，避免單次回傳太大。"""
    import requests
    rows = []
    for y in years:
        params = {"UserID": key, "method": "GetData", "DataSetName": "NIUnderlyingDetail",
                  "TableName": table, "Frequency": "M", "Year": str(y), "ResultFormat": "json"}
        for attempt in range(3):
            try:
                r = requests.get(API, params=params, timeout=90)
                r.raise_for_status()
                js = r.json()
                break
            except Exception as e:
                if attempt == 2:
                    raise RuntimeError(f"{table} {y} 下載失敗：{e}") from None
                time.sleep(3 * (attempt + 1))
        data = extract_rows(js, table, y)
        if data is None:          # 該年份還沒有資料（例如年初）
            continue
        rows.extend(data)
        time.sleep(0.7)           # BEA 限制每分鐘 100 次
    return to_frame(rows)


def extract_rows(js, table, year):
    api = js.get("BEAAPI", {})
    res = api.get("Results", {})
    if isinstance(res, list):
        res = next((x for x in res if "Data" in x or "Error" in x), {})
    err = api.get("Error") or res.get("Error")
    if err:
        msg = err.get("APIErrorDescription", err) if isinstance(err, dict) else err
        if year == dt.date.today().year and "no data" in str(msg).lower():
            return None
        raise RuntimeError(f"BEA 回傳錯誤（{table} {year}）：{msg}")
    return res.get("Data", [])


def to_frame(rows):
    df = pd.DataFrame(rows)
    if df.empty:
        raise RuntimeError("BEA 沒有回傳任何資料")
    df = df.rename(columns={"LineNumber": "line", "LineDescription": "desc",
                            "TimePeriod": "period", "DataValue": "value"})
    df["line"] = df["line"].astype(int)
    df["desc"] = df["desc"].astype(str).str.strip()
    df["period"] = df["period"].str.replace("M", "-", regex=False)
    df["value"] = pd.to_numeric(df["value"].astype(str).str.replace(",", ""), errors="coerce")
    return df[["line", "desc", "period", "value"]]


# ---------- 階層判斷（純函式） ----------

def is_less(desc):
    return desc.lower().startswith("less:")


def build_tree(lines, values):
    """
    lines：依表格順序的 (line, desc) 串列
    values：同順序的 2D 陣列（每列 = 一個項目在幾個檢查月份的名目金額）
    回傳 root 節點 dict：{i, line, desc, sign, children}
    """
    n = len(lines)
    rel_tol = 0.0002   # 名目金額是可加總的，誤差只來自四捨五入，所以容忍度要很緊

    def tol(b, k):
        return max(rel_tol * abs(b), 0.5 * k + 1)

    def close(s, target, k):
        return all(abs(a - b) <= tol(b, k) for a, b in zip(s, target))

    @lru_cache(maxsize=None)
    def parse_node(i):
        """試著把第 i 行當成有子項目的父項；不成立就是最底層細項。回傳 (node, 下一行索引)。"""
        line, desc = lines[i]
        node = {"i": i, "line": line, "desc": desc, "sign": -1 if is_less(desc) else 1, "children": []}
        kids = parse_children(i + 1, tuple(values[i]))
        if kids:
            node["children"], nxt = kids
            return node, nxt
        return node, i + 1

    def parse_children(j, target):
        children, s, k = [], [0.0] * len(target), 0
        while j < len(lines) and k < 80:
            child, nxt = parse_node(j)
            sign = child["sign"]
            s = [a + sign * b for a, b in zip(s, values[j])]
            children.append(child)
            k += 1
            j = nxt
            if k >= 1 and close(s, target, k):
                # 單一子項剛好等於父項時（例如只有一個子項），也接受
                return children, j
            # 已超過目標且下一行不是減項 → 不可能再湊回來
            if j < n and not is_less(lines[j][1]) and any(b > 0 for b in target) and all(
                    a > b + tol(b, k) for a, b in zip(s, target) if b > 0):
                return None
        return None

    root, nxt = parse_node(0)
    if not root["children"]:
        raise RuntimeError("無法從名目金額推出細項階層（第 1 行沒有可加總的子項）")
    return root, nxt


def leaves(node, path_sign=1, under_npish=False):
    """回傳最底層細項（排除減項與非營利機構的毛產出/銷售收入這類非消費品項目）。"""
    npish = under_npish or "nonprofit institutions" in node["desc"].lower()
    sign = path_sign * node["sign"]
    if not node["children"]:
        return [] if (sign < 0 or npish) else [node]
    out = []
    for c in node["children"]:
        out.extend(leaves(c, sign, npish))
    return out


# ---------- 計算 ----------

def yoy(wide):
    """寬表（列 = 項目、欄 = YYYY-MM）→ 年增率（%）。"""
    cols = sorted(wide.columns)
    wide = wide[cols]
    return (wide / wide.shift(12, axis=1) - 1) * 100


def find_line(price, pattern, exclude=()):
    d = price[["line", "desc"]].drop_duplicates()
    hit = d[d["desc"].str.contains(pattern, case=False, regex=True)]
    for ex in exclude:
        hit = hit[~hit["desc"].str.contains(ex, case=False, regex=True)]
    if len(hit) == 0:
        raise RuntimeError(f"找不到符合「{pattern}」的項目")
    return int(hit.sort_values("line").iloc[0]["line"])


def compute(price, nominal):
    pw = price.pivot_table(index="line", columns="period", values="value")
    nw = nominal.pivot_table(index="line", columns="period", values="value")
    order = nominal[["line", "desc"]].drop_duplicates("line").sort_values("line")
    lines = list(order.itertuples(index=False, name=None))

    # 檢查月份：最新 3 個月 + 疫情前 1 個月（都要全表有值）
    full = [c for c in sorted(nw.columns) if nw[c].notna().all()]
    if len(full) < 4:
        raise RuntimeError("名目金額資料不足，無法判斷階層")
    checks = full[-3:] + [next((c for c in full if c >= "2019-06"), full[0])]
    vals = nw.loc[[l for l, _ in lines], checks].to_numpy().tolist()
    root, used = build_tree(lines, vals)
    leaf_nodes = leaves(root)
    leaf_lines = [x["line"] for x in leaf_nodes]
    names = {x["line"]: x["desc"] for x in leaf_nodes}

    # 兩張表的項目名稱要對得上
    pdesc = price.drop_duplicates("line").set_index("line")["desc"]
    bad = [l for l in leaf_lines if pdesc.get(l) != names[l]]
    if bad:
        raise RuntimeError(f"價格表與金額表的項目對不上，例如第 {bad[0]} 行")

    y = yoy(pw)
    head = y.loc[1]
    core_line = find_line(price, r"excluding food and energy", exclude=(r"goods", r"services", r"market-based"))
    core = y.loc[core_line]

    ly = y.loc[leaf_lines]
    nwl = nw.reindex(index=leaf_lines, columns=ly.columns)
    valid = ly.notna()
    above = (ly > THRESHOLD) & valid
    breadth = above.sum() / valid.sum().where(valid.sum() > 0) * 100
    w = nwl.where(valid)
    breadth_w = (w.where(above).sum() / w.sum().where(w.sum() > 0)) * 100

    months = [m for m in sorted(ly.columns) if m >= SERIES_FROM and pd.notna(head.get(m)) and pd.notna(breadth.get(m))]
    if not months:
        raise RuntimeError("算不出任何月份的年增率")
    latest = months[-1]
    base = [m for m in breadth.index if BASELINE[0] <= m <= BASELINE[1] and pd.notna(breadth[m])]

    r2 = lambda v: None if pd.isna(v) else round(float(v), 2)
    dist = sorted(
        ({"name": names[l], "yoy": r2(ly.at[l, latest]), "w": r2(nwl.at[l, latest] / nwl[latest].sum() * 100)}
         for l in leaf_lines if pd.notna(ly.at[l, latest])),
        key=lambda d: -d["yoy"])
    return {
        "latest_month": latest,
        "components": len(leaf_lines),
        "tree_lines_used": used,
        "core_line": core_line,
        "baseline": {"period": f"{BASELINE[0][:4]}–{BASELINE[1][:4]}",
                     "breadth": r2(breadth[base].mean()) if base else None,
                     "breadth_w": r2(breadth_w[base].mean()) if base else None},
        "series": [{"m": m, "pce": r2(head[m]), "core": r2(core[m]),
                    "b": r2(breadth[m]), "bw": r2(breadth_w[m]),
                    "n_above": int(above[m].sum()), "n": int(valid[m].sum())} for m in months],
        "dist": dist,
    }


def main():
    key = os.environ.get("BEA_API_KEY", "").strip()
    if not key:
        sys.exit("缺少環境變數 BEA_API_KEY（到 https://apps.bea.gov/API/signup/ 免費申請）")
    years = list(range(FIRST_YEAR, dt.date.today().year + 1))
    print("下載 BEA 價格指數（U20404）...")
    price = fetch_table("U20404", years, key)
    print("下載 BEA 名目支出（U20405）...")
    nominal = fetch_table("U20405", years, key)

    res = compute(price, nominal)
    n = res["components"]
    if not 150 <= n <= 260:
        print(f"⚠ 找到 {n} 個最底層細項，跟預期的約 200 個差很多，請檢查")
    payload = {
        "id": "pce_inflation",
        "name": "美國 PCE 通膨",
        "target": 2.0,
        "threshold": THRESHOLD,
        "source": "BEA NIPA Tables 2.4.4U / 2.4.5U（PCE 價格指數與支出，細項）",
        **res,
    }

    old = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else None
    same = old and all(old.get(k) == payload[k] for k in ("series", "dist", "baseline"))
    if same:
        print(f"資料沒有變化（最新 {res['latest_month']}），不更新")
        return
    payload["updated_tw"] = dt.datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d %H:%M")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    s = res["series"][-1]
    print(f"完成 → {OUT}\n{res['latest_month']}：PCE {s['pce']}%，核心 {s['core']}%，"
          f"細項 >3% 比例 {s['b']}%（{s['n_above']}/{s['n']}），"
          f"疫情前平均 {res['baseline']['breadth']}%")


if __name__ == "__main__":
    main()
