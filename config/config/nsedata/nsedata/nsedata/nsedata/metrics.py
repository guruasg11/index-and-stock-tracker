"""Return and 52-week range computation.

Conventions, applied identically to indices and stocks:

  Return over period P = close(asof) / close(last trading day on or before
  asof - P) - 1, with P taken as a calendar offset, not a bar count. A
  1-month return therefore always spans one calendar month regardless of
  how many holidays fell inside it.

  Daily return uses the previous trading day.

  52-week high / low are the maximum intraday High and minimum intraday Low
  over the 365 calendar days ending on asof, taken from NSE's own daily
  High/Low fields. Stock prices are back-adjusted for splits, bonuses and
  rights before this runs. Index levels need no adjustment.

  % from 52W high = close/high - 1  (zero or negative)
  % above 52W low = close/low  - 1  (zero or positive)
"""

from __future__ import annotations

import pandas as pd
from dateutil.relativedelta import relativedelta

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
