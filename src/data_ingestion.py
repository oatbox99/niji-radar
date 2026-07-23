"""Data collection (ニジマスレーダー). Public data only; no self-logged data.

Paths:
  1. fetch_jma_daily()      — JMA AMeDAS JSON. DAILY sunshine total + mean/max/min temp.
  2. fetch_river_stations() — flood-stage STATUS + trend for a river's gauges (Yahoo mirror).
  3. ingest_semantic()      — 釣況ブログ/漁協テキスト -> Gemini -> 濁り/体感/水温/釣果/放流,
                              公開の現場観測。TSI(推定)の答え合わせに使う。

物理データは (river, location) 単位で重複排除して取得し、semantic/放流は reach 単位。
No live water-temperature sensor exists — water_temp is a labelled air-temp proxy.
"""
from __future__ import annotations

import datetime as dt
import re
import sqlite3
from typing import Any, Dict, List, Optional

import requests
from bs4 import BeautifulSoup

from . import config, dam, db, forecast
from .engine.trout_index import estimate_water_temp
from .llm import extract_json

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
JST = dt.timezone(dt.timedelta(hours=9))
TIMEOUT = 20


def _http_get(url: str, *, as_json: bool = False) -> Any:
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json() if as_json else resp.text


# --------------------------------------------------------------------------- #
# 1. JMA AMeDAS — clean JSON API
# --------------------------------------------------------------------------- #
def fetch_jma_amedas(location: str = "上野村") -> Dict[str, Any]:
    """Latest AMeDAS snapshot (a single 10-min reading) for a configured location."""
    station = config.JMA_STATIONS[location]
    code = station["code"]
    base = config.JMA_AMEDAS_BASE

    latest = _http_get(f"{base}/data/latest_time.txt").strip()
    stamp = re.sub(r"[-:T]", "", latest)[:14]
    snapshot = _http_get(f"{base}/data/map/{stamp}.json", as_json=True)

    rec = snapshot.get(code)
    if rec is None:
        raise RuntimeError(f"AMeDAS station {code} absent from snapshot {stamp}")

    def _val(field: str) -> Optional[float]:
        v = rec.get(field)
        return float(v[0]) if isinstance(v, list) and v and v[0] is not None else None

    return {
        "observed_at": latest,
        "location": location,
        "station_code": code,
        "air_temp": _val("temp"),
        "sun1h": _val("sun1h"),
        "sunshine_estimated": station["sunshine_estimated"],
    }


def aggregate_daily(blocks: Dict[str, Any]) -> Dict[str, Optional[float]]:
    """Reduce a day's 10-min AMeDAS series to DAILY aggregates.

    sunshine_hours = sum of hourly sun1h (each is trailing 1h sunshine). temp aggregates:
    mean/max/min. max_temp feeds the C&R 午後クローズ (hoot owl) gate; min_temp marks the
    夏の早朝安全窓.
    """
    sun_total = 0.0
    sun_seen = False
    temps: List[float] = []
    for ts, rec in blocks.items():
        temp = rec.get("temp")
        if isinstance(temp, list) and temp and temp[0] is not None:
            temps.append(float(temp[0]))
        if len(ts) >= 12 and ts[10:12] == "00":          # top of the hour
            sun = rec.get("sun1h")
            if isinstance(sun, list) and sun and sun[0] is not None:
                sun_total += float(sun[0])
                sun_seen = True
    return {
        "sunshine_hours": round(sun_total, 1) if sun_seen else None,
        "mean_temp": round(sum(temps) / len(temps), 1) if temps else None,
        "max_temp": round(max(temps), 1) if temps else None,
        "min_temp": round(min(temps), 1) if temps else None,
    }


def fetch_jma_daily(location: str = "上野村", date: Optional[dt.date] = None) -> Dict[str, Any]:
    """Daily sunshine total + mean/max/min temp for a location."""
    station = config.JMA_STATIONS[location]
    code = station["code"]
    base = config.JMA_AMEDAS_BASE
    day = date or dt.datetime.now(JST).date()
    ymd = day.strftime("%Y%m%d")

    blocks: Dict[str, Any] = {}
    for hh in ("00", "03", "06", "09", "12", "15", "18", "21"):
        try:
            blocks.update(_http_get(f"{base}/data/point/{code}/{ymd}_{hh}.json", as_json=True))
        except requests.HTTPError:
            continue
    agg = aggregate_daily(blocks)
    return {
        "date": day.isoformat(),
        "location": location,
        "sunshine_hours": agg["sunshine_hours"],
        "mean_temp": agg["mean_temp"],
        "max_temp": agg["max_temp"],
        "min_temp": agg["min_temp"],
        "sunshine_estimated": station["sunshine_estimated"],
    }


# --------------------------------------------------------------------------- #
# 2. River water level — categorical flood-stage per gauge (Yahoo mirror)
# --------------------------------------------------------------------------- #
_LEVEL_CLASS_TO_STATUS = {
    "levelNone": "平常", "levelNormal": "平常", "levelStandby": "待機",
    "levelCaution": "注意", "levelEvacuate": "避難", "levelDanger": "危険",
    "levelOutbreak": "氾濫",
}


def parse_water_status(html: str, station: str) -> Dict[str, Any]:
    """Pure parser (no network): flood status + trend for one `station`.

    status/trend = None when the station row is absent (NORMAL day is '平常', never None),
    so downstream can tell 'scraper broke' from 'river is calm'.
    """
    soup = BeautifulSoup(html, "html.parser")
    name_span = soup.find(
        lambda t: t.name == "span"
        and "name" in (t.get("class") or [])
        and station in t.get_text())
    row = name_span.find_parent("tr") if name_span else None
    if row is None:
        return {"water_level_status": None, "water_trend": None, "water_level_label": None}

    classes = row.get("class", [])
    status = next((_LEVEL_CLASS_TO_STATUS[c] for c in classes if c in _LEVEL_CLASS_TO_STATUS), None)

    trend = None
    icon = row.find("img", src=re.compile("waterLevel"))
    if icon is not None:
        src = icon.get("src", "")
        trend = "上昇" if "Up" in src else "下降" if "Down" in src else \
            "変化なし" if "Unchange" in src else None

    label_span = row.find("span", class_="waterLevelLabel")
    label = label_span.get_text(strip=True) if label_span else None
    return {"water_level_status": status, "water_trend": trend, "water_level_label": label}


def fetch_river_stations(river_name: str = "神流川") -> Dict[str, Any]:
    """Flood-stage status + trend for EVERY configured gauge (for the map)."""
    cfg = config.RIVER_WATER_LEVEL[river_name]
    if not cfg.get("yahoo_url"):
        raise RuntimeError(f"{river_name}: 水位ミラーURL未確認（水位は参考/欠測扱い）")
    html = _http_get(cfg["yahoo_url"])
    stations = []
    for name in cfg["stations"]:
        parsed = parse_water_status(html, name)
        stations.append({
            "station": name,
            "water_level_status": parsed["water_level_status"],
            "water_trend": parsed["water_trend"],
        })
    return {
        "observed_at": dt.datetime.now(JST).isoformat(timespec="minutes"),
        "river_name": river_name,
        "stations": stations,
        "source": cfg["yahoo_url"],
    }


# --------------------------------------------------------------------------- #
# 3. Semantic scraping — 釣況テキスト -> Gemini -> field observations + 放流
# --------------------------------------------------------------------------- #
_SEMANTIC_PROMPT = """あなたはニジマス釣りの河川コンディション分析アシスタントです。
以下は釣り関連ブログ/漁協サイト/SNSから抽出したプレーンテキストです。対象の釣り場は「{reach}」。
このテキストから、対象釣り場の最新の現場状況だけを読み取り、JSONで返してください。
他河川・他釣り場の話題・広告・定型文は無視すること。判断できない項目は必ず null にすること（推測禁止）。

出力スキーマ:
{{
  "turbidity_score": 0|1|2|null,          // 0=クリア, 1=笹濁り, 2=泥濁り
  "cond_score": 0|1|2|3|null,             // 体感の釣れ具合/活性: 0=渋い/低活性, 1=ぼちぼち, 2=好調, 3=絶好
  "catch_count": number|null,             // 釣果の匹数が読み取れれば(例 '20匹'→20)
  "water_temp_c": number|null,            // 本文に水温の記載があれば数値のみ
  "latest_stocking_date": "YYYY-MM-DD"|null, // 放流の告知/実施日が読み取れれば
  "source_post_date": "YYYY-MM-DD"|null,  // 投稿日/更新日が読み取れれば
  "confidence": number,                   // 0.0-1.0 抽出の確信度
  "evidence": string                      // 判断根拠にした本文の短い引用
}}

--- テキストここから ---
{text}
--- テキストここまで ---
"""


def fetch_plaintext(url: str, *, max_chars: int = 8000) -> str:
    """Strip a page to plain text for the LLM (drop scripts/styles)."""
    soup = BeautifulSoup(_http_get(url), "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text("\n", strip=True)[:max_chars]


def ingest_semantic(reach_id: str) -> Dict[str, Any]:
    """Grab reach text, ask Gemini to structure it. Returns {"semantic":..,"stocking":..|None}."""
    reach = config.REACHES[reach_id]
    source_name = reach.get("semantic_source") or reach["label"]
    text = fetch_plaintext(reach["info_url"])
    parsed = extract_json(_SEMANTIC_PROMPT.format(reach=reach["label"], text=text))

    conf = _clamp_confidence(parsed.get("confidence"))
    sem = {
        "date": dt.date.today().isoformat(),
        "source_name": source_name,
        "reach_id": reach_id,
        "turbidity_score": _clamp_score(parsed.get("turbidity_score"), 0, 2),
        "cond_score": _clamp_score(parsed.get("cond_score"), 0, 3),
        "water_temp_obs": _clamp_temp(parsed.get("water_temp_c")),
        "catch_report": _clamp_nonneg_int(parsed.get("catch_count")),
        "source_post_date": parsed.get("source_post_date"),
        "confidence": conf,
        "raw_excerpt": (parsed.get("evidence") or "")[:500],
    }
    stocking = None
    stock_date = parsed.get("latest_stocking_date")
    if isinstance(stock_date, str) and re.match(r"\d{4}[-/]\d{2}[-/]\d{2}", stock_date):
        stocking = {
            "reach_id": reach_id,
            "stock_date": stock_date[:10].replace("/", "-"),
            "source_name": source_name,
            "source_post_date": parsed.get("source_post_date"),
            "confidence": conf,
            "raw_excerpt": (parsed.get("evidence") or "")[:500],
        }
    return {"semantic": sem, "stocking": stocking}


def _clamp_score(v: Any, lo: int, hi: int) -> Optional[int]:
    """Accept only in-range ints; reject bools/strings/out-of-range as unknown (None)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    iv = int(v)
    return iv if lo <= iv <= hi else None


def _clamp_nonneg_int(v: Any) -> Optional[int]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    iv = int(v)
    return iv if iv >= 0 else None


def _clamp_temp(v: Any) -> Optional[float]:
    """Accept a plausible water temp (0–35℃); reject bools/strings/absurd values."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    fv = float(v)
    return round(fv, 1) if 0.0 <= fv <= 35.0 else None


def _clamp_confidence(v: Any) -> Optional[float]:
    """Accept a 0.0–1.0 confidence; reject bools/strings ('high' etc.)/out-of-range as None.

    LLM は数値指定を無視して 'high' 等の文字列を返すことがあり、SQLite の REAL 列は非数値を
    TEXT のまま保持する。未検証のまま下流の int(conf*100) 等に流すと公開HP生成が丸ごと落ちる
    ため、他の抽出フィールドと同様にここで数値化を強制する。
    """
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    fv = float(v)
    return round(fv, 2) if 0.0 <= fv <= 1.0 else None


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
def save_weather(conn: sqlite3.Connection, daily: Dict[str, Any]) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO weather_data
           (date, location, sunshine_hours, mean_temp, max_temp, min_temp,
            sunshine_estimated, source)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (daily["date"], daily["location"], daily["sunshine_hours"], daily["mean_temp"],
         daily.get("max_temp"), daily.get("min_temp"), int(daily["sunshine_estimated"]),
         "jma_amedas"),
    )


def save_river_stations(conn: sqlite3.Connection, river: Dict[str, Any],
                        water_temp: Optional[float]) -> None:
    for st in river["stations"]:
        conn.execute(
            """INSERT OR REPLACE INTO river_physical_data
               (date_time, river_name, station, water_level, water_level_status,
                water_trend, water_temp, water_temp_estimated, rainfall, dam_discharge, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (river["observed_at"], river["river_name"], st["station"], None,
             st["water_level_status"], st["water_trend"], water_temp, 1, None, None,
             river["source"]),
        )


def save_forecast(conn: sqlite3.Connection, row: Dict[str, Any]) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO forecast_data
           (date, location, sunshine_hours, mean_temp, max_temp, is_scour, weather,
            reliability, source)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (row["date"], row["location"], row["sunshine_hours"], row["mean_temp"],
         row.get("max_temp"), int(row["is_scour"]), row["weather"], row["reliability"],
         "jma_forecast"),
    )


def save_dam(conn: sqlite3.Connection, river: str, reading: Dict[str, Any]) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO dam_data
           (date_time, river_name, dam, rainfall, discharge, slope_pct, source)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (dt.datetime.now(JST).isoformat(timespec="minutes"), river, reading["dam"],
         reading["rainfall"], reading["discharge"], reading["slope_pct"], "dspdamdata"),
    )


def save_semantic(conn: sqlite3.Connection, sem: Dict[str, Any]) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO semantic_field_logs
           (date, source_name, reach_id, turbidity_score, cond_score, water_temp_obs,
            catch_report, source_post_date, confidence, raw_excerpt)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (sem["date"], sem["source_name"], sem["reach_id"], sem["turbidity_score"],
         sem["cond_score"], sem["water_temp_obs"], sem["catch_report"],
         sem["source_post_date"], sem["confidence"], sem["raw_excerpt"]),
    )


def save_stocking(conn: sqlite3.Connection, stock: Dict[str, Any]) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO stocking_data
           (reach_id, stock_date, source_name, source_post_date, confidence, raw_excerpt)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (stock["reach_id"], stock["stock_date"], stock["source_name"],
         stock["source_post_date"], stock["confidence"], stock["raw_excerpt"]),
    )


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def _ingest_weather(conn: sqlite3.Connection) -> Dict[str, Optional[float]]:
    """Fetch weather per unique location. Returns {location: proxy_water_temp}."""
    proxies: Dict[str, Optional[float]] = {}
    for loc in config.unique_locations():
        try:
            daily = fetch_jma_daily(loc)
            save_weather(conn, daily)
            conn.commit()                       # commit weather before any later failure
            proxies[loc] = estimate_water_temp(daily["mean_temp"])
            print(f"[JMA]      {loc} mean={daily['mean_temp']}℃ max={daily['max_temp']}℃ "
                  f"sun={daily['sunshine_hours']}h "
                  f"({'推計' if daily['sunshine_estimated'] else '観測'})")
        except Exception as exc:  # noqa: BLE001 — one location failing must not abort others
            print(f"[JMA]      {loc} skipped: {exc}")
            proxies[loc] = None
    return proxies


def _ingest_rivers(conn: sqlite3.Connection, proxies: Dict[str, Optional[float]]) -> None:
    """Fetch water stage + dams per unique river."""
    # map river -> a representative location's proxy temp (for the river_physical rows)
    river_loc = {r["river"]: r["location"] for r in config.REACHES.values()
                 if r.get("waterbody", "river") == "river"}
    for river in config.unique_rivers():
        wt = proxies.get(river_loc.get(river))
        try:
            river_data = fetch_river_stations(river)
            save_river_stations(conn, river_data, wt)
            conn.commit()
            print("[River]    " + river + ": " + " / ".join(
                f"{s['station']}:{s['water_level_status']}" for s in river_data["stations"]))
        except Exception as exc:  # noqa: BLE001 — gauge failure → water unknown (CAUTION)
            print(f"[River]    {river} skipped: {exc}")
        dams = config.DAM_DISCHARGE.get(river, {})
        got = 0
        for name, dam_id in dams.items():
            try:
                save_dam(conn, river, dam.fetch_dam(name, dam_id))
                got += 1
            except Exception as exc:  # noqa: BLE001 — one dam failing must not abort the rest
                print(f"[Dam]      {river}/{name} skipped: {exc}")
        if got:
            conn.commit()
            print(f"[Dam]      {river}: {got}/{len(dams)} 放流量を取得")


def _ingest_forecast(conn: sqlite3.Connection) -> None:
    for loc in config.unique_locations():
        try:
            for f in forecast.fetch_forecast(loc):
                save_forecast(conn, f)
            conn.commit()
            print(f"[Forecast] {loc} 週間予報を取得")
        except Exception as exc:  # noqa: BLE001 — forecast is optional
            print(f"[Forecast] {loc} skipped: {exc}")


def _ingest_semantic(conn: sqlite3.Connection) -> None:
    for reach_id, reach in config.REACHES.items():
        if not reach.get("semantic_source"):
            continue
        try:
            out = ingest_semantic(reach_id)
            save_semantic(conn, out["semantic"])
            if out["stocking"]:
                save_stocking(conn, out["stocking"])
            conn.commit()
            sem = out["semantic"]
            print(f"[Semantic] {reach_id}: turb={sem['turbidity_score']} "
                  f"cond={sem['cond_score']} temp={sem['water_temp_obs']} "
                  f"catch={sem['catch_report']} stock={out['stocking'] is not None}")
        except Exception as exc:  # noqa: BLE001 — semantic must never lose physical data
            conn.rollback()
            print(f"[Semantic] {reach_id} skipped: {exc}")


def run(*, with_semantic: bool = True) -> None:
    """One ingestion cycle: weather+forecast per location, water+dams per river, semantic per reach."""
    db.init_db()
    conn = db.connect()
    try:
        proxies = _ingest_weather(conn)
        _ingest_rivers(conn, proxies)
        _ingest_forecast(conn)
        if with_semantic:
            _ingest_semantic(conn)
        print("[DB]       committed to", db.DB_PATH)
    finally:
        conn.close()


if __name__ == "__main__":
    run()
