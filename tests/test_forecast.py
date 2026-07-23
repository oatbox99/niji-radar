"""気象庁週間予報パーサ (TSI 投影入力) の network-free テスト。"""
from __future__ import annotations

from src.forecast import _is_scour, _sun_from_code, parse_forecast

FIX = [
    {"timeSeries": []},                                   # block 0 (短期) — 無視
    {"timeSeries": [
        {"timeDefines": ["2026-07-19T00:00:00+09:00", "2026-07-20T00:00:00+09:00"],
         "areas": [{"area": {"name": "群馬県"}, "weatherCodes": ["200", "300"],
                    "pops": ["", "60"], "reliabilities": ["", "B"]}]},
        {"timeDefines": ["2026-07-19T00:00:00+09:00", "2026-07-20T00:00:00+09:00"],
         "areas": [{"area": {"name": "前橋"}, "tempsMin": ["", "25"], "tempsMax": ["", "35"]}]},
    ]},
]


def test_sun_from_code_by_category():
    assert _sun_from_code("100") == 9.0        # 晴
    assert _sun_from_code("201") == 4.0        # 曇時々晴
    assert _sun_from_code("200") == 2.5        # 曇
    assert _sun_from_code("300") == 0.5        # 雨
    assert _sun_from_code("400") == 1.0        # 雪


def test_is_scour_only_on_forecast_heavy_rain():
    assert _is_scour("300", 60) is True
    assert _is_scour("300", 30) is False       # 低pop → 投影scourではない
    assert _is_scour("201", 90) is False       # 雨コードでない


def test_parse_forecast_maps_dates_temps_and_max():
    rows = parse_forecast(FIX, "前橋")
    assert [r["date"] for r in rows] == ["2026-07-19", "2026-07-20"]
    assert rows[0]["sunshine_hours"] == 2.5 and rows[0]["mean_temp"] is None   # 温度空
    assert rows[1]["mean_temp"] == 30.0        # (25+35)/2, 前橋 offset 0
    assert rows[1]["max_temp"] == 35.0         # C&R 午後クローズ判定に使う日次最高
    assert rows[1]["is_scour"] is True         # code 300 + pop 60


def test_parse_forecast_applies_location_offset():
    rows = parse_forecast(FIX, "上野村")        # 上野村 offset -2.5
    assert rows[1]["mean_temp"] == 27.5 and rows[1]["max_temp"] == 32.5


def test_parse_forecast_degraded_returns_empty_not_raises():
    assert parse_forecast([], "前橋") == []                       # ブロックなし
    assert parse_forecast([{"timeSeries": []}], "前橋") == []      # 週間ブロック欠損
    assert parse_forecast([{}, {"timeSeries": []}], "前橋") == []  # 天気系列なし


def test_parse_forecast_missing_temp_block_still_projects():
    only_weather = [{}, {"timeSeries": [
        {"timeDefines": ["2026-07-19T00:00:00+09:00"],
         "areas": [{"area": {"name": "群馬県"}, "weatherCodes": ["100"]}]}]}]
    rows = parse_forecast(only_weather, "前橋")
    assert len(rows) == 1 and rows[0]["mean_temp"] is None and rows[0]["max_temp"] is None
    assert rows[0]["sunshine_hours"] == 9.0
