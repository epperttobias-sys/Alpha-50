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
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import numpy as np
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
            "s": (row.get("Sector") or "").strip(),
        })
    if len(out) < 500:
        raise RuntimeError(f"zu wenige Titel geladen: {len(out)}")
    return out


CUR = {"": "USD", ".AS": "EUR", ".PA": "EUR", ".DE": "EUR", ".SW": "CHF", ".T": "JPY", ".L": "GBP", ".TO": "CAD"}


def fallback_holdings():
    return [{"y": t + suf, "t": f"{tv}:{t.replace('-', '.')}", "n": n, "c": c, "w": float("nan"),
             "ref": float("nan"), "cur": CUR.get(suf, "USD"), "pence": suf == ".L"}
            for t, n, c, suf, tv in FALLBACK]


def _wma(s, n):
    w = np.arange(1, n + 1, dtype=float)
    return s.rolling(n).apply(lambda x: float(np.dot(x, w) / w.sum()), raw=True)


def rating(df):
    """Technisches Gesamtsignal auf Tagesbasis nach der Methode von TradingView:
    Mittel aus gleitenden Durchschnitten und Oszillatoren, je Signal +1 / 0 / -1.
    > 0.5 = starker Kauf, < -0.5 = starker Verkauf."""
    try:
        c = df["Close"].astype(float)
        hi = df["High"].astype(float).fillna(c)
        lo = df["Low"].astype(float).fillna(c)
        v = df["Volume"].astype(float).fillna(0)
        if len(c) < 60:
            return None
        p = c.iloc[-1]
        sg = lambda b, s: 1 if b else (-1 if s else 0)

        ma = []
        for n in (10, 20, 30, 50, 100, 200):
            if len(c) >= n:
                s = c.rolling(n).mean().iloc[-1]
                e = c.ewm(span=n, adjust=False).mean().iloc[-1]
                ma += [sg(p > s, p < s), sg(p > e, p < e)]
        if v.tail(20).sum() > 0:
            vw = ((c * v).rolling(20).sum() / v.rolling(20).sum()).iloc[-1]
            ma.append(sg(p > vw, p < vw))
        hma = _wma(2 * _wma(c, 4) - _wma(c, 9), 3).iloc[-1]
        ma.append(sg(p > hma, p < hma))
        conv = (hi.rolling(9).max() + lo.rolling(9).min()) / 2
        base = (hi.rolling(26).max() + lo.rolling(26).min()) / 2
        lead1 = ((conv + base) / 2).iloc[-1]
        lead2 = ((hi.rolling(52).max() + lo.rolling(52).min()) / 2).iloc[-1]
        ma.append(sg(base.iloc[-1] > lead1 > lead2 and conv.iloc[-1] > base.iloc[-1] and p > lead1,
                     base.iloc[-1] < lead1 < lead2 and conv.iloc[-1] < base.iloc[-1] and p < lead1))

        osc = []
        d = c.diff()
        up = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
        dn = (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
        rsi = 100 - 100 / (1 + up / dn.replace(0, np.nan))
        r0, r1 = rsi.iloc[-1], rsi.iloc[-2]
        osc.append(sg(r0 < 30 and r0 > r1, r0 > 70 and r0 < r1))
        ll, hh = lo.rolling(14).min(), hi.rolling(14).max()
        k = (100 * (c - ll) / (hh - ll).replace(0, np.nan)).rolling(3).mean()
        dd = k.rolling(3).mean()
        osc.append(sg(k.iloc[-1] < 20 and dd.iloc[-1] < 20 and k.iloc[-1] > dd.iloc[-1],
                      k.iloc[-1] > 80 and dd.iloc[-1] > 80 and k.iloc[-1] < dd.iloc[-1]))
        tp = (hi + lo + c) / 3
        md = tp.rolling(20).apply(lambda x: float(np.mean(np.abs(x - x.mean()))), raw=True)
        cci = (tp - tp.rolling(20).mean()) / (0.015 * md.replace(0, np.nan))
        osc.append(sg(cci.iloc[-1] < -100 and cci.iloc[-1] > cci.iloc[-2],
                      cci.iloc[-1] > 100 and cci.iloc[-1] < cci.iloc[-2]))
        pdm = (hi.diff()).where((hi.diff() > -lo.diff()) & (hi.diff() > 0), 0.0)
        mdm = (-lo.diff()).where((-lo.diff() > hi.diff()) & (-lo.diff() > 0), 0.0)
        trr = np.maximum(hi - lo, np.maximum((hi - c.shift()).abs(), (lo - c.shift()).abs()))
        atr = trr.ewm(alpha=1 / 14, adjust=False).mean()
        pdi = 100 * pdm.ewm(alpha=1 / 14, adjust=False).mean() / atr
        mdi = 100 * mdm.ewm(alpha=1 / 14, adjust=False).mean() / atr
        adx = (100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)).ewm(alpha=1 / 14, adjust=False).mean()
        osc.append(sg(adx.iloc[-1] > 20 and adx.iloc[-1] > adx.iloc[-2] and pdi.iloc[-1] > mdi.iloc[-1],
                      adx.iloc[-1] > 20 and adx.iloc[-1] > adx.iloc[-2] and pdi.iloc[-1] < mdi.iloc[-1]))
        hl2 = (hi + lo) / 2
        ao = hl2.rolling(5).mean() - hl2.rolling(34).mean()
        osc.append(sg(ao.iloc[-1] > 0 and ao.iloc[-1] > ao.iloc[-2], ao.iloc[-1] < 0 and ao.iloc[-1] < ao.iloc[-2]))
        mom = c - c.shift(10)
        osc.append(sg(mom.iloc[-1] > mom.iloc[-2], mom.iloc[-1] < mom.iloc[-2]))
        macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
        sig = macd.ewm(span=9, adjust=False).mean()
        osc.append(sg(macd.iloc[-1] > sig.iloc[-1], macd.iloc[-1] < sig.iloc[-1]))
        wr = -100 * (hh - c) / (hh - ll).replace(0, np.nan)
        osc.append(sg(wr.iloc[-1] < -80 and wr.iloc[-1] > wr.iloc[-2], wr.iloc[-1] > -20 and wr.iloc[-1] < wr.iloc[-2]))
        e13 = c.ewm(span=13, adjust=False).mean()
        bull, bear = hi - e13, lo - e13
        rising = e13.iloc[-1] > e13.iloc[-2]
        osc.append(sg(rising and bear.iloc[-1] < 0 and bear.iloc[-1] > bear.iloc[-2],
                      (not rising) and bull.iloc[-1] > 0 and bull.iloc[-1] < bull.iloc[-2]))
        bp = c - np.minimum(lo, c.shift())
        trr2 = np.maximum(hi, c.shift()) - np.minimum(lo, c.shift())
        avg = lambda n: bp.rolling(n).sum() / trr2.rolling(n).sum().replace(0, np.nan)
        uo = (100 * (4 * avg(7) + 2 * avg(14) + avg(28)) / 7).iloc[-1]
        osc.append(sg(uo > 70, uo < 30))

        ma_r, osc_r = float(np.mean(ma)), float(np.mean(osc))
        val = (ma_r + osc_r) / 2
        return None if math.isnan(val) else round(val, 2)
    except Exception:
        return None


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
    data = yf.download(tickers, period="1y", interval="1d", progress=False,
                       group_by="ticker", threads=True, auto_adjust=True)
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
        tr = rating(df)
        items.append({
            "n": h["n"], "t": h["t"], "c": h["c"], "s": h.get("s", ""), "w": None if math.isnan(h["w"]) else round(h["w"], 3),
            "p": round(close * factor, 4), "cur": h["cur"], "chg": round((close / prev - 1) * 100, 2),
            "vol": int(vol), "val": round(vol * close * factor * fx), "date": str(df.index[-1].date()), "tr": tr,
            "_y": h["y"], "_fx": fx, "_f": factor,
        })

    # Marktkapitalisierung = Aktienanzahl × aktueller Kurs (in USD).
    # Die Aktienanzahl ändert sich selten: einmal täglich für alle Titel abfragen, sonst Cache.
    today = datetime.now(timezone.utc).date().isoformat()
    try:
        with open("shares.json", encoding="utf-8") as f:
            cache = json.load(f)
    except Exception:
        cache = {"date": "", "shares": {}}
    shares = cache.get("shares", {})
    if cache.get("date") != today:
        def get_shares(y):
            try:
                return y, float(getattr(yf.Ticker(y).fast_info, "shares", 0) or 0)
            except Exception:
                return y, 0.0
        with ThreadPoolExecutor(max_workers=12) as ex:
            for y, sh in ex.map(get_shares, [it["_y"] for it in items]):
                if sh > 0:
                    shares[y] = sh
        cache = {"date": today, "shares": shares}
        with open("shares.json", "w", encoding="utf-8") as f:
            json.dump(cache, f, separators=(",", ":"))
    for it in items:
        sh = shares.get(it["_y"])
        if sh:
            it["mc"] = round(sh * it["p"] * it["_fx"])

    def clean(lst):
        return [{k: v for k, v in x.items() if not k.startswith("_")} for x in lst]

    out = {
        "updated": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "source": source,
        "count": len(items),
        "note": note,
        "all": clean(sorted(items, key=lambda x: x["val"], reverse=True)),
        "lists": {
            # grösste Bewegungen unter den 100 meistgehandelten Titeln (Umsatz in USD)
            "active_gainers": clean(sorted([x for x in sorted(items, key=lambda x: x["val"], reverse=True)[:100]
                                            if x["chg"] > 0], key=lambda x: x["chg"], reverse=True)[:20]),
            "active_losers": clean(sorted([x for x in sorted(items, key=lambda x: x["val"], reverse=True)[:100]
                                           if x["chg"] < 0], key=lambda x: x["chg"])[:20]),
        },
    }
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"{len(items)} Titel verarbeitet ({source})")


if __name__ == "__main__":
    main()
