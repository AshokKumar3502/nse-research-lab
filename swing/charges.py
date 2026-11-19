"""Zerodha delivery (CNC) equity charges as a fraction of the traded value, round trip.

Brokerage 0; STT 0.1% on buy and on sell; NSE transaction 0.00297% a side; SEBI Rs 10 a crore;
stamp duty 0.015% on the buy; GST 18% on exchange + SEBI; DP charge Rs 15.34 (incl. GST) per scrip
on the sell. Slippage is separate, per side.
"""

STT = 0.001
EXCHANGE = 0.0000297
SEBI = 0.000001
STAMP_BUY = 0.00015
GST = 0.18
DP_SELL = 15.34


def cnc_round_trip(notional=100000.0, slippage=0.001):
    exch = 2 * EXCHANGE * notional
    sebi = 2 * SEBI * notional
    total = 2 * STT * notional + STAMP_BUY * notional + exch + sebi + GST * (exch + sebi) + DP_SELL
    return total / notional + 2 * slippage
