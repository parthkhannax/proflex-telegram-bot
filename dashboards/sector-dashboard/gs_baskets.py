"""Goldman Sachs crowding-monitor baskets, reconstructed from public sources.

Source: ~/Downloads/GS_Crowding_Basket_Constituents.xlsx (built 25-Aug-2026).

IMPORTANT — these are RECONSTRUCTIONS, not official GS membership. Real
constituent lists for GS*/GSX*/GSTMT*/GSCB* baskets live only on a Bloomberg
terminal (`<ticker> Index` -> MEMB/MEMV) or behind a GS Marquee client login.
Each basket carries the workbook's own confidence grade so the UI can say so.

Constituent lists are hand-curated from the workbook's free-text "known /
likely constituents" column. That column contains editorial annotations that a
naive comma-split would turn into fake tickers -- "ABBV-no", "SWN-merged",
"CTLT-acquired", "RBLX-no(now profitable)", "BOYD-private", "CTAS-style
staffing & BPO", "IPG-type agencies", "SU-utilities:", "GEMI-type",
"SPX-Technologies (SPXC)", "NVMI-no", "ZBH-negative sleeve". Those are
resolved by hand here, not by regex.

Ticker hygiene (validated against the Yahoo chart API on 25-Aug-2026): every
symbol below resolves and returns a live price. Twelve names in the workbook
404 on Yahoo and were dropped as delisted/merged -- BITF, CFLT, CMA, CTRA, IPG,
MAXN, SMLR, SNV, TERN, TPIC, WNS, X. Their bankruptcy-stub successors (TPICQ at
$0.0065, MAXNQ at $0.02) were deliberately NOT substituted: a sub-cent stub in
an equal-weighted basket swamps every other constituent. ABB was replaced by
its live US ADR line, ABBNY. Re-run scripts/validate_tickers.py after any edit.

Fields per basket:
  name        display name from the workbook's "Category / Name" column
  desc        what the basket actually is
  proxy       best tradeable ETF proxy (used directly when dynamic=True)
  confidence  workbook's own confidence in the reconstruction
  members     equal-weighted constituents (empty when dynamic)
  weights     optional {ticker: weight}; when present, basket is weight-weighted
  dynamic     True when the real basket rebalances too fast to list statically,
              so the proxy ETF is tracked instead of a constituent list
  note        caveat surfaced in the UI
"""

# Published GVIP top-25 (of ~52) holdings, weights as of 2-Jul-2026.
# These sum to ~0.552, not 1.0 -- the remaining ~27 names are paywalled.
# Renormalised at load so the basket is a clean weighted average of what we have.
_GVIP_RAW = {
    "MRVL": 0.0264, "AMAT": 0.0263, "MU": 0.0257, "RVMD": 0.0248, "ABVX": 0.0241,
    "SNDK": 0.0236, "LRCX": 0.0235, "AMD": 0.0233, "LLY": 0.0230, "DASH": 0.0230,
    "ASML": 0.0225, "MELI": 0.0217, "JPM": 0.0213, "NU": 0.0212, "V": 0.0211,
    "INTC": 0.0211, "COF": 0.0208, "MA": 0.0207, "SPGI": 0.0207, "TLN": 0.0207,
    "VST": 0.0205, "UNH": 0.0205, "TSM": 0.0205, "GEV": 0.0202, "APP": 0.0200,
}
_GVIP_SUM = sum(_GVIP_RAW.values())
GVIP_WEIGHTS = {k: v / _GVIP_SUM for k, v in _GVIP_RAW.items()}

BASKETS = {
    "GSX1MEGA": {
        "name": "Mag7",
        "desc": "The seven US mega-cap technology leaders.",
        "proxy": "MAGS",
        "confidence": "High - exact",
        "members": ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA"],
    },
    "GSTMTAIP": {
        "name": "Broad AI",
        "desc": "GS flagship US TMT AI basket - the full AI value chain.",
        "proxy": "AIQ",
        "confidence": "Medium",
        "members": [
            "NVDA", "AVGO", "AMD", "MU", "MRVL", "TSM", "ASML", "AMAT", "LRCX",
            "KLAC", "ARM", "MSFT", "GOOGL", "META", "AMZN", "ORCL", "NOW",
            "PLTR", "SNOW", "CRM", "ANET", "DELL", "SMCI", "VRT", "CRWV",
        ],
    },
    "GSTMTDAT": {
        "name": "AI Data Centers",
        "desc": "Data-centre build-out: power, cooling, electricals, REITs.",
        "proxy": "DTCR",
        "confidence": "Medium",
        "members": [
            "VRT", "ETN", "GEV", "PWR", "EMR", "HUBB", "NVT", "MOD", "AAON",
            "CARR", "JCI", "TT", "SMCI", "DELL", "ANET", "CIEN", "LITE", "COHR",
            "CEG", "VST", "TLN", "NRG", "EQIX", "DLR", "IRM", "SRE", "NEE",
        ],
    },
    "GSXUCOOL": {
        "name": "Liquid Cooling Infra",
        "desc": "Thermal-management supply chain for high-density compute.",
        "proxy": "DTCR",
        "confidence": "Medium",
        "members": [
            "VRT", "MOD", "AAON", "TT", "CARR", "JCI", "LII", "SPXC", "NDSN",
            "PH", "FLS", "IEX", "MKSI", "THRM",
        ],
        "note": "Excludes private/foreign-listed names in the workbook (Boyd, Asetek).",
    },
    "GSTMTAIR": {
        "name": "AI At Risk",
        "desc": "Services, staffing and agency models seen as AI-disrupted. "
                "Traded as the short leg of a GS AI long/short pair.",
        "proxy": "None - short leg of GSXUAILE pair",
        "confidence": "Low - illustrative",
        "members": [
            "ACN", "IT", "EPAM", "CTSH", "INFY", "TTEC", "CHGG", "COUR",
            "UPWK", "FVRR", "RHI", "MAN", "KFY", "ADBE", "PSKY", "OMC", ],
        "note": "Most speculative list in the workbook - treat direction, not level.",
    },
    "GSCBASMC": {
        "name": "Analog Semis",
        "desc": "Analog and mixed-signal semiconductor makers.",
        "proxy": "SOXX",
        "confidence": "Medium-High",
        "members": [
            "TXN", "ADI", "MCHP", "NXPI", "ON", "STM", "IFNNY", "MPWR", "SWKS",
            "QRVO", "SLAB", "DIOD", "ALGM", "LSCC", "AOSL", "POWI",
        ],
    },
    "GSCBBTC1": {
        "name": "Bitcoin Sensitive Eq",
        "desc": "Equities whose returns track bitcoin - treasuries, miners, brokers.",
        "proxy": "BITQ",
        "confidence": "Medium-High",
        "members": [
            "MSTR", "COIN", "MARA", "RIOT", "CLSK", "HUT", "CORZ", "IREN",
            "WULF", "CIFR", "HOOD", "GLXY", "BTBT", "CME",
        ],
    },
    "GSXUCADR": {
        "name": "China ADRs",
        "desc": "US-listed China ADRs.",
        "proxy": "KWEB",
        "confidence": "High",
        "members": [
            "BABA", "PDD", "JD", "BIDU", "NTES", "TCOM", "BEKE", "LI", "XPEV",
            "NIO", "TME", "YUMC", "ZTO", "BILI", "IQ", "VIPS", "FUTU", "TAL",
            "EDU", "ATHM",
        ],
    },
    "GSXUDFNS": {
        "name": "Defense",
        "desc": "US defense primes and next-gen defense tech.",
        "proxy": "ITA",
        "confidence": "High",
        "members": [
            "LMT", "RTX", "NOC", "GD", "LHX", "HII", "BA", "TDG", "HWM", "AXON",
            "LDOS", "KTOS", "AVAV", "CW", "MOG-A", "TXT", "SAIC", "BAH", "PLTR",
            "RKLB",
        ],
    },
    "GSX1SF8X": {
        "name": "Expensive Software",
        "desc": "High-multiple software (workbook shorthand: >8x EV/sales).",
        "proxy": "IGV",
        "confidence": "Medium",
        "members": [
            "CRWD", "DDOG", "SNOW", "NET", "ZS", "MDB", "PLTR", "HUBS", "TEAM",
            "SNPS", "CDNS", "VEEV", "TTD", "RBRK", "S", "GTLB", "ESTC",
            "TOST", "APP",
        ],
    },
    "GSXUGRID": {
        "name": "Grid Revamp",
        "desc": "Electrical grid build-out: equipment, engineering and utilities.",
        "proxy": "GRID",
        "confidence": "Medium-High",
        "members": [
            "ETN", "GEV", "PWR", "EMR", "HUBB", "NVT", "AME", "ROK", "PNR",
            "MYRG", "PRIM", "DY", "ATKR", "ENS", "VRT", "ABBNY", "SIEGY",
            "NEE", "AEP", "D", "XEL", "ED", "WEC",
        ],
    },
    "GVIPTR": {
        "name": "HF VIP Index",
        "desc": "Goldman's Hedge Fund VIP list - the most common top-10 holdings "
                "of fundamentally-driven hedge funds.",
        "proxy": "GVIP",
        "confidence": "High",
        "members": list(GVIP_WEIGHTS.keys()),
        "weights": GVIP_WEIGHTS,
        "note": "Published top 25 of ~52 holdings (2-Jul-2026); weights renormalised. "
                "The remaining names are paywalled.",
    },
    "GSXUHMOM": {
        "name": "High Momentum (Winners)",
        "desc": "GS momentum-winners basket.",
        "proxy": "MTUM",
        "confidence": "Low - dynamic",
        "members": [],
        "dynamic": True,
        "note": "Rebalances too fast to list statically - tracked via MTUM as live proxy.",
    },
    "GSCBRGBK": {
        "name": "Liquid Regional Banks",
        "desc": "The liquid end of the US regional banking complex.",
        "proxy": "KRE",
        "confidence": "High",
        "members": [
            "PNC", "USB", "TFC", "FITB", "CFG", "KEY", "RF", "HBAN", "MTB",
            "ZION", "WAL", "EWBC", "FHN", "PB", "WBS", "VLY",
            "CFR", "ONB", "BOKF", "PNFP", "SSB", "UMBF",
        ],
    },
    "GSXUMEME": {
        "name": "Memes",
        "desc": "High-retail-attention, high-short-interest names.",
        "proxy": "BUZZ",
        "confidence": "Low - rotates fast",
        "members": [
            "GME", "AMC", "BBAI", "SOUN", "RGTI", "IONQ", "QUBT", "OPEN",
            "LCID", "PLUG", "RIVN", "HOOD", "DJT", "MARA", "RIOT", "TLRY",
            "WOLF", "CHPT", "SMR", "ACHR", "JOBY",
        ],
    },
    "GSXUMETL": {
        "name": "Metals",
        "desc": "Industrial and precious metals miners and processors.",
        "proxy": "XME",
        "confidence": "Medium-High",
        "members": [
            "FCX", "NEM", "NUE", "STLD", "CLF", "AA", "MP", "RIO", "BHP",
            "VALE", "SCCO", "TECK", "HBM", "AGI", "KGC", "WPM", "FNV", "RGLD",
            "HL", "CDE", "UEC",
        ],
    },
    "GSENNATG": {
        "name": "Nat Gas",
        "desc": "Natural gas producers, midstream and gas-levered power.",
        "proxy": "FCG",
        "confidence": "High",
        "members": [
            "EQT", "EXE", "AR", "RRC", "CNX", "GPOR", "CRK", "KMI",
            "WMB", "OKE", "TRGP", "LNG", "NFE", "VST", "TLN",
        ],
    },
    "GSX1NPTC": {
        "name": "Non-Profitable Tech",
        "desc": "Rotating screen of negative-EPS technology.",
        "proxy": "ARKK",
        "confidence": "Low - dynamic",
        "members": [
            "IONQ", "RGTI", "SOUN", "BBAI", "RXRX", "TEM", "SERV", "AUR",
            "CHPT", "PLUG", "LCID", "RIVN", "JOBY", "ACHR", "OPEN", "WRBY",
            "LMND", "ASTS", "RKLB",
        ],
        "note": "Screen-based and rotates; workbook's 'typical members' only.",
    },
    "GSHLCBMI": {
        "name": "Obesity Drugs",
        "desc": "GLP-1 makers plus the cardiometabolic supply chain.",
        "proxy": "THNR",
        "confidence": "Medium",
        "members": [
            "LLY", "NVO", "VKTX", "ALT", "STVN", "HIMS", "WST", "BDX",
            "TMO", "DHR", "REGN", "AMGN", "PFE", "RHHBY",
        ],
        "note": "Long sleeve only. The workbook's food/retail short sleeve "
                "(HSY, KHC, GIS, DPZ, PEP) is excluded.",
    },
    "GSXURNEW": {
        "name": "Renewables",
        "desc": "Solar, storage and renewable power generation.",
        "proxy": "ICLN",
        "confidence": "Medium-High",
        "members": [
            "FSLR", "ENPH", "SEDG", "RUN", "NXT", "ARRY", "SHLS", "GEV",
            "AMRC", "BE", "PLUG", "BEP", "NEE", "CWEN", "ORA", "CSIQ", "JKS",
        ],
    },
    "GSXURFAV": {
        "name": "Retail Favorites",
        "desc": "Names with the heaviest retail ownership and attention.",
        "proxy": "BUZZ",
        "confidence": "Medium",
        "members": [
            "TSLA", "NVDA", "PLTR", "AMD", "AAPL", "AMZN", "SOFI", "HOOD",
            "RIVN", "LCID", "GME", "AMC", "MSTR", "COIN", "INTC", "F", "NIO",
            "SMCI", "IONQ", "RKLB", "ACHR", "OPEN",
        ],
    },
}

DISCLAIMER = (
    "Reconstructed from public sources, not official Goldman Sachs basket membership. "
    "Real constituents are only on a Bloomberg terminal (the ticker + ' Index' -> MEMB) "
    "or on GS Marquee."
)


def all_symbols():
    """Every symbol the GS tab needs quotes for: members + proxies."""
    syms = set()
    for b in BASKETS.values():
        syms.update(b["members"])
        if b.get("dynamic") and b.get("proxy"):
            syms.add(b["proxy"])
    return sorted(syms)
