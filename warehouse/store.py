"""Parquet datasets: one folder per dataset, one file per month (sets/<name>/YYYY-MM.parquet).

upsert() merges new rows into the month files and drops duplicate keys (the new row wins), so every
loader can be re-run safely. Files are written to a temp name and renamed, so a crash never leaves
half a file.
"""
import json
import os

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from . import config


def set_dir(name):
    return os.path.join(config.SETS, name)


def month_files(name):
    d = set_dir(name)
    if not os.path.isdir(d):
        return []
    return sorted(os.path.join(d, f) for f in os.listdir(d) if f.endswith(".parquet"))


def glob(name):
    return os.path.join(set_dir(name), "*.parquet").replace("\\", "/")


def upsert(name, df, keys, date_col="date"):
    """Merge df into the dataset's month files. date_col holds datetime.date or Timestamp values."""
    if df is None or df.empty:
        return 0
    d = set_dir(name)
    os.makedirs(d, exist_ok=True)
    df = df.copy()
    month = pd.to_datetime(df[date_col]).dt.strftime("%Y-%m")
    n = 0
    for m, part in df.groupby(month, sort=True):
        path = os.path.join(d, m + ".parquet")
        part = part.drop(columns=[c for c in part.columns if c.startswith("__")], errors="ignore")
        if os.path.exists(path):
            old = pq.read_table(path).to_pandas()
            part = pd.concat([old, part], ignore_index=True)
        part = part.drop_duplicates(subset=keys, keep="last").sort_values(keys).reset_index(drop=True)
        tmp = path + ".tmp"
        pq.write_table(pa.Table.from_pandas(part, preserve_index=False), tmp, compression="zstd")
        os.replace(tmp, path)
        n += len(part)
    return n


def write_table(name, df):
    """A small reference table that is rebuilt whole each time (sets/<name>/all.parquet)."""
    d = set_dir(name)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "all.parquet")
    tmp = path + ".tmp"
    pq.write_table(pa.Table.from_pandas(df.reset_index(drop=True), preserve_index=False), tmp, compression="zstd")
    os.replace(tmp, path)
    return len(df)


def present_dates(name, date_col="date"):
    """Every date the dataset already holds (reads only that column)."""
    out = set()
    for f in month_files(name):
        col = pq.read_table(f, columns=[date_col]).column(0).to_pylist()
        out.update(x if not hasattr(x, "date") else x.date() for x in col)
    return out


# ---------------------------------------------------------------- state

def load_state():
    try:
        with open(config.STATE, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_state(st):
    os.makedirs(os.path.dirname(config.STATE), exist_ok=True)
    tmp = config.STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(st, fh, indent=1, sort_keys=True)
    os.replace(tmp, config.STATE)


def closed_days(kind):
    return set(load_state().get("closed", {}).get(kind, []))


def mark_closed(kind, days):
    if not days:
        return
    st = load_state()
    cl = st.setdefault("closed", {})
    cl[kind] = sorted(set(cl.get(kind, [])) | {str(d) for d in days})
    save_state(st)
