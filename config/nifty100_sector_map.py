"""Owner-frozen V1 grouping. Keep old versions available for historical reports."""
from collections import Counter
from config.nifty100_symbols import NIFTY_100_SYMBOLS, NIFTY_100_NSE_SOURCE_DATE

SECTOR_MAP_VERSION = "owner-v1-2026-09-08"
SECTOR_MAP_V1 = (
    ("Automobile and Auto Components", "MARUTI M&M BAJAJ-AUTO EICHERMOT TVSMOTOR HYUNDAI MOTHERSON BOSCHLTD TMPV"),
    ("Capital Goods", "HAL BEL TMCV ABB SIEMENS CGPOWER CUMMINSIND ENRIN MAZDOCK"),
    ("Chemicals", "SOLARINDS PIDILITIND"),
    ("Construction", "LT"),
    ("Construction Materials", "ULTRACEMCO GRASIM AMBUJACEM SHREECEM"),
    ("Consumer Durables", "TITAN ASIANPAINT"),
    ("Consumer Services", "ETERNAL DMART TRENT INDHOTEL"),
    ("Fast Moving Consumer Goods", "HINDUNILVR ITC NESTLEIND VBL BRITANNIA UNITDSPR TATACONSUM GODREJCP"),
    ("Financial Services", "HDFCBANK ICICIBANK SBIN BAJFINANCE KOTAKBANK AXISBANK BAJAJFINSV SHRIRAMFIN SBILIFE TATACAP JIOFIN CHOLAFIN UNIONBANK PNB BAJAJHLDNG BANKBARODA HDFCLIFE PFC MUTHOOTFIN CANBK IRFC HDFCAMC RECLTD"),
    ("Healthcare", "SUNPHARMA DIVISLAB TORNTPHARM APOLLOHOSP CIPLA ZYDUSLIFE DRREDDY MAXHEALTH"),
    ("Information Technology", "TCS INFY HCLTECH WIPRO TECHM LTM"),
    ("Metals & Mining", "ADANIENT JSWSTEEL HINDZINC TATASTEEL HINDALCO JINDALSTEL VEDL"),
    ("Oil Gas & Consumable Fuels", "RELIANCE ONGC COALINDIA IOC BPCL GAIL"),
    ("Power", "ADANIPOWER NTPC POWERGRID ADANIGREEN ADANIENSOL TATAPOWER"),
    ("Realty", "DLF LODHA"),
    ("Services", "ADANIPORTS INDIGO"),
    ("Telecommunication", "BHARTIARTL"),
)
SECTOR_MAP_VERSIONS = {SECTOR_MAP_VERSION: SECTOR_MAP_V1}


def sector_map_payload(*, universe=None, sectors=None):
    universe = NIFTY_100_SYMBOLS if universe is None else universe
    sectors = SECTOR_MAP_V1 if sectors is None else sectors
    groups = [{"name": name, "symbols": symbols.split()} for name, symbols in sectors]
    counts = Counter(symbol for group in groups for symbol in group["symbols"])
    duplicates = sorted(symbol for symbol, count in counts.items() if count != 1)
    missing, unexpected = sorted(set(universe)-set(counts)), sorted(set(counts)-set(universe))
    valid = (len(groups) == 17 and len(counts) == 100 and len(universe) == 100
             and len(set(universe)) == 100 and not duplicates and not missing and not unexpected)
    reason = None if valid else f"Sector map mismatch: missing={missing}; unexpected={unexpected}; duplicates={duplicates}; sectors={len(groups)}; unique_symbols={len(counts)}"
    return {"version": SECTOR_MAP_VERSION, "universe_version": NIFTY_100_NSE_SOURCE_DATE,
            "valid": valid, "reason": reason, "sectors": groups, "symbol_count": len(counts)}
