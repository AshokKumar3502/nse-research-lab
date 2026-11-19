"""Offline tests on synthetic data (no network). Run: .venv\\Scripts\\python -m pytest -q"""
import datetime
import importlib

import pandas as pd
import pytest

D = datetime.date


@pytest.fixture()
def wh(tmp_path, monkeypatch):
    """The warehouse package pointed at an empty temp data folder."""
    monkeypatch.setenv("NSEWH_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("NSEWH_SOURCE", str(tmp_path / "src"))
    import warehouse.config as config
    importlib.reload(config)
    import warehouse.store as store
    import warehouse.db as db
    import warehouse.quality as quality
    for m in (store, db, quality):
        importlib.reload(m)
    return config, store, db, quality


def test_symbol_map_follows_chains():
    from warehouse.db import symbol_map
    rows = symbol_map([("AAA", "BBB", D(2018, 1, 1)), ("BBB", "CCC", D(2021, 1, 1))])
    m = {(r[0], r[1]): (r[2], r[3]) for r in rows}
    assert m[("AAA", D(1990, 1, 1))] == (D(2018, 1, 1), "CCC")
    assert m[("BBB", D(1990, 1, 1))] == (D(2021, 1, 1), "CCC")


def test_upsert_dedupes_new_row_wins(wh):
    config, store, db, _ = wh
    a = pd.DataFrame({"date": [D(2024, 1, 2), D(2024, 1, 2)], "symbol": ["X", "Y"], "close": [1.0, 2.0]})
    b = pd.DataFrame({"date": [D(2024, 1, 2), D(2024, 2, 1)], "symbol": ["X", "X"], "close": [9.0, 3.0]})
    store.upsert("t", a, ["date", "symbol"])
    store.upsert("t", b, ["date", "symbol"])
    files = store.month_files("t")
    assert [f[-15:] for f in files] == ["2024-01.parquet", "2024-02.parquet"]
    jan = pd.read_parquet(files[0])
    assert len(jan) == 2 and jan.set_index("symbol").close["X"] == 9.0
    assert store.present_dates("t") == {D(2024, 1, 2), D(2024, 2, 1)}


def test_parse_fo_old_and_udiff():
    from warehouse.loaders.fno import parse_fo, split_fo
    old = ("INSTRUMENT,SYMBOL,EXPIRY_DT,STRIKE_PR,OPTION_TYP,OPEN,HIGH,LOW,CLOSE,SETTLE_PR,CONTRACTS,VAL_INLAKH,"
           "OPEN_INT,CHG_IN_OI,TIMESTAMP,\n"
           "FUTSTK,ABC,25-Jan-2023,0,XX,10,11,9,10.5,10.5,100,2.5,5000,50,02-JAN-2023,\n"
           "OPTSTK,ABC,25-Jan-2023,100,CE,1,1,1,1,1,1,0.1,10,0,02-JAN-2023,\n"
           "OPTSTK,ABC,23-Feb-2023,100,CE,1,1,1,1,1,1,0.1,10,0,02-JAN-2023,\n"
           "OPTSTK,ABC,30-Mar-2023,100,PE,1,1,1,1,1,1,0.1,10,0,02-JAN-2023,\n")
    rows = parse_fo(old, D(2023, 1, 2))
    fut, opt = split_fo(rows, keep=2)
    assert len(fut) == 1 and fut.iloc[0].value_rs == pytest.approx(250000) and fut.iloc[0].contracts == 100
    assert sorted(opt.expiry.unique()) == [D(2023, 1, 25), D(2023, 2, 23)]
    udiff = ("TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,FininstrmActlXpryDt,StrkPric,"
             "OptnTp,FinInstrmNm,OpnPric,HghPric,LwPric,ClsPric,LastPric,PrvsClsgPric,UndrlygPric,SttlmPric,OpnIntrst,"
             "ChngInOpnIntrst,TtlTradgVol,TtlTrfVal,TtlNbOfTxsExctd,SsnId,NewBrdLotQty,Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4\n"
             "2026-09-16,2026-09-16,FO,NSE,STF,1,,ABC,,2026-10-27,2026-10-27,,,ABC26OCTFUT,10,11,9,10.5,10.5,10,10.4,"
             "10.5,6200,100,3100,32000,5,F1,3100,,,,,\n"
             "2026-09-16,2026-09-16,FO,NSE,STO,2,,ABC,,2026-10-27,2026-10-27,375.00,CE,X,0,0,0,50,0,50,381,24,0,0,0,"
             "0,0,F1,3100,,,,,\n")
    rows = parse_fo(udiff, D(2026, 9, 16))
    fut, opt = split_fo(rows)
    assert fut.iloc[0].instrument == "FUTSTK" and fut.iloc[0].underlying == 10.4 and fut.iloc[0].lot_size == 3100
    assert opt.iloc[0].strike == 375.0 and opt.iloc[0].option_type == "CE"


def test_parse_delivery_formats():
    from warehouse.loaders.daily import parse_mto, parse_sec_bhavdata
    mto = "Security Wise...\n10,MTO,02012018\nRecord Type,...\n20,1,ABC,EQ,173946,103765,59.65\n"
    assert parse_mto(mto, D(2018, 1, 2)) == [(D(2018, 1, 2), "ABC", "EQ", None, None, 103765.0, 59.65)]
    sec = ("SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, LAST_PRICE, CLOSE_PRICE, AVG_PRICE, "
           "TTL_TRD_QNTY, TURNOVER_LACS, NO_OF_TRADES, DELIV_QTY, DELIV_PER\n"
           "ABC, EQ, 16-Nov-2026, 1, 1, 1, 1, 1, 1, 180.75, 65133, 117.73, 2707, 31071, 47.70\n"
           "XYZ, BE, 16-Nov-2026, 1, 1, 1, 1, 1, 1, 52.25, 7678, 4.01, 66, -, -\n")
    r = parse_sec_bhavdata(sec)
    assert r[0] == (D(2026, 11, 16), "ABC", "EQ", 180.75, 2707.0, 31071.0, 47.7)
    assert r[1][5] is None and r[1][6] is None


def test_parse_participant_oi():
    from warehouse.loaders.fno import parse_poi
    t = ('"Participant wise Open Interest",,,,,,,,,,,,,,\nClient Type,a,b,c,d,e,f,g,h,i,j,k,l,m,n\n'
         "Client,1,2,3,4,5,6,7,8,9,10,11,12,13,14\nFII,1,1,1,1,1,1,1,1,1,1,1,1,1,1\n")
    r = parse_poi(t, D(2026, 11, 2))
    assert [x[1] for x in r] == ["CLIENT", "FII"] and r[0][2] == 1.0 and r[0][-1] == 14.0


def test_build_adjusts_for_split_and_rename(wh):
    config, store, db, quality = wh
    days = [D(2024, 1, 1) + datetime.timedelta(days=i) for i in range(6)]
    rows = []
    for i, d in enumerate(days):
        sym = "OLDCO" if i < 3 else "NEWCO"
        px = 1000.0 if i < 2 else 200.0          # 1:5 split, ex-date day index 2
        rows.append((d, sym, "EQ", px, px, px, px, px, 100.0, px * 100))
    eq = pd.DataFrame(rows, columns=["date", "symbol", "series", "open", "high", "low", "close", "prev_close",
                                     "volume", "turnover"])
    store.upsert("eq_daily", eq, ["date", "symbol"])
    store.write_table("symbol_changes", pd.DataFrame([("OLDCO", "NEWCO", days[3])],
                                                     columns=["old_symbol", "new_symbol", "change_date"]))
    store.write_table("corp_actions", pd.DataFrame([("OLDCO", days[2], 0.2, "split 10 to 2")],
                                                   columns=["symbol", "ex_date", "factor", "subject"]))
    con = db.build(log=lambda m: None)
    got = con.execute("SELECT date, sym_id, adj_close, adj_volume FROM eq_daily ORDER BY date").fetchall()
    assert {r[1] for r in got} == {"NEWCO"}
    assert [round(r[2], 6) for r in got] == [200.0] * 6
    assert got[0][3] == pytest.approx(500.0)
