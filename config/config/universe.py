"""
Universe definition.

DISPLAY names below are what the app shows.
They are matched to NSE's "Index Name" column in ind_close_all_DDMMYYYY.csv
via normalise() + ALIASES. If NSE renames an index, add an alias here --
build_data.py writes data/index_names_seen.csv with every name NSE actually
published, so mismatches are visible instead of silent.
"""

import re

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
