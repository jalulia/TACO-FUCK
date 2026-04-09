#!/usr/bin/env python3
"""
TACO FUCK Index — Hybrid Daily Updater
Pulls historical data from FRED + same-day data from Yahoo Finance.
Computes z-scores, builds composite index, injects into HTML.
"""

import csv, json, re, sys, os
from datetime import datetime, timedelta
from pathlib import Path

# ── CONFIG ──
REPO_DIR = Path("/Users/juliacompton/TACO-FUCK")
HTML_FILE = REPO_DIR / "index.html"
TMP_DIR = Path("/tmp/taco-fred")
DISPLAY_START = "2025-01-01"
WARMUP_START = "2024-06-01"
ROLLING_WINDOW = 60

def fetch_fred_csv(series_id, start, end):
    """Fetch CSV from FRED using curl (avoids Python SSL issues)."""
    import subprocess
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}&cosd={start}&coed={end}"
    out = TMP_DIR / f"{series_id}.csv"
    subprocess.run(["curl", "-s", "-o", str(out), url], check=True)
    data = {}
    with open(out) as f:
        reader = csv.reader(f)
        next(reader)
        for row in reader:
            if len(row) >= 2 and row[1].strip() not in ('', '.'):
                data[row[0]] = float(row[1])
    return data

def fetch_yahoo_today():
    """Fetch today's VIX, S&P 500, 10Y-2Y spread from Yahoo Finance."""
    import yfinance as yf
    
    today_str = datetime.now().strftime("%Y-%m-%d")
    result = {}
    
    # VIX
    try:
        vix = yf.Ticker("^VIX")
        hist = vix.history(period="5d")
        if not hist.empty:
            for date, row in hist.iterrows():
                d = date.strftime("%Y-%m-%d")
                result.setdefault(d, {})["vix"] = round(row["Close"], 2)
    except Exception as e:
        print(f"  Yahoo VIX error: {e}")
    
    # S&P 500
    try:
        sp = yf.Ticker("^GSPC")
        hist = sp.history(period="5d")
        if not hist.empty:
            for date, row in hist.iterrows():
                d = date.strftime("%Y-%m-%d")
                result.setdefault(d, {})["sp500"] = round(row["Close"], 2)
    except Exception as e:
        print(f"  Yahoo S&P error: {e}")
    
    # 10Y Treasury yield
    try:
        tnx = yf.Ticker("^TNX")
        hist = tnx.history(period="5d")
        if not hist.empty:
            for date, row in hist.iterrows():
                d = date.strftime("%Y-%m-%d")
                result.setdefault(d, {})["tnx_10y"] = round(row["Close"], 4)
    except Exception as e:
        print(f"  Yahoo 10Y error: {e}")
    
    # 2Y Treasury yield
    try:
        twoy = yf.Ticker("2YY=F")
        hist = twoy.history(period="5d")
        if not hist.empty:
            for date, row in hist.iterrows():
                d = date.strftime("%Y-%m-%d")
                result.setdefault(d, {})["tnx_2y"] = round(row["Close"], 4)
    except Exception as e:
        print(f"  Yahoo 2Y error: {e}")
    
    # HY spread proxy — iShares HY bond ETF (HYG) price as proxy
    try:
        hyg = yf.Ticker("HYG")
        hist = hyg.history(period="5d")
        if not hist.empty:
            for date, row in hist.iterrows():
                d = date.strftime("%Y-%m-%d")
                result.setdefault(d, {})["hyg"] = round(row["Close"], 2)
    except Exception as e:
        print(f"  Yahoo HYG error: {e}")
    
    return result

def rolling_zscore(values, window=ROLLING_WINDOW):
    """Compute rolling z-scores. Returns list of same length with None for warmup."""
    zscores = [None] * len(values)
    for i in range(window, len(values)):
        w = [v for v in values[i-window:i] if v is not None]
        if len(w) >= 20:
            mean = sum(w) / len(w)
            std = (sum((x - mean)**2 for x in w) / len(w)) ** 0.5
            if std > 0 and values[i] is not None:
                zscores[i] = (values[i] - mean) / std
    return zscores

def safe_avg(vals):
    clean = [v for v in vals if v is not None]
    return sum(clean) / len(clean) if clean else None

def build_index():
    """Main pipeline: fetch data, compute index, return JSON-ready output."""
    today = datetime.now().strftime("%Y-%m-%d")
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    
    print("=== TACO FUCK Index Update ===")
    print(f"Date: {today}")
    
    # 1. Fetch FRED historical data
    print("\n[1/4] Fetching FRED historical data...")
    vix_fred = fetch_fred_csv("VIXCLS", WARMUP_START, today)
    t10y2y_fred = fetch_fred_csv("T10Y2Y", WARMUP_START, today)
    umcsent_fred = fetch_fred_csv("UMCSENT", WARMUP_START, today)
    hy_fred = fetch_fred_csv("BAMLH0A0HYM2", WARMUP_START, today)
    sp_fred = fetch_fred_csv("SP500", WARMUP_START, today)
    
    print(f"  FRED VIX: {len(vix_fred)} pts, last={max(vix_fred.keys()) if vix_fred else 'NONE'}")
    print(f"  FRED T10Y2Y: {len(t10y2y_fred)} pts")
    print(f"  FRED UMCSENT: {len(umcsent_fred)} pts")
    print(f"  FRED HY: {len(hy_fred)} pts")
    print(f"  FRED SP500: {len(sp_fred)} pts")
    
    # 2. Fetch Yahoo Finance same-day data
    print("\n[2/4] Fetching Yahoo Finance same-day data...")
    yahoo = fetch_yahoo_today()
    
    yahoo_dates_added = 0
    for d, vals in yahoo.items():
        if "vix" in vals and d not in vix_fred:
            vix_fred[d] = vals["vix"]
            yahoo_dates_added += 1
        if "sp500" in vals and d not in sp_fred:
            sp_fred[d] = vals["sp500"]
        # Compute 10Y-2Y spread if we have both yields
        if "tnx_10y" in vals and "tnx_2y" in vals and d not in t10y2y_fred:
            t10y2y_fred[d] = round(vals["tnx_10y"] - vals["tnx_2y"], 4)
        # HY spread: if FRED doesn't have it, keep FRED's last value
        # (HYG price is inverse proxy but not directly comparable)
    
    print(f"  Yahoo added {yahoo_dates_added} new date(s)")
    if yahoo:
        latest_yahoo = max(yahoo.keys())
        print(f"  Yahoo latest: {latest_yahoo} -> {yahoo[latest_yahoo]}")
    
    # 3. Build aligned daily dataset
    print("\n[3/4] Computing index...")
    all_dates = sorted(set(vix_fred.keys()) | set(sp_fred.keys()))
    
    # Forward-fill UMCSENT (monthly)
    umcsent_monthly = sorted(umcsent_fred.keys())
    def get_umcsent(date_str):
        result = None
        for m in umcsent_monthly:
            if m <= date_str:
                result = umcsent_fred[m]
            else:
                break
        return result
    
    # Forward-fill HY spread for days Yahoo covers but FRED doesn't
    hy_sorted = sorted(hy_fred.keys())
    def get_hy(date_str):
        result = None
        for d in hy_sorted:
            if d <= date_str:
                result = hy_fred[d]
            else:
                break
        return result
    
    rows = []
    for d in all_dates:
        if d in vix_fred and d in sp_fred:
            rows.append({
                'date': d,
                'vix': vix_fred.get(d),
                't10y2y': t10y2y_fred.get(d),
                'umcsent': get_umcsent(d),
                'hy_spread': get_hy(d),
                'sp500': sp_fred.get(d)
            })
    
    # Compute z-scores
    F_z = rolling_zscore([r['vix'] for r in rows])
    U_z = rolling_zscore([r['t10y2y'] for r in rows])
    C_raw = rolling_zscore([r['umcsent'] for r in rows])
    C_z = [(-x if x is not None else None) for x in C_raw]
    K_z = rolling_zscore([r['hy_spread'] for r in rows])
    sp_z_raw = rolling_zscore([r['sp500'] for r in rows])
    taco_pressure = [(-x if x is not None else None) for x in sp_z_raw]
    
    # Build output from display start date
    output = []
    for i, r in enumerate(rows):
        if r['date'] < DISPLAY_START:
            continue
        f, u, c, k, tp = F_z[i], U_z[i], C_z[i], K_z[i], taco_pressure[i]
        fuck = safe_avg([f, u, c, k])
        taco_fuck = safe_avg([fuck, tp]) if fuck is not None and tp is not None else None
        
        output.append({
            'date': r['date'],
            'vix': r['vix'],
            't10y2y': r['t10y2y'],
            'umcsent': r['umcsent'],
            'hy_spread': r['hy_spread'],
            'sp500': r['sp500'],
            'F': round(f, 4) if f is not None else None,
            'U': round(u, 4) if u is not None else None,
            'C': round(c, 4) if c is not None else None,
            'K': round(k, 4) if k is not None else None,
            'FUCK': round(fuck, 4) if fuck is not None else None,
            'taco_pressure': round(tp, 4) if tp is not None else None,
            'TACO_FUCK': round(taco_fuck, 4) if taco_fuck is not None else None
        })
    
    print(f"  Total data points: {len(output)}")
    print(f"  Date range: {output[0]['date']} to {output[-1]['date']}")
    
    latest = output[-1]
    print(f"\n  LATEST ({latest['date']}):")
    print(f"    VIX: {latest['vix']}")
    print(f"    S&P 500: {latest['sp500']}")
    print(f"    TACO FUCK: {latest['TACO_FUCK']}")
    
    return output

def inject_into_html(data):
    """Replace the DATA array in the HTML file."""
    print("\n[4/4] Injecting into HTML...")
    
    html = HTML_FILE.read_text()
    
    # Build compact JSON data line
    entries = []
    for r in data:
        parts = []
        for key in ['date','vix','t10y2y','umcsent','hy_spread','sp500',
                     'F','U','C','K','FUCK','taco_pressure','TACO_FUCK']:
            v = r[key]
            if v is None:
                parts.append(f'"{key}":null')
            elif isinstance(v, str):
                parts.append(f'"{key}":"{v}"')
            else:
                parts.append(f'"{key}":{v}')
        entries.append('{' + ','.join(parts) + '}')
    
    new_data_line = 'const DATA = [' + ','.join(entries) + '];'
    
    # Replace the DATA line
    lines = html.split('\n')
    replaced = False
    for i, line in enumerate(lines):
        if 'const DATA = [' in line:
            indent = line[:len(line) - len(line.lstrip())]
            lines[i] = indent + new_data_line
            replaced = True
            print(f"  Replaced DATA at line {i+1}")
            break
    
    if not replaced:
        print("  ERROR: Could not find DATA line!")
        return False
    
    HTML_FILE.write_text('\n'.join(lines))
    print(f"  HTML written: {len('\n'.join(lines))} chars")
    return True

def git_push():
    """Commit and push changes."""
    import subprocess
    today = datetime.now().strftime("%Y-%m-%d")
    os.chdir(REPO_DIR)
    subprocess.run(["git", "add", "index.html"], check=True)
    result = subprocess.run(
        ["git", "commit", "-m", f"TACO FUCK update: {today} (hybrid Yahoo+FRED)"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        if "nothing to commit" in result.stdout + result.stderr:
            print("  No changes to commit")
            return True
        print(f"  Commit error: {result.stderr}")
        return False
    
    push = subprocess.run(["git", "push"], capture_output=True, text=True)
    if push.returncode != 0:
        print(f"  Push error: {push.stderr}")
        return False
    
    print(f"  Pushed to GitHub!")
    return True

if __name__ == "__main__":
    data = build_index()
    if data and inject_into_html(data):
        git_push()
    print("\n=== Done ===")
