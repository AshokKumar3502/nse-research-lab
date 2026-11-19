"""HTTP with retries for NSE's archive and Upstox's public candle API."""
import gzip
import io
import json
import time
import urllib.error
import urllib.request
import zipfile

from . import config


class NotPublished(Exception):
    """The file does not exist (holiday, not yet published, or before the archive starts)."""


def get(url, timeout=90, tries=3, headers=None):
    h = {"User-Agent": config.UA, "Accept": "*/*"}
    h.update(headers or {})
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
            if raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
            return raw
        except urllib.error.HTTPError as e:
            code = e.code
            e.close()
            if code in (403, 404):
                raise NotPublished("HTTP %d %s" % (code, url))
            last = "HTTP %d" % code
            if code == 429:
                time.sleep(5 * (attempt + 1))
                continue
        except (urllib.error.URLError, OSError) as e:
            last = str(getattr(e, "reason", e))
        time.sleep(1.5 * (attempt + 1))
    raise IOError("%s: %s" % (url, last))


def text(raw):
    """Zip or plain bytes -> text; NSE's 'page not found' HTML counts as not published."""
    if raw[:2] == b"PK":
        z = zipfile.ZipFile(io.BytesIO(raw))
        raw = z.read(z.namelist()[0])
    t = raw.decode("utf-8", "replace")
    if t.lstrip()[:1] == "<":
        raise NotPublished("NSE answered a web page, not a file")
    return t


def get_json(url, timeout=60, tries=4):
    return json.loads(get(url, timeout, tries, {"Accept": "application/json"}).decode("utf-8"))
