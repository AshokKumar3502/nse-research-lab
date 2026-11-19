"""Point-in-time industries: an unlisted (e.g. delisted) stock is assigned by how it moves, and an
assignment only uses data up to the month-end before it is valid."""
import importlib

import duckdb
import numpy as np
import pandas as pd


def test_unlisted_stock_assigned_by_correlation(tmp_path, monkeypatch):
    monkeypatch.setenv("NSEWH_DATA", str(tmp_path / "data"))
    import warehouse.config as config
    importlib.reload(config)
    import warehouse.store as store
    importlib.reload(store)
    import swing.industry_pit as ip
    importlib.reload(ip)

    rng = np.random.default_rng(0)
    days = pd.bdate_range("2023-01-02", periods=300)
    banks, tech = rng.normal(0, 0.01, len(days)), rng.normal(0, 0.01, len(days))
    series = {}
    for i in range(3):
        series["BANK%d" % i] = banks + rng.normal(0, 0.002, len(days))
        series["TECH%d" % i] = tech + rng.normal(0, 0.002, len(days))
    series["GONEBANK"] = banks + rng.normal(0, 0.002, len(days))   # on no list, like a delisted bank
    rows = []
    for s, r in series.items():
        px = 100 * np.cumprod(1 + r)
        rows += [(s, d.date(), "EQ", float(p)) for d, p in zip(days, px)]
    con = duckdb.connect()
    con.register("eq", pd.DataFrame(rows, columns=["sym_id", "date", "series", "adj_close"]))
    con.execute("CREATE TABLE eq_daily AS SELECT * FROM eq")
    lab = pd.DataFrame({"symbol": ["BANK0", "BANK1", "BANK2", "TECH0", "TECH1", "TECH2"],
                        "industry": ["Banks"] * 3 + ["IT"] * 3})
    con.register("lab", lab)
    con.execute("CREATE TABLE industries AS SELECT * FROM lab")

    df = ip.build(con, log=lambda m: None)
    gone = df[df.sym_id == "GONEBANK"]
    assert len(gone) > 0 and (gone.industry == "Banks").all()
    first_valid = pd.Timestamp(df.valid_from.min())
    assert first_valid > days[ip.WINDOW - 1]      # needs a full 120-session window before it
