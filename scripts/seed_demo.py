"""Seed ~45 days of plausible DEMO data so the dashboard/HP is demoable.

FAKE data (source='seed_demo'); real ingestion overwrites today's rows. 神流川 上野村 gets
cool water + a scour→recovery story + a fresh stocking + a blog field log (→ 好条件). 渡良瀬川
は水位ミラー未確認なので水位を入れない（参考区間で欠測が正直に出る様子を再現）。

⚠️ 冬期C&R区間(上野村/前橋)は営業期間外(夏)だと季節ゲートで NO_GO になる — これは仕様。
実運用の冬に見ると verified 区間が GO を出す。

Run:  python -m scripts.seed_demo
"""
from __future__ import annotations

import datetime as dt
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config, db  # noqa: E402
from src.engine.trout_index import estimate_water_temp  # noqa: E402

DAYS = 45
KANNA_SCOURS = {12}
TODAY = dt.date.today()
NOW = f"{TODAY.isoformat()}T06:00+09:00"


def _weather_row(offset: int, rng: random.Random, scours: set):
    """(sun, mean, max, min) — 冷水の好適条件を模した気温 (mean≈10-14℃)。"""
    if offset in scours:                               # storm day: no sun, cold rain
        mean = round(rng.uniform(6.0, 9.0), 1)
        return round(rng.uniform(0.0, 1.5), 1), mean, round(mean + 2, 1), round(mean - 2, 1)
    base_sun = rng.uniform(3.0, 6.5)
    if any(0 < offset - s <= 1 for s in scours):
        base_sun *= 0.6
    mean = round(rng.uniform(10.0, 14.0), 1)
    return round(base_sun, 1), mean, round(mean + 4, 1), round(mean - 3, 1)


def _seed_weather(conn, location, rng, scours):
    for offset in range(DAYS - 1, -1, -1):
        day = TODAY - dt.timedelta(days=offset)
        sun, mean, mx, mn = _weather_row(offset, rng, scours)
        conn.execute(
            """INSERT OR REPLACE INTO weather_data
               (date, location, sunshine_hours, mean_temp, max_temp, min_temp,
                sunshine_estimated, source)
               VALUES (?, ?, ?, ?, ?, ?, 1, 'seed_demo')""",
            (day.isoformat(), location, sun, mean, mx, mn),
        )


def _seed_lake_weather(conn, location, rng, lo, hi):
    """湖のAMeDAS地点の気温(標高補正前)を DAYS 日分。"""
    for offset in range(DAYS - 1, -1, -1):
        day = TODAY - dt.timedelta(days=offset)
        mean = round(rng.uniform(lo, hi), 1)
        conn.execute(
            """INSERT OR REPLACE INTO weather_data
               (date, location, sunshine_hours, mean_temp, max_temp, min_temp,
                sunshine_estimated, source)
               VALUES (?, ?, ?, ?, ?, ?, 1, 'seed_demo')""",
            (day.isoformat(), location, round(rng.uniform(3.0, 6.0), 1), mean,
             round(mean + 4, 1), round(mean - 3, 1)))


def _seed_gauges_today(conn, river, water_temp):
    for name in config.RIVER_WATER_LEVEL[river]["stations"]:
        conn.execute(
            """INSERT OR REPLACE INTO river_physical_data
               (date_time, river_name, station, water_level_status, water_trend,
                water_temp, water_temp_estimated, source)
               VALUES (?, ?, ?, '平常', '変化なし', ?, 1, 'seed_demo')""",
            (NOW, river, name, water_temp),
        )


def _seed_forecast(conn, location, rng):
    for offset in range(1, 8):                          # next 7 days: cool, calm week
        day = TODAY + dt.timedelta(days=offset)
        mean = round(rng.uniform(10.0, 14.0), 1)
        conn.execute(
            """INSERT OR REPLACE INTO forecast_data
               (date, location, sunshine_hours, mean_temp, max_temp, is_scour,
                weather, reliability, source)
               VALUES (?, ?, ?, ?, ?, 0, '曇り時々晴れ', 'B', 'seed_demo')""",
            (day.isoformat(), location, round(rng.uniform(3.0, 6.0), 1), mean,
             round(mean + 4, 1)),
        )


def main() -> None:
    db.init_db()
    conn = db.connect()
    rng = random.Random(20260723)
    try:
        # --- weather per unique location (cool = 好適 conditions) ---
        _seed_weather(conn, "上野村", rng, KANNA_SCOURS)
        _seed_weather(conn, "前橋", random.Random(99), set())
        _seed_weather(conn, "中之条", random.Random(55), set())
        _seed_weather(conn, "桐生", random.Random(77), set())
        # 湖のAMeDAS地点(標高補正前の気温)。藤原(700m)→菅沼/丸沼/大尻沼、草津(1223m)→野反湖。
        # 榛名湖は中之条(354m・上で河川用にseed済)を標高補正で共用するため個別seedは不要。
        # 藤原/草津は高標高湖の観測点として少し高めに置き、標高減率補正で適水温になる様子を再現。
        _seed_lake_weather(conn, "藤原", random.Random(21), lo=18.0, hi=22.0)
        _seed_lake_weather(conn, "草津", random.Random(22), lo=14.0, hi=18.0)
        for loc in ("上野村", "前橋", "中之条", "桐生", "藤原", "草津"):
            _seed_forecast(conn, loc, random.Random(hash(loc) % 1000))
        # 野反湖: 放流カレンダー(直近放流を1件)
        conn.execute(
            "INSERT OR REPLACE INTO stocking_data(reach_id, stock_date, source_name, "
            "source_post_date, confidence, raw_excerpt) VALUES ('nozorilake', ?, 'seed_demo', "
            "?, 0.7, '（デモ）ニジマス放流')",
            ((TODAY - dt.timedelta(days=2)).isoformat(),
             (TODAY - dt.timedelta(days=2)).isoformat()))

        # --- 神流川: scour story + calm gauges today ---
        for offset in KANNA_SCOURS:
            day = TODAY - dt.timedelta(days=offset)
            conn.execute(
                """INSERT OR REPLACE INTO river_physical_data
                   (date_time, river_name, station, water_level_status, water_trend,
                    water_temp, water_temp_estimated, source)
                   VALUES (?, '神流川', '万場', '注意', '上昇', ?, 1, 'seed_demo')""",
                (f"{day.isoformat()}T12:00+09:00", estimate_water_temp(8.0)),
            )
        _seed_gauges_today(conn, "神流川", estimate_water_temp(13.0))
        _seed_gauges_today(conn, "利根川", estimate_water_temp(13.0))
        _seed_gauges_today(conn, "吾妻川", estimate_water_temp(13.0))
        # 渡良瀬川は水位ミラー未確認 → 水位を入れない（参考区間の欠測を再現）

        # --- 上野村(verified): blog field log + fresh stocking ---
        conn.execute(
            """INSERT OR REPLACE INTO semantic_field_logs
               (date, source_name, reach_id, turbidity_score, cond_score, water_temp_obs,
                catch_report, source_post_date, confidence, raw_excerpt)
               VALUES (?, 'seed_demo', 'kanna_ueno', 1, 3, 12.0, 18, ?, 0.7,
                       '（デモ）笹濁りで活性高い、水温12℃、ニジマス18匹')""",
            (TODAY.isoformat(), (TODAY - dt.timedelta(days=1)).isoformat()),
        )
        conn.execute(
            """INSERT OR REPLACE INTO stocking_data
               (reach_id, stock_date, source_name, source_post_date, confidence, raw_excerpt)
               VALUES ('kanna_ueno', ?, 'seed_demo', ?, 0.7, '（デモ）本日ニジマス放流しました')""",
            ((TODAY - dt.timedelta(days=2)).isoformat(),
             (TODAY - dt.timedelta(days=2)).isoformat()),
        )

        # --- dams calm (利根川5基 / 神流川 下久保) ---
        for river, dams in config.DAM_DISCHARGE.items():
            for name in dams:
                conn.execute(
                    """INSERT OR REPLACE INTO dam_data
                       (date_time, river_name, dam, rainfall, discharge, slope_pct, source)
                       VALUES (?, ?, ?, 0.0, 25.0, 0.0, 'seed_demo')""",
                    (NOW, river, name),
                )
        conn.commit()
        print(f"seeded {DAYS}日 x 5区間（上野村: 冷水+放流+ブログ / 渡良瀬: 水位欠測デモ）。"
              "※冬期C&R区間は夏だと季節ゲートでNO_GO＝仕様")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
