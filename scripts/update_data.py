"""Erzeugt data.json mit Ranglisten (Volumen, Marktkapitalisierung, Gewinner, Verlierer)
ausschliesslich aus den Bestandteilen des MSCI World.

Quelle der Bestandteile: Holdings des iShares MSCI World ETF (URTH).
Kursdaten: Yahoo Finance via yfinance.
"""
import csv
import io
import json
import math
import sys
from datetime import datetime, timezone

import requests
import yfinance as yf

HOLDINGS_URLS = [
    "https://www.ishares.com/us/products/239696/ishares-msci-world-etf/latest-holdings.csv",
    "https://www.ishares.com/us/products/239696/ishares-msci-world-etf/"
    "1467271812596.ajax?fileType=csv&fileName=URTH_holdings&dataType=fund",
]

# iShares-Börsenname (Teilstring, klein) -> (Yahoo-Suffix, TradingView-Präfix)
EXCHANGES = [
    ("copenhagen", ".CO", "OMXCOP"),
    ("stockholm", ".ST", "OMXSTO"),
    ("helsinki", ".HE", "OMXHEX"),
    ("nordic", None, None),                      # Land entscheidet (NORDIC)
    ("euronext amsterdam", ".AS", "EURONEXT"),
    ("euronext paris", ".PA", "EURONEXT"),
    ("euronext brussels", ".BR", "EURONEXT"),
    ("euronext lisbon", ".LS", "EURONEXT"),
    ("nasdaq", "", "NASDAQ"),
    ("nyse arca", "", "AMEX"),
    ("nyse", "", "NYSE"),
    ("new york stock exchange", "", "NYSE"),
    ("cboe", "", ""),
    ("tokyo", ".T", "TSE"),
    ("london", ".L", "LSE"),
    ("xetra", ".DE", "XETR"),
    ("six swiss", ".SW", "SIX"),
    ("toronto", ".TO", "TSX"),
    ("asx", ".AX", "ASX"),
    ("borsa italiana", ".MI", "MIL"),
    ("madrid", ".MC", "BME"),
    ("oslo", ".OL", "OSL"),
    ("hong kong", ".HK", "HKEX"),
    ("singapore", ".SI", "SGX"),
    ("irish", ".IR", ""),
    ("wiener", ".VI", "VIE"),
    ("tel aviv", ".TA", "TASE"),
    ("new zealand", ".NZ", "NZX"),
]
NORDIC = {"Sweden": (".ST", "OMXSTO"), "Denmark": (".CO", "OMXCOP"), "Finland": (".HE", "OMXHEX")}
US_FIX = {"BRKB": "BRK-B", "BFB": "BF-B", "BFA": "BF-A", "LENB": "LEN-B", "HEIA": "HEI-A"}

FALLBACK = [  # falls die iShares-Liste nicht geladen werden kann: grösste Titel
    ("AAPL", "Apple", "United States", "", "NASDAQ"), ("MSFT", "Microsoft", "United States", "", "NASDAQ"),
    ("NVDA", "Nvidia", "United States", "", "NASDAQ"), ("AMZN", "Amazon", "United States", "", "NASDAQ"),
    ("GOOGL", "Alphabet A", "United States", "", "NASDAQ"), ("META", "Meta", "United States", "", "NASDAQ"),
    ("AVGO", "Broadcom", "United States", "", "NASDAQ"), ("TSLA", "Tesla", "United States", "", "NASDAQ"),
    ("BRK-B", "Berkshire Hathaway B", "United States", "", "NYSE"), ("JPM", "JPMorgan", "United States", "", "NYSE"),
    ("LLY", "Eli Lilly", "United States", "", "NYSE"), ("V", "Visa", "United States", "", "NYSE"),
    ("ASML", "ASML", "Netherlands", ".AS", "EURONEXT"), ("NESN", "Nestlé", "Switzerland", ".SW", "SIX"),
    ("NOVN", "Novartis", "Switzerland", ".SW", "SIX"), ("ROG", "Roche", "Switzerland", ".SW", "SIX"),
    ("SAP", "SAP", "Germany", ".DE", "XETR"), ("7203", "Toyota", "Japan", ".T", "TSE"),
    ("SHEL", "Shell", "United Kingdom", ".L", "LSE"), ("AZN", "AstraZeneca", "United Kingdom", ".L", "LSE"),
    ("MC", "LVMH", "France", ".PA", "EURONEXT"), ("RY", "Royal Bank of Canada", "Canada", ".TO", "TSX"),
]


def num(s):
    try:
        return float(str(s).replace(",", ""))
    except ValueError:
        return float("nan")


def map_symbol(ticker, exchange, location):
    ex = exchange.lower()
    for key, suf, tv in EXCHANGES:
        if key in ex:
            if suf is None:
                if location not in NORDIC:
                    return None
                suf, tv = NORDIC[location]
            break
    else:
        return None
    t = ticker.strip()
    if suf == "":
        y = US_FIX.get(t, t.replace(".", "-").replace(" ", "-"))
        tvs = (tv + ":" if tv else "") + y.replace("-", ".")
    elif suf == ".L":
        t = t.rstrip(".")
        y = t.replace(".", "-").replace(" ", "-") + suf
        tvs = tv + ":" + t.replace(".", "")
    elif suf == ".HK":
        y = t.zfill(4) + suf
        tvs = tv + ":" + t.lstrip("0")
    elif suf in (".TO",):
        y = t.replace(".", "-").replace(" ", "-") + suf
        tvs = tv + ":" + t.replace(" ", ".")
    else:
        y = t.replace(" ", "-").replace(".", "-") + suf
        tvs = (tv + ":" if tv else "") + t.replace(" ", "_").replace(".", "_")
    return y, tvs


def fetch_holdings_text():
    errors = []
    for url in HOLDINGS_URLS:
        try:
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                                           "Accept": "text/csv,text/plain,*/*"}, timeout=60)
            r.raise_for_status()
            text = r.content.decode("utf-8-sig", errors="replace")
            if "\nTicker," in text or text.startswith("Ticker,"):
                return text
            errors.append(f"{url}: kein CSV")
        except Exception as e:
            errors.append(f"{url}: {e}")
    raise RuntimeError(" | ".join(errors))


def load_holdings():
    text = fetch_holdings_text()
    lines = text.splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith("Ticker,"))
    rows = list(csv.DictReader(io.StringIO("\n".join(lines[start:]))))
    out = []
    for row in rows:
        if (row.get("Asset Class") or "").strip() != "Equity":
            continue
        m = map_symbol(row["Ticker"], row.get("Exchange", ""), row.get("Location", ""))
        if not m:
            continue
        out.append({
            "y": m[0], "t": m[1], "n": row["Name"].strip().title(), "c": row.get("Location", "").strip(),
            "w": num(row.get("Weight (%)")), "ref": num(row.get("Price")),
            "cur": (row.get("Market Currency") or row.get("Currency") or "USD").strip(),
        })
    if len(out) < 500:
        raise RuntimeError(f"zu wenige Titel geladen: {len(out)}")
    return out


CUR = {"": "USD", ".AS": "EUR", ".PA": "EUR", ".DE": "EUR", ".SW": "CHF", ".T": "JPY", ".L": "GBP", ".TO": "CAD"}


def fallback_holdings():
    return [{"y": t + suf, "t": f"{tv}:{t.replace('-', '.')}", "n": n, "c": c, "w": float("nan"),
             "ref": float("nan"), "cur": CUR.get(suf, "USD"), "pence": suf == ".L"}
            for t, n, c, suf, tv in FALLBACK]


def fx_rates(currencies):
    rates = {"USD": 1.0}
    need = sorted({c for c in currencies if c and c != "USD"})
    if not need:
        return rates
    pairs = [f"{c}USD=X" for c in need]
    data = yf.download(pairs, period="5d", interval="1d", progress=False, group_by="ticker", threads=True)
    for c, p in zip(need, pairs):
        try:
            s = data[p]["Close"].dropna() if len(pairs) > 1 else data["Close"].dropna()
            rates[c] = float(s.iloc[-1])
        except Exception:
            pass
    return rates


def main():
    source = "iShares MSCI World ETF (URTH)"
    note = ""
    try:
        hold = load_holdings()
    except Exception as e:
        note = str(e)[:500]
        print("Holdings nicht geladen, nutze Ersatzliste:", e, file=sys.stderr)
        hold = fallback_holdings()
        source = "Ersatzliste (grösste MSCI-World-Titel)"

    tickers = [h["y"] for h in hold]
    data = yf.download(tickers, period="5d", interval="1d", progress=False,
                       group_by="ticker", threads=True, auto_adjust=False)
    rates = fx_rates([h["cur"] for h in hold])

    items = []
    for h in hold:
        try:
            df = data[h["y"]].dropna(subset=["Close"])
        except Exception:
            continue
        if len(df) < 2:
            continue
        close, prev = float(df["Close"].iloc[-1]), float(df["Close"].iloc[-2])
        vol = float(df["Volume"].iloc[-1] or 0)
        if not (close > 0 and prev > 0):
            continue
        fx = rates.get(h["cur"])
        if fx is None:
            continue
        factor = 0.01 if h.get("pence") else 1.0
        if h["cur"] in ("GBP", "ILS") and h["ref"] == h["ref"] and h["ref"] > 0:
            # Yahoo notiert London in Pence und Tel Aviv in Agorot
            if 50 < close * fx / h["ref"] < 200 or 50 < close / h["ref"] < 200:
                factor = 0.01
        items.append({
            "n": h["n"], "t": h["t"], "c": h["c"], "w": None if math.isnan(h["w"]) else round(h["w"], 3),
            "p": round(close * factor, 4), "cur": h["cur"], "chg": round((close / prev - 1) * 100, 2),
            "vol": int(vol), "val": round(vol * close * factor * fx), "date": str(df.index[-1].date()),
            "_y": h["y"], "_fx": fx, "_f": factor,
        })

    # Marktkapitalisierung für die nach Index-Gewicht grössten Titel
    by_w = sorted(items, key=lambda x: (x["w"] or 0), reverse=True)[:80]
    for it in by_w:
        try:
            fi = yf.Ticker(it["_y"]).fast_info
            mc = float(getattr(fi, "market_cap", 0) or 0)
            cur = str(getattr(fi, "currency", "") or "")
            f = 0.01 if cur in ("GBp", "ILA", "ZAc") else 1.0
            if mc > 0:
                it["mc"] = round(mc * f * it["_fx"])
        except Exception:
            pass

    def clean(lst):
        return [{k: v for k, v in x.items() if not k.startswith("_")} for x in lst]

    out = {
        "updated": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "source": source,
        "count": len(items),
        "note": note,
        "lists": {
            "volume_leaders": clean(sorted(items, key=lambda x: x["val"], reverse=True)[:50]),
            "most_capitalized": clean(sorted([x for x in items if x.get("mc")], key=lambda x: x["mc"], reverse=True)[:50]),
            "top_gainers": clean(sorted(items, key=lambda x: x["chg"], reverse=True)[:30]),
            "top_losers": clean(sorted(items, key=lambda x: x["chg"])[:30]),
            # grösste Bewegungen unter den 100 meistgehandelten Titeln (Umsatz in USD)
            "active_gainers": clean(sorted([x for x in sorted(items, key=lambda x: x["val"], reverse=True)[:100]
                                            if x["chg"] > 0], key=lambda x: x["chg"], reverse=True)[:10]),
            "active_losers": clean(sorted([x for x in sorted(items, key=lambda x: x["val"], reverse=True)[:100]
                                           if x["chg"] < 0], key=lambda x: x["chg"])[:10]),
        },
    }
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"{len(items)} Titel verarbeitet ({source})")


if __name__ == "__main__":
    main()
