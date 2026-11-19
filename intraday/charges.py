"""Zerodha intraday (MIS) equity charges as a fraction of the traded value, round trip.

Brokerage 0.03% or Rs 20 per executed order, whichever is lower; STT 0.025% on the sell side;
NSE transaction charge 0.00297%; SEBI Rs 10 a crore; stamp duty 0.003% on the buy side; GST 18% on
brokerage + exchange + SEBI. Slippage is separate (per side, as a fraction).
"""

BROKERAGE_PCT = 0.0003
BROKERAGE_CAP = 20.0
STT_SELL = 0.00025
EXCHANGE = 0.0000297
SEBI = 0.000001
STAMP_BUY = 0.00003
GST = 0.18


def round_trip(notional=50000.0, slippage=0.0005):
    """Cost of buying and selling `notional` rupees once (long or short), as a fraction of notional."""
    brk = 2 * min(BROKERAGE_CAP, BROKERAGE_PCT * notional)
    exch = 2 * EXCHANGE * notional
    sebi = 2 * SEBI * notional
    tax = STT_SELL * notional + STAMP_BUY * notional + GST * (brk + exch + sebi)
    return (brk + exch + sebi + tax) / notional + 2 * slippage
