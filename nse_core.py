"""NSE end-of-day data layer: universe, downloader, adjustment, metrics.

Everything the app and the build script need, in one module, so the repo has
no package folders to get wrong.
"""

from __future__ import annotations

import io
import json
import logging
import re
import time
import zipfile
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests
from dateutil.relativedelta import relativedelta

log = logging.getLogger(__name__)

# ==========================================================================
# UNIVERSE
# ==========================================================================

BROAD_MARKET = [
    "Nifty 50",
    "Nifty Next 50",
    "Nifty 100",
    "Nifty 200",
    "Nifty 500",
    "Nifty Total Market",
    "Nifty Midcap 50",
    "Nifty Smallcap 50",
]

SECTORS = [
    "Nifty Bank",
    "Nifty PSU Bank",
    "Nifty Private Bank",
    "Nifty Financial Services",
    "Nifty Housing Finance",
    "Nifty NBFC",
    "Nifty Energy",
    "Nifty Power",
    "Nifty Auto",
    "Nifty FMCG",
    "Nifty IT",
    "Nifty Metal",
    "Nifty Pharma",
    "Nifty Healthcare",
    "Nifty Hospital",
    "Nifty Realty",
    "Nifty Cement",
    "Nifty Construction",
    "Nifty Media",
    "Nifty Capital Goods",
    "Nifty Consumer Durables",
    "Nifty Retail",
    "Nifty Capital Markets",
    "Nifty Commodities",
    "Nifty Defence",
    "Nifty India Digital",
    "Nifty India Manufacturing",
    "Nifty India Tourism",
]

ALL_INDICES = BROAD_MARKET + SECTORS


def normalise(name: str) -> str:
    """Uppercase, strip punctuation/spaces and the trailing word INDEX."""
    s = re.sub(r"[^A-Za-z0-9]+", "", str(name)).upper()
    if s.endswith("INDEX"):
        s = s[: -len("INDEX")]
    return s


# NSE's published name -> our display name.
# Keys are normalise()d. Extend when data/index_names_seen.csv shows a mismatch.
ALIASES = {
    "NIFTYHEALTHCARE": "Nifty Healthcare",
    "NIFTYHEALTHCARE1": "Nifty Healthcare",
    "NIFTYHOUSING": "Nifty Housing Finance",
    "NIFTYINDIADEFENCE": "Nifty Defence",
    "NIFTYINDIACONSUMPTION": None,          # explicitly not requested
    "NIFTYMIDSMALLHEALTHCARE": None,
    "NIFTYFINANCIALSERVICES2550": None,     # variant index, not the headline one
    "NIFTYFINANCIALSERVICESEXBANK": None,
    "NIFTYPRIVATEBANK": "Nifty Private Bank",
    "NIFTYCONSUMERDURABLES": "Nifty Consumer Durables",
    "NIFTYCAPITALMARKETS": "Nifty Capital Markets",
    "NIFTYMIDCAP50": "Nifty Midcap 50",
    "NIFTYSMALLCAP50": "Nifty Smallcap 50",
    "NIFTYTOTALMARKET": "Nifty Total Market",
}


def resolve_index_name(nse_name: str):
    """Map an NSE-published index name to a display name, or None if unwanted."""
    key = normalise(nse_name)
    if key in ALIASES:
        return ALIASES[key]
    for display in ALL_INDICES:
        if normalise(display) == key:
            return display
    return None


# ---------------------------------------------------------------------------
# Constituent list files on the NSE archive.
# Explicit overrides first; anything not listed is probed with generated slugs.
# ---------------------------------------------------------------------------
CONSTITUENT_FILE_OVERRIDES = {
    "Nifty 50": "ind_nifty50list.csv",
    "Nifty Next 50": "ind_niftynext50list.csv",
    "Nifty 100": "ind_nifty100list.csv",
    "Nifty 200": "ind_nifty200list.csv",
    "Nifty 500": "ind_nifty500list.csv",
    "Nifty Total Market": "ind_niftytotalmarket_list.csv",
    "Nifty Midcap 50": "ind_niftymidcap50list.csv",
    "Nifty Smallcap 50": "ind_niftysmallcap50list.csv",
    "Nifty Bank": "ind_niftybanklist.csv",
    "Nifty Financial Services": "ind_niftyfinancelist.csv",
    "Nifty Private Bank": "ind_nifty_privatebanklist.csv",
    "Nifty PSU Bank": "ind_niftypsubanklist.csv",
}


def candidate_constituent_files(display_name: str):
    """Ordered list of plausible archive filenames for an index's members."""
    if display_name in CONSTITUENT_FILE_OVERRIDES:
        first = [CONSTITUENT_FILE_OVERRIDES[display_name]]
    else:
        first = []

    base = normalise(display_name)
    body = base[5:] if base.startswith("NIFTY") else base
    body = body.lower()

    generated = [
        f"ind_nifty{body}list.csv",
        f"ind_nifty{body}_list.csv",
        f"ind_nifty_{body}list.csv",
        f"ind_nifty_{body}_list.csv",
        f"ind_nifty{body}.csv",
        f"ind_{body}list.csv",
        f"ind_niftyindia{body}_list.csv",
        f"ind_niftyindia{body}list.csv",
    ]
    out, seen = [], set()
    for f in first + generated:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out

# ==========================================================================
# HTTP ACCESS TO NSE ARCHIVES
# ==========================================================================



HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/csv,application/csv,application/zip,*/*",
    "Accept-Language": "en-GB,en;q=0.9",
    "Referer": "https://www.nseindia.com/all-reports",
    "Connection": "keep-alive",
}

ARCHIVE_HOSTS = [
    "https://nsearchives.nseindia.com",
    "https://archives.nseindia.com",
]


class NSEClient:
    def __init__(self, timeout: int = 30, retries: int = 3, pause: float = 1.0):
        self.timeout = timeout
        self.retries = retries
        self.pause = pause
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self._warmed = False

    def _warm(self):
        """Pick up cookies. Failure is not fatal -- archive files often work without."""
        if self._warmed:
            return
        try:
            self.session.get("https://www.nseindia.com", timeout=self.timeout)
        except requests.RequestException as exc:
            log.debug("Cookie warm-up failed (continuing): %s", exc)
        self._warmed = True

    def get(self, path: str) -> bytes | None:
        """Fetch `path` (leading slash) from the archive hosts. None on 404 everywhere."""
        self._warm()
        last_status = None
        for host in ARCHIVE_HOSTS:
            url = host + path
            for attempt in range(self.retries):
                try:
                    r = self.session.get(url, timeout=self.timeout)
                except requests.RequestException as exc:
                    log.debug("%s attempt %d error: %s", url, attempt + 1, exc)
                    time.sleep(self.pause * (attempt + 1))
                    continue
                last_status = r.status_code
                if r.status_code == 200 and r.content:
                    return r.content
                if r.status_code == 404:
                    break  # genuinely absent on this host; try the next host
                time.sleep(self.pause * (attempt + 1))
        log.debug("Not retrieved: %s (last status %s)", path, last_status)
        return None

    @staticmethod
    def unzip_single(payload: bytes) -> bytes:
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            name = zf.namelist()[0]
            return zf.read(name)

# ==========================================================================
# RETURNS AND 52-WEEK RANGE
# ==========================================================================

PERIODS = [
    ("1D", None),
    ("1W", relativedelta(days=7)),
    ("1M", relativedelta(months=1)),
    ("2M", relativedelta(months=2)),
    ("3M", relativedelta(months=3)),
    ("6M", relativedelta(months=6)),
    ("1Y", relativedelta(years=1)),
]

RETURN_COLS = [f"{code} %" for code, _ in PERIODS]
RANGE_COLS = ["From 52W High %", "Above 52W Low %"]
NUMERIC_COLS = RETURN_COLS + RANGE_COLS
MIN_DAYS_FOR_52W = 200  # trading days; below this the 52W range is partial


def _pivot(df: pd.DataFrame, key: str, value: str) -> pd.DataFrame:
    return df.pivot_table(index="date", columns=key, values=value, aggfunc="last").sort_index()


def build_panel(df: pd.DataFrame, key: str, close_col: str,
                high_col: str, low_col: str):
    """Return (close, high, low) wide frames indexed by date."""
    close = _pivot(df, key, close_col)
    high = _pivot(df, key, high_col)
    low = _pivot(df, key, low_col)
    return close, high, low


def _row_on_or_before(frame: pd.DataFrame, anchor: pd.Timestamp):
    pos = frame.index.searchsorted(anchor, side="right") - 1
    if pos < 0:
        return None
    return frame.iloc[pos]


def compute(close: pd.DataFrame, high: pd.DataFrame, low: pd.DataFrame,
            asof: pd.Timestamp | None = None,
            ffill_limit: int = 10) -> pd.DataFrame:
    if close.empty:
        return pd.DataFrame()

    asof = pd.Timestamp(asof) if asof is not None else close.index.max()
    close = close.loc[:asof]
    high = high.loc[:asof]
    low = low.loc[:asof]

    traded_today = close.iloc[-1].notna()
    filled = close.ffill(limit=ffill_limit)
    last = filled.iloc[-1]

    out = pd.DataFrame(index=close.columns)
    out["Close"] = last

    for code, offset in PERIODS:
        colname = f"{code} %"
        if offset is None:
            base = filled.iloc[-2] if len(filled) >= 2 else None
        else:
            base = _row_on_or_before(filled, asof - offset)
        if base is None:
            out[colname] = pd.NA
        else:
            out[colname] = (last / base - 1.0) * 100.0

    window = close.index > (asof - pd.Timedelta(days=365))
    n_days = int(window.sum())
    h52 = high.loc[window].max()
    l52 = low.loc[window].min()

    out["52W High"] = h52
    out["52W Low"] = l52
    out["From 52W High %"] = (last / h52 - 1.0) * 100.0
    out["Above 52W Low %"] = (last / l52 - 1.0) * 100.0
    out["52W window days"] = n_days
    out["Partial 52W"] = n_days < MIN_DAYS_FOR_52W

    out = out[traded_today.reindex(out.index).fillna(False)]
    return out.replace([float("inf"), float("-inf")], pd.NA)


def coverage_note(close: pd.DataFrame, asof: pd.Timestamp) -> str:
    window = close.index > (asof - pd.Timedelta(days=365))
    n = int(window.sum())
    if n >= MIN_DAYS_FOR_52W:
        return ""
    return (
        f"52-week range is based on {n} trading days of history, not a full year. "
        "Extend the backfill window in build_data.py to correct this."
    )

# ==========================================================================
# BUILD: DOWNLOAD, PARSE, ADJUST
# ==========================================================================



DATA_DIR = Path(__file__).resolve().parent / "data"
INDEX_FILE = DATA_DIR / "index_daily.parquet"
STOCK_FILE = DATA_DIR / "stock_daily.parquet"
CONSTITUENT_FILE = DATA_DIR / "constituents.parquet"
META_FILE = DATA_DIR / "meta.json"
NAMES_SEEN_FILE = DATA_DIR / "index_names_seen.csv"
CA_LOG_FILE = DATA_DIR / "corporate_action_events.csv"

# Series treated as ordinary equity. BE = trade-to-trade, still equity.
EQUITY_SERIES = {"EQ", "BE", "BZ", "SM", "ST"}


# ---------------------------------------------------------------------------
# parsers
# ---------------------------------------------------------------------------
def _parse_index_close_all(payload: bytes, on: date) -> pd.DataFrame:
    df = pd.read_csv(io.BytesIO(payload))
    df.columns = [c.strip() for c in df.columns]

    def pick(*options):
        for o in options:
            if o in df.columns:
                return o
        raise KeyError(f"none of {options} in {list(df.columns)}")

    out = pd.DataFrame(
        {
            "date": pd.Timestamp(on),
            "name": df[pick("Index Name", "Index Name ")].astype(str).str.strip(),
            "open": pd.to_numeric(df[pick("Open Index Value", "Open")], errors="coerce"),
            "high": pd.to_numeric(df[pick("High Index Value", "High")], errors="coerce"),
            "low": pd.to_numeric(df[pick("Low Index Value", "Low")], errors="coerce"),
            "close": pd.to_numeric(
                df[pick("Closing Index Value", "Close", "Close Index Value")],
                errors="coerce",
            ),
        }
    )
    return out.dropna(subset=["close"])


def _parse_sec_bhavdata_full(payload: bytes, on: date) -> pd.DataFrame:
    df = pd.read_csv(io.BytesIO(payload))
    df.columns = [c.strip().upper() for c in df.columns]
    df["SERIES"] = df["SERIES"].astype(str).str.strip().str.upper()
    df = df[df["SERIES"].isin(EQUITY_SERIES)]
    out = pd.DataFrame(
        {
            "date": pd.Timestamp(on),
            "symbol": df["SYMBOL"].astype(str).str.strip().str.upper(),
            "series": df["SERIES"],
            "prev_close": pd.to_numeric(df["PREV_CLOSE"], errors="coerce"),
            "open": pd.to_numeric(df["OPEN_PRICE"], errors="coerce"),
            "high": pd.to_numeric(df["HIGH_PRICE"], errors="coerce"),
            "low": pd.to_numeric(df["LOW_PRICE"], errors="coerce"),
            "close": pd.to_numeric(df["CLOSE_PRICE"], errors="coerce"),
            "volume": pd.to_numeric(df["TTL_TRD_QNTY"], errors="coerce"),
        }
    )
    return out.dropna(subset=["close"])


def _parse_udiff(payload: bytes, on: date) -> pd.DataFrame:
    df = pd.read_csv(io.BytesIO(payload))
    df.columns = [c.strip() for c in df.columns]
    df = df[df["FinInstrmTp"].astype(str).str.upper().isin({"STK", "EQ"})]
    df["SctySrs"] = df["SctySrs"].astype(str).str.strip().str.upper()
    df = df[df["SctySrs"].isin(EQUITY_SERIES)]
    out = pd.DataFrame(
        {
            "date": pd.Timestamp(on),
            "symbol": df["TckrSymb"].astype(str).str.strip().str.upper(),
            "series": df["SctySrs"],
            "prev_close": pd.to_numeric(df["PrvsClsgPric"], errors="coerce"),
            "open": pd.to_numeric(df["OpnPric"], errors="coerce"),
            "high": pd.to_numeric(df["HghPric"], errors="coerce"),
            "low": pd.to_numeric(df["LwPric"], errors="coerce"),
            "close": pd.to_numeric(df["ClsPric"], errors="coerce"),
            "volume": pd.to_numeric(df.get("TtlTradgVol"), errors="coerce"),
        }
    )
    return out.dropna(subset=["close"])


# ---------------------------------------------------------------------------
# per-day fetch
# ---------------------------------------------------------------------------
def fetch_day(client: NSEClient, on: date):
    """Return (index_df, stock_df). Either may be None (holiday / not published)."""
    ddmmyyyy = on.strftime("%d%m%Y")
    yyyymmdd = on.strftime("%Y%m%d")

    idx = None
    payload = client.get(f"/content/indices/ind_close_all_{ddmmyyyy}.csv")
    if payload:
        try:
            idx = _parse_index_close_all(payload, on)
        except Exception as exc:
            log.warning("%s index parse failed: %s", on, exc)

    stk = None
    payload = client.get(f"/products/content/sec_bhavdata_full_{ddmmyyyy}.csv")
    if payload:
        try:
            stk = _parse_sec_bhavdata_full(payload, on)
        except Exception as exc:
            log.warning("%s sec_bhavdata parse failed: %s", on, exc)
    if stk is None or stk.empty:
        payload = client.get(
            f"/content/cm/BhavCopy_NSE_CM_0_0_0_{yyyymmdd}_F_0000.csv.zip"
        )
        if payload:
            try:
                stk = _parse_udiff(NSEClient.unzip_single(payload), on)
            except Exception as exc:
                log.warning("%s UDiFF parse failed: %s", on, exc)

    return idx, stk


# ---------------------------------------------------------------------------
# corporate action back-adjustment
# ---------------------------------------------------------------------------
def back_adjust(stocks: pd.DataFrame, threshold: float = 0.02):
    """Chain-adjust prices for splits/bonuses/rights.

    NSE restates PREV_CLOSE on the ex-date to the adjusted level, so
        factor_t = prev_close_t / close_{t-1}
    deviating from 1 by more than `threshold` marks an adjustment event.
    Every price strictly before t is multiplied by the cumulative product of
    all later factors. NSE does not adjust for ordinary dividends, so those
    do not appear here.
    """
    stocks = stocks.sort_values(["symbol", "date"]).copy()
    g = stocks.groupby("symbol", sort=False)
    prior_close = g["close"].shift(1)

    factor = stocks["prev_close"] / prior_close
    valid = prior_close.notna() & stocks["prev_close"].notna() & (prior_close > 0)
    is_event = valid & ((factor - 1).abs() > threshold)

    events = stocks.loc[is_event, ["date", "symbol"]].copy()
    events["factor"] = factor[is_event].round(6)
    events["prev_close_reported"] = stocks.loc[is_event, "prev_close"].values
    events["close_previous_day"] = prior_close[is_event].values

    f = pd.Series(1.0, index=stocks.index)
    f[is_event] = factor[is_event]

    # cumulative product of factors at and after each row, per symbol,
    # applied to rows strictly before the event -> reverse cumprod, shifted.
    stocks["_f"] = f
    rev = (
        stocks.iloc[::-1]
        .groupby("symbol", sort=False)["_f"]
        .cumprod()
        .iloc[::-1]
    )
    # rev at row i = product of factors from i to end (inclusive).
    # We want the multiplier for rows BEFORE an event, so divide out own factor.
    mult = rev / stocks["_f"]

    for col in ("open", "high", "low", "close"):
        stocks[col + "_adj"] = stocks[col] * mult

    stocks = stocks.drop(columns=["_f"])
    return stocks, events


# ---------------------------------------------------------------------------
# constituents
# ---------------------------------------------------------------------------
def fetch_constituents(client: NSEClient) -> tuple[pd.DataFrame, dict]:
    rows, resolved = [], {}
    for display in ALL_INDICES:
        got = False
        for fname in candidate_constituent_files(display):
            payload = client.get(f"/content/indices/{fname}")
            if not payload:
                continue
            try:
                df = pd.read_csv(io.BytesIO(payload))
            except Exception:
                continue
            df.columns = [c.strip() for c in df.columns]
            if "Symbol" not in df.columns:
                continue
            for _, r in df.iterrows():
                rows.append(
                    {
                        "index_name": display,
                        "symbol": str(r["Symbol"]).strip().upper(),
                        "company": str(r.get("Company Name", "")).strip(),
                        "industry": str(r.get("Industry", "")).strip(),
                    }
                )
            resolved[display] = fname
            got = True
            break
        if not got:
            resolved[display] = None
            log.warning("Constituent list not found for %s", display)
    return pd.DataFrame(rows), resolved


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------
def _existing(path: Path) -> pd.DataFrame | None:
    if path.exists():
        return pd.read_parquet(path)
    return None


def update(days_back: int = 400, refresh_constituents: bool = True,
           until: date | None = None) -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    client = NSEClient()

    until = until or date.today()
    start = until - timedelta(days=days_back)

    idx_hist = _existing(INDEX_FILE)
    stk_hist = _existing(STOCK_FILE)

    have_idx = set(idx_hist["date"].dt.date) if idx_hist is not None else set()
    have_stk = set(stk_hist["date"].dt.date) if stk_hist is not None else set()

    wanted = []
    d = start
    while d <= until:
        if d.weekday() < 5:
            wanted.append(d)
        d += timedelta(days=1)

    new_idx, new_stk, holidays = [], [], []
    for d in wanted:
        need_i = d not in have_idx
        need_s = d not in have_stk
        if not (need_i or need_s):
            continue
        i_df, s_df = fetch_day(client, d)
        if i_df is not None and need_i and not i_df.empty:
            new_idx.append(i_df)
        if s_df is not None and need_s and not s_df.empty:
            new_stk.append(s_df)
        if i_df is None and s_df is None:
            holidays.append(d.isoformat())

    if new_idx:
        idx_hist = pd.concat([idx_hist] + new_idx if idx_hist is not None else new_idx)
    if new_stk:
        stk_hist = pd.concat([stk_hist] + new_stk if stk_hist is not None else new_stk)

    if idx_hist is None or stk_hist is None:
        raise RuntimeError(
            "No data retrieved from NSE. Most likely the host IP is blocked "
            "(common on cloud runners). Run the build locally and commit data/."
        )

    idx_hist = (
        idx_hist.drop_duplicates(["date", "name"], keep="last")
        .sort_values(["name", "date"])
        .reset_index(drop=True)
    )
    idx_hist = idx_hist[idx_hist["date"].dt.date >= start]

    # keep only requested indices, tagged with display name
    seen = sorted(idx_hist["name"].unique())
    pd.DataFrame({"nse_index_name": seen}).to_csv(NAMES_SEEN_FILE, index=False)
    idx_hist["display"] = idx_hist["name"].map(resolve_index_name)
    matched = idx_hist.dropna(subset=["display"]).copy()

    stk_hist = (
        stk_hist.drop_duplicates(["date", "symbol"], keep="last")
        .sort_values(["symbol", "date"])
        .reset_index(drop=True)
    )
    stk_hist = stk_hist[stk_hist["date"].dt.date >= start]

    stk_adj, ca_events = back_adjust(stk_hist)

    matched.to_parquet(INDEX_FILE, index=False)
    stk_adj.to_parquet(STOCK_FILE, index=False)
    ca_events.to_csv(CA_LOG_FILE, index=False)

    resolved = {}
    if refresh_constituents or not CONSTITUENT_FILE.exists():
        cons, resolved = fetch_constituents(client)
        if not cons.empty:
            cons.to_parquet(CONSTITUENT_FILE, index=False)

    missing_indices = [
        n for n in ALL_INDICES if n not in set(matched["display"].unique())
    ]

    meta = {
        "built_at_utc": pd.Timestamp.utcnow().isoformat(),
        "last_trade_date": str(matched["date"].max().date()),
        "index_rows": int(len(matched)),
        "stock_rows": int(len(stk_adj)),
        "symbols": int(stk_adj["symbol"].nunique()),
        "history_from": str(matched["date"].min().date()),
        "indices_not_found_in_nse_file": missing_indices,
        "constituent_files_resolved": resolved,
        "constituent_lists_missing": [k for k, v in resolved.items() if v is None],
        "non_trading_days_probed": holidays,
        "corporate_action_events": int(len(ca_events)),
    }
    META_FILE.write_text(json.dumps(meta, indent=2))
    return meta
