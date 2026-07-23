"""利根川 上流ダムの放流量を取得し、濁り放流リスク（1時間の放流増加率）を出す。

放流量の上昇 = 濁った水が下流(前橋/渋川の鮎場)へ向かうサイン。利根川はブログ濁り源が
無いため、これが唯一の濁り precursor。到達時間は距離/流速データが無いので「数時間以内」
の粗い目安に留める（正直に）。データ源: MLIT 水文水質DB DspDamData（EUC-JP）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from . import config

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
TIMEOUT = 20
_STEPS_PER_HOUR = 6         # 10-min rows
_FROM_ZERO = 999.0          # sentinel: dam went from ~0 to releasing (a big surge)

# DspDamData IFRAME row: [date, time, 雨量, 貯水量, 流入量, 放流量, 貯水率]
_RAIN_COL = 2
_DISCHARGE_COL = 5


def _get_euc(url: str) -> str:
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    resp.raise_for_status()
    resp.encoding = "euc_jp"           # DspDamData is EUC-JP, not Shift-JIS
    return resp.text


def _to_float(s: str) -> Optional[float]:
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def parse_dam_rows(iframe_html: str) -> List[Tuple[str, Optional[float], Optional[float]]]:
    """Pure parser: IFRAME html -> [(datetime, rainfall_mmh, discharge_m3s), ...] newest-first."""
    soup = BeautifulSoup(iframe_html, "html.parser")
    out: List[Tuple[str, Optional[float], Optional[float]]] = []
    for tr in soup.find_all("tr"):
        cells = [c.get_text(strip=True) for c in tr.find_all("td")]
        if len(cells) > _DISCHARGE_COL and "/" in cells[0]:   # a data row (date in col 0)
            out.append((f"{cells[0]} {cells[1]}",
                        _to_float(cells[_RAIN_COL]), _to_float(cells[_DISCHARGE_COL])))
    return out


def latest_slope(series: List[Tuple[str, Optional[float], Optional[float]]]) -> Dict[str, Any]:
    """Current discharge/rainfall + fractional 放流量 change over the last hour.

    Tolerant of 欠測 (None) cells: uses the newest VALID discharge as 'now' and the
    first VALID discharge at least ~1h back as 'prev'. If no valid row exists ~1h back
    (short series / all-None), slope is None (unknown), never a fabricated sub-hour rate.
    """
    now = next(((ts, rain, d) for ts, rain, d in series if d is not None), None)
    if now is None:
        return {"discharge": None, "rainfall": None, "slope_pct": None, "observed_at": None}
    ts_now, rain_now, disch_now = now
    prev = next((d for _ts, _rain, d in series[_STEPS_PER_HOUR:] if d is not None), None)
    slope: Optional[float] = None
    if prev is not None:
        if prev > 0:
            slope = (disch_now - prev) / prev
        elif disch_now >= config.DAM_MIN_FLOW_M3S:
            slope = _FROM_ZERO
        else:
            slope = 0.0
    return {"discharge": disch_now, "rainfall": rain_now, "slope_pct": slope, "observed_at": ts_now}


def fetch_dam(name: str, dam_id: str) -> Dict[str, Any]:
    """Fetch one dam's latest discharge + 1h slope. Returns {dam, discharge, rainfall, slope_pct}."""
    base = config.DAM_ENDPOINT.format(id=dam_id)
    iframe = BeautifulSoup(_get_euc(base), "html.parser").find("iframe")
    if iframe is None or not iframe.get("src"):
        return {"dam": name, "discharge": None, "rainfall": None, "slope_pct": None,
                "observed_at": None}
    series = parse_dam_rows(_get_euc(urljoin(base, iframe["src"])))
    return {"dam": name, **latest_slope(series)}
