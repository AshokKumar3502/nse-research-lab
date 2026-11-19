"""F&O end-of-day bhavcopy and participant-wise open interest.

F&O bhavcopy (nsearchives):
  to 2024-07-05:   content/historical/DERIVATIVES/YYYY/MON/foDDMONYYYYbhav.csv.zip   (old format)
  from 2024-07-08: content/fo/BhavCopy_NSE_FO_0_0_0_YYYYMMDD_F_0000.csv.zip           (UDiFF)
Both are normalised to one shape. Futures are kept whole; options keep the nearest
OPTION_EXPIRIES_KEPT expiries of each underlying (all strikes), which covers PCR, OI walls and IV
work at a fraction of the size. Raw zips are not kept.

Units: open interest and change in OI are in shares in both formats. `contracts` (old format) and
`qty` (UDiFF TtlTradgVol) are kept apart because the two files count volume differently; `value_rs`
is rupees in both (old VAL_INLAKH x 1e5).

Participant OI: content/nsccl/fao_participant_oi_DDMMYYYY.csv: open contracts held by Client, DII,
FII and Pro in index/stock futures and options.
"""
import csv
import datetime
import gzip
import io
import os
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from .. import config, net, store

OLD_URL = config.ARCH + "content/historical/DERIVATIVES/%d/%s/fo%02d%s%dbhav.csv.zip"
UDIFF_URL = config.ARCH + "content/fo/BhavCopy_NSE_FO_0_0_0_%s_F_0000.csv.zip"
POI_URL = config.ARCH + "content/nsccl/fao_participant_oi_%s.csv"
UDIFF_TYPES = {"IDF": "FUTIDX", "STF": "FUTSTK", "IDO": "OPTIDX", "STO": "OPTSTK"}
COLS = ["date", "instrument", "symbol", "expiry", "strike", "option_type", "open", "high", "low", "close",
        "settle", "underlying", "oi", "chg_oi", "contracts", "qty", "value_rs", "lot_size"]


def _num(x):
    try:
        return float(str(x).strip().replace(",", ""))
    except (TypeError, ValueError):
        return None


def _date(s, fmts=("%Y-%m-%d", "%d-%b-%Y", "%d-%b-%y")):
    s = (s or "").strip()
    for f in fmts:
        try:
            return datetime.datetime.strptime(s, f).date()
        except ValueError:
            continue
    return None


def fo_url(day):
    if day >= config.UDIFF_FO_FROM:
        return UDIFF_URL % day.strftime("%Y%m%d")
    mon = day.strftime("%b").upper()
    return OLD_URL % (day.year, mon, day.day, mon, day.year)


def parse_fo(text, day):
    """Either F&O bhavcopy format -> list of tuples in COLS order."""
    rd = csv.DictReader(io.StringIO(text))
    rd.fieldnames = [h.strip() for h in (rd.fieldnames or [])]
    out = []
    if "TckrSymb" in rd.fieldnames:
        for r in rd:
            ins = UDIFF_TYPES.get((r.get("FinInstrmTp") or "").strip())
            if not ins:
                continue
            opt = (r.get("OptnTp") or "").strip() or None
            out.append((day, ins, r["TckrSymb"].strip().upper(), _date(r.get("XpryDt")),
                        _num(r.get("StrkPric")) if opt else None, opt,
                        _num(r.get("OpnPric")), _num(r.get("HghPric")), _num(r.get("LwPric")), _num(r.get("ClsPric")),
                        _num(r.get("SttlmPric")), _num(r.get("UndrlygPric")), _num(r.get("OpnIntrst")),
                        _num(r.get("ChngInOpnIntrst")), None, _num(r.get("TtlTradgVol")), _num(r.get("TtlTrfVal")),
                        _num(r.get("NewBrdLotQty"))))
    elif "INSTRUMENT" in rd.fieldnames:
        for r in rd:
            ins = (r.get("INSTRUMENT") or "").strip().upper()
            if ins not in ("FUTIDX", "FUTSTK", "OPTIDX", "OPTSTK"):
                continue
            opt = (r.get("OPTION_TYP") or "").strip().upper()
            opt = opt if opt in ("CE", "PE") else None
            val = _num(r.get("VAL_INLAKH"))
            out.append((day, ins, r["SYMBOL"].strip().upper(), _date(r.get("EXPIRY_DT")),
                        _num(r.get("STRIKE_PR")) if opt else None, opt,
                        _num(r.get("OPEN")), _num(r.get("HIGH")), _num(r.get("LOW")), _num(r.get("CLOSE")),
                        _num(r.get("SETTLE_PR")), None, _num(r.get("OPEN_INT")), _num(r.get("CHG_IN_OI")),
                        _num(r.get("CONTRACTS")), None, val * 1e5 if val is not None else None, None))
    else:
        raise ValueError("not an NSE F&O bhavcopy")
    return out


def split_fo(rows, keep=config.OPTION_EXPIRIES_KEPT):
    """-> (futures DataFrame, options DataFrame with the nearest `keep` expiries per underlying)."""
    df = pd.DataFrame(rows, columns=COLS)
    fut = df[df.instrument.str.startswith("FUT")].drop(columns=["strike", "option_type"])
    opt = df[df.instrument.str.startswith("OPT")]
    if not opt.empty:
        exp = opt[["symbol", "expiry"]].drop_duplicates().sort_values(["symbol", "expiry"])
        exp["n"] = exp.groupby("symbol").cumcount()
        near = exp[exp.n < keep][["symbol", "expiry"]]
        opt = opt.merge(near, on=["symbol", "expiry"], how="inner")
    return fut, opt


def _fetch_fo(day):
    try:
        return day, parse_fo(net.text(net.get(fo_url(day))), day)
    except net.NotPublished:
        return day, None


def load_fo(since, workers=4, log=print):
    """Every trading day in eq_daily from `since` that fo_futures lacks."""
    trading = store.present_dates("eq_daily")
    have = store.present_dates("fo_futures") | {datetime.date.fromisoformat(x) for x in store.closed_days("fo")}
    todo = sorted(d for d in trading if d >= since and d not in have)
    if not todo:
        log("fo: up to date")
        return 0
    log("fo: %d day(s) to download (about 1 MB each)" % len(todo))
    futs, opts, missing, n = [], [], [], 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, (day, rows) in enumerate(ex.map(_fetch_fo, todo)):
            if rows is None:
                missing.append(day)
            else:
                f, o = split_fo(rows)
                futs.append(f)
                opts.append(o)
                n += 1
            if len(futs) >= 20 or i == len(todo) - 1:
                if futs:
                    store.upsert("fo_futures", pd.concat(futs), ["date", "symbol", "instrument", "expiry"])
                    store.upsert("fo_options", pd.concat(opts),
                                 ["date", "symbol", "instrument", "expiry", "strike", "option_type"])
                futs, opts = [], []
                log("  fo: %d of %d days (through %s)" % (i + 1, len(todo), day))
    store.mark_closed("fo", [d for d in missing if (config.today_ist() - d).days > 5])
    if missing:
        log("  fo: NSE had no file for %d day(s), e.g. %s" % (len(missing), missing[0]))
    return n


# ---------------------------------------------------------------- participant OI

POI_FIELDS = ["fut_idx_long", "fut_idx_short", "fut_stk_long", "fut_stk_short", "opt_idx_call_long",
              "opt_idx_put_long", "opt_idx_call_short", "opt_idx_put_short", "opt_stk_call_long",
              "opt_stk_put_long", "opt_stk_call_short", "opt_stk_put_short", "total_long", "total_short"]


def parse_poi(text, day):
    out = []
    text = text.replace("\r\n", "\n").replace("\r", "\n")     # some older files end lines with CR only
    for r in csv.reader(io.StringIO(text)):
        if len(r) < 15:
            continue
        who = r[0].strip().strip('"')
        if who.upper() not in ("CLIENT", "DII", "FII", "PRO", "TOTAL"):
            continue
        vals = [_num(x) for x in r[1:15]]
        out.append((day, who.upper()) + tuple(vals))
    if not out:
        raise ValueError("not a participant OI file")
    return out


def _local_poi(day):
    name = "fao_participant_oi_%s.csv" % day.strftime("%d%m%Y")
    for folder in (os.path.join(config.SOURCE, "flows"), os.path.join(config.RAW, "participant_oi")):
        for p in (os.path.join(folder, name + ".gz"), os.path.join(folder, name)):
            if os.path.exists(p):
                op = gzip.open if p.endswith(".gz") else open
                with op(p, "rt", encoding="utf-8", errors="replace") as fh:
                    return fh.read()
    return None


def _fetch_poi(day):
    text = _local_poi(day)
    if text is None:
        try:
            text = net.text(net.get(POI_URL % day.strftime("%d%m%Y")))
        except net.NotPublished:
            return day, None
        os.makedirs(os.path.join(config.RAW, "participant_oi"), exist_ok=True)
        with gzip.open(os.path.join(config.RAW, "participant_oi", "%s.csv.gz" % day.isoformat()), "wt",
                       encoding="utf-8") as fh:
            fh.write(text)
    try:
        return day, parse_poi(text, day)
    except (ValueError, csv.Error):
        return day, None


def load_participant_oi(since, workers=4, log=print):
    trading = store.present_dates("eq_daily")
    have = store.present_dates("participant_oi") | {datetime.date.fromisoformat(x)
                                                    for x in store.closed_days("participant_oi")}
    todo = sorted(d for d in trading if d >= since and d not in have)
    if not todo:
        log("participant_oi: up to date")
        return 0
    log("participant_oi: %d day(s) to load" % len(todo))
    rows, missing = [], []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for day, got in ex.map(_fetch_poi, todo):
            if got is None:
                missing.append(day)
            else:
                rows += got
    if rows:
        store.upsert("participant_oi", pd.DataFrame(rows, columns=["date", "participant"] + POI_FIELDS),
                     ["date", "participant"])
    store.mark_closed("participant_oi", [d for d in missing if (config.today_ist() - d).days > 5])
    log("  participant_oi: %d day(s) loaded, %d without a file" % (len(todo) - len(missing), len(missing)))
    return len(todo) - len(missing)
