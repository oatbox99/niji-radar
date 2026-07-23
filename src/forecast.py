"""気象庁 週間予報 → 今後7日の TSI 投影入力。

予報ベースの推定（未来は不確実）。天気コード→日照時間は粗いマッピングで、UIでは常に
「予報」と明示する。降水予報の強雨日は投影上の増水リセット候補として扱う。max_temp は
C&R の午後クローズ(hoot owl)判定に使う。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests

from . import config

FORECAST_URL = "https://www.jma.go.jp/bosai/forecast/data/forecast/{area}.json"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
TIMEOUT = 20


def _sun_from_code(code: str) -> float:
    """Rough daily sunshine hours from a JMA weather code (leading digit = 晴/曇/雨/雪)."""
    c = str(code)
    if c.startswith("1"):                                  # 晴 family
        return 9.0 if c in ("100", "101", "110", "111", "130", "131") else 6.0
    if c.startswith("2"):                                  # 曇 family
        return 4.0 if c in ("201", "210", "211") else 2.5
    if c.startswith("3"):                                  # 雨
        return 0.5
    if c.startswith("4"):                                  # 雪
        return 1.0
    return 3.0


def _weather_text(code: str) -> str:
    c = str(code)
    if c.startswith("1"):
        return "晴れ" if c in ("100", "101") else "晴れ時々曇り"
    if c.startswith("2"):
        return "曇り時々晴れ" if c in ("201", "210") else "曇り"
    if c.startswith("3"):
        return "雨"
    if c.startswith("4"):
        return "雪"
    return "—"


def _is_scour(code: str, pop: Optional[int]) -> bool:
    return str(code).startswith("3") and (pop or 0) >= 50   # forecast heavy rain → 垢 reset


def _to_int(s: Any) -> Optional[int]:
    try:
        return int(s)
    except (ValueError, TypeError):
        return None


def _to_float(s: Any) -> Optional[float]:
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def parse_forecast(data: list, location: str) -> List[Dict[str, Any]]:
    """Pure parser: JMA forecast JSON -> next-days projected daily rows for `location`.

    Returns [] on a partial/degraded response (missing weekly block or weather series)
    rather than raising, so a truncated JMA payload yields an empty projection.
    """
    offset = config.LOCATION_TEMP_OFFSET.get(location, 0.0)
    if not isinstance(data, list) or len(data) < 2:
        return []
    tslist = data[1].get("timeSeries", [])                # block 1 = weekly forecast
    ws = next((ts for ts in tslist if ts.get("areas") and "weatherCodes" in ts["areas"][0]), None)
    if ws is None:
        return []
    a = ws["areas"][0]
    codes, pops = a["weatherCodes"], a.get("pops", [])
    rels = a.get("reliabilities", [])
    dates = [d[:10] for d in ws["timeDefines"]]

    ts_t = next((ts for ts in tslist if ts.get("areas") and "tempsMax" in ts["areas"][0]), None)
    tmin, tmax, tidx = [], [], {}
    if ts_t is not None:
        tarea = next((x for x in ts_t["areas"]
                      if x["area"]["name"] == config.JMA_FORECAST_TEMP_AREA), ts_t["areas"][0])
        tmin, tmax = tarea.get("tempsMin", []), tarea.get("tempsMax", [])
        tidx = {d[:10]: i for i, d in enumerate(ts_t["timeDefines"])}

    out: List[Dict[str, Any]] = []
    for i, date in enumerate(dates):
        code = codes[i] if i < len(codes) else ""
        pop = _to_int(pops[i]) if i < len(pops) else None
        mean_t = None
        max_t = None
        j = tidx.get(date)
        if j is not None:
            lo = _to_float(tmin[j]) if j < len(tmin) else None
            hi = _to_float(tmax[j]) if j < len(tmax) else None
            if hi is not None:
                max_t = round(hi + offset, 1)
            if lo is not None and hi is not None:
                mean_t = round((lo + hi) / 2 + offset, 1)
            elif hi is not None:
                mean_t = round(hi + offset, 1)
        out.append({
            "date": date,
            "location": location,
            "sunshine_hours": _sun_from_code(code),
            "mean_temp": mean_t,
            "max_temp": max_t,
            "is_scour": _is_scour(code, pop),
            "weather": _weather_text(code),
            "reliability": rels[i] if i < len(rels) else "",
        })
    return out


def fetch_forecast(location: str = "上野村") -> List[Dict[str, Any]]:
    """Fetch + project the JMA weekly forecast for `location` (next ~7 days)."""
    data = requests.get(FORECAST_URL.format(area=config.JMA_FORECAST_AREA),
                        headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT).json()
    return parse_forecast(data, location)
