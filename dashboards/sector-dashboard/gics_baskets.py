"""S&P 500 GICS Level-2 industry-group indices — the top panel of the GS crowding monitor.

Generated from GS_Crowding_Basket_Constituents.xlsx sheet `1_GICS_Constituents`
by scripts/build_gics.py. Do not hand-edit; regenerate instead.

Unlike gs_baskets.py these are EXACT, not reconstructions: SPX*/SPLRC* are
public S&P GICS industry-group indices and the workbook maps all 503 S&P 500
members to the correct one. Constituent counts here match sheet `1b` exactly
for all 21 indices, so these baskets carry no confidence caveat.

Equal-weighted (the real indices are cap-weighted, so absolute levels differ;
the relative-rotation signal is what this dashboard is for).

Symbols are Yahoo-form: BRK.B -> BRK-B, BF.B -> BF-B.
"""

INDICES = {
    "SPLRCCS": {
        "name": "Comm. & Prof. Svcs.",
        "desc": "S&P 500 GICS Industry Group index (Bloomberg ticker) - public, exact membership",
        "proxy": "XLI (services sleeve)",
        "members": [
            "ADP", "BR", "CPRT", "CTAS", "EFX", "LDOS", "PAYX", "ROL",
            "RSG", "VLTO", "VRSK", "WM"
        ],
    },
    "SPLRCDF": {
        "name": "Financial Services",
        "desc": "S&P 500 GICS Industry Group index (Bloomberg ticker) - public, exact membership",
        "proxy": "XLF (ex-banks/insurance) / IAI",
        "members": [
            "AMP", "APO", "ARES", "AXP", "BEN", "BLK", "BNY", "BRK-B",
            "BX", "CBOE", "CME", "COF", "COIN", "CPAY", "FDS", "FIS",
            "FISV", "GPN", "GS", "HOOD", "IBKR", "ICE", "IVZ", "JKHY",
            "KKR", "MA", "MCO", "MS", "MSCI", "NDAQ", "NTRS", "PYPL",
            "RJF", "SCHW", "SPGI", "STT", "SYF", "TROW", "V", "XYZ"
        ],
    },
    "SPLRCFBT": {
        "name": "Food, Beverage & Tobacco",
        "desc": "S&P 500 GICS Industry Group index (Bloomberg ticker) - public, exact membership",
        "proxy": "XLP / PBJ",
        "members": [
            "ADM", "BF-B", "BG", "CAG", "CPB", "GIS", "HRL", "HSY", "KDP",
            "KHC", "KO", "MDLZ", "MKC", "MNST", "MO", "PEP", "PM", "SJM",
            "STZ", "TAP", "TSN"
        ],
    },
    "SPLRCFD": {
        "name": "Cons. Stap. Distribution & Retail",
        "desc": "S&P 500 GICS Industry Group index (Bloomberg ticker) - public, exact membership",
        "proxy": "XLP (staples retail sleeve)",
        "members": [
            "CASY", "COST", "DG", "DLTR", "KR", "SYY", "TGT", "WMT"
        ],
    },
    "SPLRCHC": {
        "name": "Health Care Equipment & Services",
        "desc": "S&P 500 GICS Industry Group index (Bloomberg ticker) - public, exact membership",
        "proxy": "XLV (equip+svcs sleeve) / IHI + IHF",
        "members": [
            "ABT", "ALGN", "BAX", "BDX", "BSX", "CAH", "CI", "CNC", "COO",
            "COR", "CVS", "DGX", "DVA", "DXCM", "ELV", "EW", "GEHC",
            "HCA", "HSIC", "HUM", "IDXX", "ISRG", "LH", "MCK", "MDT",
            "PODD", "RMD", "RVTY", "SOLV", "STE", "SYK", "UHS", "UNH",
            "VEEV", "WST", "ZBH"
        ],
    },
    "SPLRCHCR": {
        "name": "Consumer Services",
        "desc": "S&P 500 GICS Industry Group index (Bloomberg ticker) - public, exact membership",
        "proxy": "XLY (consumer svcs sleeve) / PEJ",
        "members": [
            "ABNB", "BKNG", "CCL", "CMG", "DASH", "DPZ", "DRI", "EXPE",
            "HLT", "LVS", "MAR", "MCD", "MGM", "NCLH", "RCL", "SBUX",
            "WYNN", "YUM"
        ],
    },
    "SPLRCHHPE": {
        "name": "Household & Personal Products",
        "desc": "S&P 500 GICS Industry Group index (Bloomberg ticker) - public, exact membership",
        "proxy": "XLP (HPC sleeve)",
        "members": [
            "CHD", "CL", "CLX", "EL", "KMB", "KVUE", "PG"
        ],
    },
    "SPLRCIC": {
        "name": "Capital Goods",
        "desc": "S&P 500 GICS Industry Group index (Bloomberg ticker) - public, exact membership",
        "proxy": "XLI (cap goods sleeve)",
        "members": [
            "ALLE", "AME", "AOS", "AXON", "BA", "BLDR", "CARR", "CAT",
            "CMI", "DE", "DOV", "EME", "EMR", "ETN", "FAST", "FIX", "FTV",
            "GD", "GE", "GEV", "GNRC", "GWW", "HII", "HON", "HUBB", "HWM",
            "IEX", "IR", "ITW", "J", "JCI", "LHX", "LII", "LMT", "MAS",
            "MMM", "NDSN", "NOC", "OTIS", "PCAR", "PH", "PNR", "PWR",
            "ROK", "RTX", "SNA", "SWK", "TDG", "TT", "TXT", "URI", "VRT",
            "WAB", "XYL"
        ],
    },
    "SPLRCIS": {
        "name": "Software & Services",
        "desc": "S&P 500 GICS Industry Group index (Bloomberg ticker) - public, exact membership",
        "proxy": "IGV + XLK (software sleeve)",
        "members": [
            "ACN", "ADBE", "ADSK", "AKAM", "APP", "CDNS", "CRM", "CRWD",
            "CTSH", "DDOG", "EPAM", "FICO", "FTNT", "GDDY", "GEN", "IBM",
            "INTU", "IT", "MSFT", "NOW", "ORCL", "PANW", "PLTR", "PTC",
            "SNPS", "TRMB", "TYL", "VRSN", "WDAY"
        ],
    },
    "SPLRCLP": {
        "name": "Consumer Durables & Apparel",
        "desc": "S&P 500 GICS Industry Group index (Bloomberg ticker) - public, exact membership",
        "proxy": "XLY (durables sleeve)",
        "members": [
            "DECK", "DHI", "GRMN", "HAS", "LEN", "LULU", "NKE", "NVR",
            "PHM", "RL", "TPR"
        ],
    },
    "SPLRCMA": {
        "name": "Materials",
        "desc": "S&P 500 GICS Industry Group index (Bloomberg ticker) - public, exact membership",
        "proxy": "XLB",
        "members": [
            "ALB", "AMCR", "APD", "AVY", "BALL", "CF", "CRH", "CTVA",
            "DD", "DOW", "ECL", "FCX", "IFF", "IP", "LIN", "LYB", "MLM",
            "MOS", "NEM", "NUE", "PKG", "PPG", "SHW", "STLD", "SW", "VMC"
        ],
    },
    "SPLRCME": {
        "name": "Media & Entertainment",
        "desc": "S&P 500 GICS Industry Group index (Bloomberg ticker) - public, exact membership",
        "proxy": "XLC (media sleeve)",
        "members": [
            "CHTR", "CMCSA", "DIS", "EA", "FOX", "FOXA", "GOOG", "GOOGL",
            "LYV", "META", "NFLX", "NWS", "NWSA", "OMC", "PSKY", "TKO",
            "TTD", "TTWO", "WBD"
        ],
    },
    "SPLRCPHB": {
        "name": "Pharma, Biotech & Life Sciences",
        "desc": "S&P 500 GICS Industry Group index (Bloomberg ticker) - public, exact membership",
        "proxy": "XLV (pharma sleeve) / IHE + IBB",
        "members": [
            "A", "ABBV", "AMGN", "BIIB", "BMY", "CRL", "DHR", "GILD",
            "INCY", "IQV", "JNJ", "LLY", "MRK", "MRNA", "MTD", "PFE",
            "REGN", "TECH", "TMO", "VRTX", "VTRS", "WAT", "ZTS"
        ],
    },
    "SPLRCREC": {
        "name": "Equity REITs",
        "desc": "S&P 500 GICS Industry Group index (Bloomberg ticker) - public, exact membership",
        "proxy": "XLRE",
        "members": [
            "AMT", "ARE", "AVB", "BXP", "CCI", "CPT", "DLR", "DOC",
            "EQIX", "EQR", "ESS", "EXR", "FRT", "HST", "INVH", "IRM",
            "KIM", "MAA", "O", "PLD", "PSA", "REG", "SBAC", "SPG", "UDR",
            "VICI", "VTR", "WELL", "WY"
        ],
    },
    "SPLRCSE": {
        "name": "Semi & Semi Equipment",
        "desc": "S&P 500 GICS Industry Group index (Bloomberg ticker) - public, exact membership",
        "proxy": "SOXX / SMH",
        "members": [
            "ADI", "AMAT", "AMD", "AVGO", "FSLR", "INTC", "KLAC", "LRCX",
            "MCHP", "MPWR", "MU", "NVDA", "NXPI", "ON", "Q", "QCOM",
            "SWKS", "TER", "TXN"
        ],
    },
    "SPLRCTEHW": {
        "name": "Technology Hardware & Equipment",
        "desc": "S&P 500 GICS Industry Group index (Bloomberg ticker) - public, exact membership",
        "proxy": "XLK (hardware sleeve) / IYW",
        "members": [
            "AAPL", "ANET", "APH", "CDW", "CIEN", "COHR", "CSCO", "DELL",
            "FFIV", "GLW", "HPE", "HPQ", "JBL", "KEYS", "LITE", "MSI",
            "NTAP", "ROP", "SMCI", "SNDK", "STX", "TDY", "TEL", "WDC",
            "ZBRA"
        ],
    },
    "SPLRCTRN": {
        "name": "Transportation",
        "desc": "S&P 500 GICS Industry Group index (Bloomberg ticker) - public, exact membership",
        "proxy": "IYT",
        "members": [
            "CHRW", "CSX", "DAL", "EXPD", "FDX", "JBHT", "LUV", "NSC",
            "ODFL", "UAL", "UBER", "UNP", "UPS"
        ],
    },
    "SPLRCU": {
        "name": "Utilities",
        "desc": "S&P 500 GICS Industry Group index (Bloomberg ticker) - public, exact membership",
        "proxy": "XLU",
        "members": [
            "AEE", "AEP", "AES", "ATO", "AWK", "CEG", "CMS", "CNP", "D",
            "DTE", "DUK", "ED", "EIX", "ES", "ETR", "EVRG", "EXC", "FE",
            "LNT", "NEE", "NI", "NRG", "PCG", "PEG", "PNW", "PPL", "SO",
            "SRE", "VST", "WEC", "XEL"
        ],
    },
    "SPNY": {
        "name": "Energy",
        "desc": "S&P 500 GICS Industry Group index (Bloomberg ticker) - public, exact membership",
        "proxy": "XLE",
        "members": [
            "APA", "BKR", "COP", "CVX", "DVN", "EOG", "EQT", "EXE",
            "FANG", "HAL", "KMI", "MPC", "OKE", "OXY", "PSX", "SLB",
            "TPL", "TRGP", "VLO", "WMB", "XOM"
        ],
    },
    "SPXBK": {
        "name": "Banks",
        "desc": "S&P 500 GICS Industry Group index (Bloomberg ticker) - public, exact membership",
        "proxy": "KBWB / XLF (bank sleeve)",
        "members": [
            "BAC", "C", "CFG", "FITB", "HBAN", "JPM", "KEY", "MTB", "PNC",
            "RF", "TFC", "USB", "WFC"
        ],
    },
    "SPXIN": {
        "name": "Insurance",
        "desc": "S&P 500 GICS Industry Group index (Bloomberg ticker) - public, exact membership",
        "proxy": "KIE / IAK",
        "members": [
            "ACGL", "AFL", "AIG", "AIZ", "AJG", "ALL", "AON", "BRO", "CB",
            "CINF", "EG", "ERIE", "GL", "HIG", "L", "MET", "MRSH", "PFG",
            "PGR", "PRU", "TRV", "WRB", "WTW"
        ],
    },
}

DISCLAIMER = (
    "Exact S&P 500 GICS industry-group membership (all 503 members mapped), "
    "equal-weighted rather than cap-weighted."
)


def all_symbols():
    syms = set()
    for b in INDICES.values():
        syms.update(b["members"])
    return sorted(syms)
