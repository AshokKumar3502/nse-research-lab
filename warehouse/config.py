"""Paths and constants for the NSE research warehouse.

Override with environment variables:
  NSEWH_DATA    where the Parquet datasets and the DuckDB file live   (default: nse-warehouse/data)
  NSEWH_SOURCE  the india-day-trader-skill cache to reuse             (default: ../india-day-trader-skill/trader-in)
"""
import datetime
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("NSEWH_DATA") or os.path.join(ROOT, "data")
SOURCE = os.environ.get("NSEWH_SOURCE") or os.path.join(os.path.dirname(ROOT), "india-day-trader-skill", "trader-in")
SKILLS = os.path.join(os.path.dirname(ROOT), "india-day-trader-skill")

DB_PATH = os.path.join(DATA, "warehouse.duckdb")
RAW = os.path.join(DATA, "raw")            # raw downloads the warehouse keeps (delivery, participant OI)
SETS = os.path.join(DATA, "sets")          # Parquet datasets, one folder each, one file per month
STATE = os.path.join(DATA, "state.json")   # closed days, last sync times

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
ARCH = "https://nsearchives.nseindia.com/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")

UDIFF_FO_FROM = datetime.date(2024, 7, 8)       # NSE switched the F&O bhavcopy to UDiFF
SEC_BHAVDATA_FROM = datetime.date(2019, 8, 23)  # first sec_bhavdata_full on the archive; MTO before
UPSTOX_MINUTE_FROM = datetime.date(2022, 1, 1)  # Upstox's public 1-minute history starts here
OPTION_EXPIRIES_KEPT = 2                        # options: the nearest N expiries per underlying per day


def today_ist():
    return datetime.datetime.now(IST).date()
