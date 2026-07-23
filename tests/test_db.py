"""SQLite スキーマ (reach粒度・新テーブル・加算マイグレーション) の network-free テスト。"""
from __future__ import annotations

import sqlite3

from src import db


def _tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _cols(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def test_init_creates_all_tables(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    conn = db.connect(p)
    try:
        names = _tables(conn)
    finally:
        conn.close()
    assert {"river_physical_data", "weather_data", "semantic_field_logs",
            "stocking_data", "verdict_snapshot", "dam_data", "forecast_data"} <= names
    assert "catch_log" not in names        # 自己釣果テーブルは設計上持たない


def test_weather_has_max_min_temp(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    conn = db.connect(p)
    try:
        cols = _cols(conn, "weather_data")
    finally:
        conn.close()
    # max_temp=C&R午後クローズ / min_temp=夏の早朝安全窓
    assert {"max_temp", "min_temp", "sunshine_hours", "mean_temp"} <= cols


def test_semantic_and_stocking_are_reach_scoped(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    conn = db.connect(p)
    try:
        sem = _cols(conn, "semantic_field_logs")
        stock = _cols(conn, "stocking_data")
    finally:
        conn.close()
    # 鮎の moss_score でなく cond_score / water_temp_obs、判定単位は reach_id
    assert {"reach_id", "cond_score", "water_temp_obs", "catch_report"} <= sem
    assert {"reach_id", "stock_date"} <= stock


def test_verdict_snapshot_has_trout_columns(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    conn = db.connect(p)
    try:
        cols = _cols(conn, "verdict_snapshot")
    finally:
        conn.close()
    assert {"reach_id", "tsi", "cr_risk", "water_temp_proxy", "days_since_stock",
            "source_confidence", "next_good_date"} <= cols


def test_migration_adds_missing_columns_to_old_db(tmp_path):
    # 旧カラムだけの DB → init_db が ALTER で加算する (破壊しない)
    p = tmp_path / "old.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE weather_data (date TEXT, location TEXT, "
              "PRIMARY KEY (date, location))")
    c.commit()
    c.close()
    db.init_db(p)
    conn = db.connect(p)
    try:
        cols = _cols(conn, "weather_data")
    finally:
        conn.close()
    assert {"sunshine_hours", "mean_temp", "max_temp", "min_temp",
            "sunshine_estimated", "source"} <= cols
