"""Zerodha equity-intraday charges, checked against Kite's contract-note calculator."""
def intraday_charges(buy_value, sell_value, exch_rate=0.0000307):
    brok = min(20.0, 0.0003 * buy_value) + min(20.0, 0.0003 * sell_value)
    stt = round(0.00025 * sell_value)
    exch = exch_rate * (buy_value + sell_value)
    sebi = 10e-7 * (buy_value + sell_value)
    stamp = round(0.00003 * buy_value)
    gst = 0.18 * (brok + exch + sebi)
    return brok + stt + exch + sebi + stamp + gst
