"""NSE end-of-day performance board.

Indices, sectors and stocks on one set of return columns, refreshed from
NSE archive files after the close. No live data.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from config.universe import BROAD_MARKET, SECTORS
from nsedata import metrics

DATA_DIR = Path(__file__).resolve().parent / "data"

st.set_page_config(
    page_title="NSE EOD Board",
    page_icon="▦",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
      .block-container {padding-top: 2.2rem; padding-bottom: 3rem; max-width: 1500px;}
      [data-testid="stDataFrame"] div[role="gridcell"] {font-variant-numeric: tabular-nums;}
      h1 {font-size: 1.45rem; font-weight: 650; letter-spacing: -0.01em; margin-bottom: 0.1rem;}
      h2 {font-size: 1.05rem; font-weight: 600; letter-spacing: 0.01em;
          text-transform: uppercase; opacity: 0.72; margin-top: 1.6rem;}
      .stamp {font-size: 0.8rem; opacity: 0.6; margin-bottom: 1.2rem;}
      .flag {font-size: 0.78rem; opacity: 0.75; border-left: 2px solid currentColor;
             padding-left: 0.6rem; margin: 0.4rem 0;}
    </style>
    """,
    unsafe_allow_html=True,
)

# Streamlit renamed use_container_width -> width in late-2025 builds.
_ST_VER = tuple(int(x) for x in st.__version__.split(".")[:2])
_WIDE = {"width": "stretch"} if _ST_VER >= (1, 49) else {"use_container_width": True}

GREEN = (15, 157, 88)
RED = (217, 48, 37)


# ---------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def load_store():
    idx_p, stk_p = DATA_DIR / "index_daily.parquet", DATA_DIR / "stock_daily.parquet"
    if not idx_p.exists() or not stk_p.exists():
        return None
    idx = pd.read_parquet(idx_p)
    stk = pd.read_parquet(stk_p)
    cons_p = DATA_DIR / "constituents.parquet"
    cons = pd.read_parquet(cons_p) if cons_p.exists() else pd.DataFrame(
        columns=["index_name", "symbol", "company", "industry"]
    )
    meta_p = DATA_DIR / "meta.json"
    meta = json.loads(meta_p.read_text()) if meta_p.exists() else {}
    return idx, stk, cons, meta


@st.cache_data(show_spinner=False)
def index_metrics(idx: pd.DataFrame) -> pd.DataFrame:
    c, h, l = metrics.build_panel(idx, "display", "close", "high", "low")
    out = metrics.compute(c, h, l)
    out.index.name = "Index"
    return out.reset_index()


@st.cache_data(show_spinner=False)
def stock_metrics(stk: pd.DataFrame) -> pd.DataFrame:
    c, h, l = metrics.build_panel(stk, "symbol", "close_adj", "high_adj", "low_adj")
    out = metrics.compute(c, h, l)
    out.index.name = "Symbol"
    return out.reset_index()


# ---------------------------------------------------------------------------
# presentation
# ---------------------------------------------------------------------------
DISPLAY_ORDER = (
    ["Close"]
    + metrics.RETURN_COLS
    + ["From 52W High %", "Above 52W Low %", "52W High", "52W Low"]
)


def _rgba(rgb, alpha):
    return f"background-color: rgba({rgb[0]},{rgb[1]},{rgb[2]},{alpha:.3f})"


def _diverging(v, scale):
    if pd.isna(v):
        return ""
    x = max(-1.0, min(1.0, float(v) / scale))
    if x == 0:
        return ""
    return _rgba(GREEN if x > 0 else RED, 0.10 + 0.50 * abs(x))


def _sequential(v, scale, rgb):
    if pd.isna(v):
        return ""
    x = min(1.0, abs(float(v)) / scale)
    return _rgba(rgb, 0.08 + 0.50 * x)


def style_table(df: pd.DataFrame, ret_scale: float, high_scale: float,
                low_scale: float):
    present_returns = [c for c in metrics.RETURN_COLS if c in df.columns]
    sty = df.style
    if present_returns:
        sty = sty.map(lambda v: _diverging(v, ret_scale), subset=present_returns)
    if "From 52W High %" in df.columns:
        sty = sty.map(lambda v: _sequential(v, high_scale, RED),
                      subset=["From 52W High %"])
    if "Above 52W Low %" in df.columns:
        sty = sty.map(lambda v: _sequential(v, low_scale, GREEN),
                      subset=["Above 52W Low %"])

    fmt = {c: "{:+.2f}" for c in present_returns}
    for c in ("From 52W High %", "Above 52W Low %"):
        if c in df.columns:
            fmt[c] = "{:+.2f}"
    for c in ("Close", "52W High", "52W Low"):
        if c in df.columns:
            fmt[c] = "{:,.2f}"
    return sty.format(fmt, na_rep="—")


def render_table(df: pd.DataFrame, label_cols, key, selectable=False,
                 height=None, scales=(10.0, 30.0, 60.0)):
    cols = [c for c in label_cols if c in df.columns] + [
        c for c in DISPLAY_ORDER if c in df.columns
    ]
    view = df[cols].copy()
    h = height or min(60 + 35 * len(view), 720)

    if len(view) > int(st.session_state.get("style_cap", 500)):
        st.markdown(
            f"<div class='flag'>Heat map is off above "
            f"{st.session_state.get('style_cap', 500)} rows. Filter or raise the "
            f"cap in the sidebar to colour this table.</div>",
            unsafe_allow_html=True,
        )
        payload = view
    else:
        payload = style_table(view, *scales)

    kwargs = dict(hide_index=True, height=h, key=key, **_WIDE)
    if selectable:
        kwargs.update(on_select="rerun", selection_mode="single-row")
    return st.dataframe(payload, **kwargs), view


def download(df: pd.DataFrame, name: str, key: str):
    st.download_button(
        "Download CSV",
        df.to_csv(index=False).encode(),
        file_name=name,
        mime="text/csv",
        key=key,
    )


# ---------------------------------------------------------------------------
# app
# ---------------------------------------------------------------------------
store = load_store()
if store is None:
    st.title("NSE end-of-day board")
    st.error(
        "No data store found. Run `python build_data.py --days 400` and commit "
        "the `data/` folder, or point this app at a repo that already has one."
    )
    st.stop()

idx_raw, stk_raw, cons, meta = store
imx = index_metrics(idx_raw)
smx = stock_metrics(stk_raw)

last_date = pd.to_datetime(idx_raw["date"]).max().date()

with st.sidebar:
    st.markdown("### Data")
    st.write(f"Close of **{last_date:%d %b %Y}**")
    st.caption(
        f"{meta.get('symbols', smx.shape[0]):,} symbols · "
        f"{meta.get('history_from', '?')} onward · built "
        f"{meta.get('built_at_utc', '?')[:16].replace('T', ' ')} UTC"
    )
    if st.button("Reload store", **_WIDE):
        st.cache_data.clear()
        st.rerun()

    if st.button("Fetch latest from NSE", **_WIDE,
                 help="Downloads any trading days missing from the store."):
        try:
            from nsedata.build import update as _update
            with st.spinner("Downloading from NSE…"):
                m = _update(days_back=400, refresh_constituents=False)
            st.cache_data.clear()
            st.success(f"Store now runs to {m['last_trade_date']}.")
            st.rerun()
        except Exception as exc:
            st.error(
                f"NSE fetch failed: {exc}\n\n"
                "If this host is a cloud runner, NSE has most likely blocked "
                "the IP. The scheduled GitHub Action is the normal refresh "
                "path; this button is a fallback."
            )

    st.markdown("### Heat map")
    ret_scale = st.slider("Return saturates at ±%", 2.0, 40.0, 10.0, 1.0)
    high_scale = st.slider("Below 52W high saturates at %", 5.0, 80.0, 30.0, 5.0)
    low_scale = st.slider("Above 52W low saturates at %", 10.0, 200.0, 60.0, 10.0)
    st.session_state["style_cap"] = st.number_input(
        "Colour up to N rows", 100, 3000, 500, 100,
        help="Colouring very large tables is slow.",
    )
    scales = (ret_scale, high_scale, low_scale)

    missing = meta.get("indices_not_found_in_nse_file", [])
    if missing:
        st.markdown("### Gaps")
        st.caption("Not published under the expected name: " + ", ".join(missing))
    missing_c = meta.get("constituent_lists_missing", [])
    if missing_c:
        st.caption("Member list unresolved: " + ", ".join(missing_c))

st.title("NSE end-of-day board")
st.markdown(
    f"<div class='stamp'>Returns are calendar-anchored to the last trading day "
    f"on or before each cut-off. 52-week range uses intraday highs and lows over "
    f"365 days to {last_date:%d %b %Y}. Stock prices back-adjusted for splits, "
    f"bonuses and rights.</div>",
    unsafe_allow_html=True,
)

if "view" not in st.session_state:
    st.session_state["view"] = "Indices & sectors"
if "drill" not in st.session_state:
    st.session_state["drill"] = BROAD_MARKET[0]

st.radio(
    "View", ["Indices & sectors", "Constituents", "Compare"],
    horizontal=True, key="view", label_visibility="collapsed",
)
view = st.session_state["view"]


def go_to_constituents(name: str):
    st.session_state["drill"] = name
    st.session_state["view"] = "Constituents"
    st.rerun()


# --- page 1 -----------------------------------------------------------------
if view == "Indices & sectors":
    broad = imx[imx["Index"].isin(BROAD_MARKET)].copy()
    broad["Index"] = pd.Categorical(broad["Index"], BROAD_MARKET, ordered=True)
    broad = broad.sort_values("Index")

    sect = imx[imx["Index"].isin(SECTORS)].copy()
    sect["Index"] = pd.Categorical(sect["Index"], SECTORS, ordered=True)
    sect = sect.sort_values("Index")

    st.markdown("## Broad market")
    ev, shown = render_table(broad, ["Index"], "tbl_broad", selectable=True,
                             scales=scales)
    if ev.selection and ev.selection.get("rows"):
        go_to_constituents(str(shown.iloc[ev.selection["rows"][0]]["Index"]))
    download(shown, "nse_broad_market.csv", "dl_broad")

    st.markdown("## Sectors")
    ev2, shown2 = render_table(sect, ["Index"], "tbl_sect", selectable=True,
                               scales=scales)
    if ev2.selection and ev2.selection.get("rows"):
        go_to_constituents(str(shown2.iloc[ev2.selection["rows"][0]]["Index"]))
    download(shown2, "nse_sectors.csv", "dl_sect")

    st.caption("Select any row to open its constituents.")

# --- page 2 -----------------------------------------------------------------
elif view == "Constituents":
    available = sorted(cons["index_name"].unique()) if not cons.empty else []
    if not available:
        st.warning(
            "No constituent lists in the store. Run `python build_data.py` to "
            "fetch them."
        )
        st.stop()

    default = st.session_state["drill"] if st.session_state["drill"] in available else available[0]
    choice = st.selectbox("Index or sector", available,
                          index=available.index(default))
    st.session_state["drill"] = choice

    members = (
        cons[cons["index_name"] == choice][["symbol", "company", "industry"]]
        .rename(columns={"symbol": "Symbol", "company": "Company",
                         "industry": "Industry"})
        .drop_duplicates("Symbol")
    )
    tbl = members.merge(smx, on="Symbol", how="left")

    q = st.text_input("Filter by symbol or company", "")
    if q:
        m = (tbl["Symbol"].str.contains(q, case=False, na=False)
             | tbl["Company"].str.contains(q, case=False, na=False))
        tbl = tbl[m]

    no_price = tbl[tbl["Close"].isna()]["Symbol"].tolist()
    tbl = tbl[tbl["Close"].notna()]

    sort_col = st.selectbox("Sort by", metrics.NUMERIC_COLS, index=0)
    tbl = tbl.sort_values(sort_col, ascending=False)

    st.markdown(f"## {choice} — {len(tbl)} stocks")
    _, shown = render_table(tbl, ["Symbol", "Company", "Industry"],
                            "tbl_members", height=720, scales=scales)
    download(shown, f"{choice.replace(' ', '_').lower()}_members.csv", "dl_mem")

    if no_price:
        st.markdown(
            "<div class='flag'>No price on the latest close for: "
            + ", ".join(no_price[:25])
            + ("…" if len(no_price) > 25 else "")
            + ". Usually a suspended, renamed or newly listed symbol.</div>",
            unsafe_allow_html=True,
        )

# --- page 3 -----------------------------------------------------------------
else:
    st.markdown("## Compare")
    c1, c2 = st.columns(2)
    with c1:
        pick_idx = st.multiselect(
            "Indices and sectors", imx["Index"].tolist(),
            default=["Nifty 50", "Nifty Bank", "Nifty IT"],
        )
    with c2:
        pick_stk = st.multiselect("Stocks (all NSE equity)",
                                  smx["Symbol"].tolist(), default=[])

    expand = st.multiselect(
        "Also pull in every constituent of",
        sorted(cons["index_name"].unique()) if not cons.empty else [],
        default=[],
    )
    if expand:
        extra = cons[cons["index_name"].isin(expand)]["symbol"].unique().tolist()
        pick_stk = sorted(set(pick_stk) | set(extra))

    rows = []
    if pick_idx:
        a = imx[imx["Index"].isin(pick_idx)].copy()
        a.insert(0, "Name", a["Index"])
        a.insert(1, "Type", "Index")
        rows.append(a.drop(columns=["Index"]))
    if pick_stk:
        b = smx[smx["Symbol"].isin(pick_stk)].copy()
        b = b.merge(
            cons.drop_duplicates("symbol")[["symbol", "company"]],
            left_on="Symbol", right_on="symbol", how="left",
        )
        b.insert(0, "Name", b["Symbol"])
        b.insert(1, "Type", "Stock")
        b = b.rename(columns={"company": "Company"}).drop(
            columns=["Symbol", "symbol"], errors="ignore"
        )
        rows.append(b)

    if not rows:
        st.info("Pick at least one index or stock.")
        st.stop()

    comp = pd.concat(rows, ignore_index=True)
    sort_col = st.selectbox("Sort by", metrics.NUMERIC_COLS, index=0,
                            key="cmp_sort")
    comp = comp.sort_values(sort_col, ascending=False)

    st.caption(f"{len(comp)} rows")
    _, shown = render_table(comp, ["Name", "Type", "Company"], "tbl_cmp",
                            height=720, scales=scales)
    download(shown, "nse_comparison.csv", "dl_cmp")

partial = imx["Partial 52W"].any() if "Partial 52W" in imx.columns else False
if partial:
    st.markdown(
        "<div class='flag'>Some rows have under 200 trading days of history, so "
        "their 52-week range covers less than a full year. Widen the backfill "
        "window to fix.</div>",
        unsafe_allow_html=True,
    )
