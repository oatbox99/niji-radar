"""自己監査層 (verdict_snapshot 台帳・前回比・予実照合) の network-free テスト。"""
from __future__ import annotations

import sqlite3

from src import calibration, db
from src.decision import build_verdict
from src.engine.trout_index import TroutParams, TState

P = TroutParams()
WINTER = "2026-01-15"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(db.SCHEMA)
    return conn


def _snap(**over):
    base = {
        "run_date": "2026-01-20", "reach_id": "kanna_ueno", "as_of": "2026-01-20",
        "level": "CAUTION", "tsi": 40.0, "model_quality": "やや低調",
        "observed_quality": None, "effective_quality": "やや低調",
        "quality_source": "TSI予想", "cr_risk": "safe", "turbidity": None,
        "water_status": "平常", "water_temp_proxy": 12.0, "confidence": 0.5,
        "source_confidence": "verified", "days_since_stock": None,
        "next_good_date": None, "observed_post_date": None,
    }
    base.update(over)
    return base


def _seed_weather(conn, days, sunshine=5.0, temp=15.0):
    for i in range(days):
        conn.execute(
            "INSERT INTO weather_data (date, location, sunshine_hours, mean_temp, "
            "sunshine_estimated, source) VALUES (?, '上野村', ?, ?, 0, 't')",
            (f"2026-01-{i + 1:02d}", sunshine, temp))


def _seed_obs(conn, ingest_date, post_date, cond, catch=None):
    conn.execute(
        "INSERT OR REPLACE INTO semantic_field_logs (date, source_name, reach_id, "
        "turbidity_score, cond_score, catch_report, source_post_date, confidence, "
        "raw_excerpt) VALUES (?, 'テスト源', 'kanna_ueno', 0, ?, ?, ?, 0.9, 'x')",
        (ingest_date, cond, catch, post_date))


# --------------------------------------------------------------------------- #
# 台帳 (snapshot_from_verdict / save / latest_before)
# --------------------------------------------------------------------------- #
def test_snapshot_from_verdict_roundtrips_through_db():
    state = TState(WINTER, 60.0, 1.0, 12.0, "好適", "safe", False)
    v = build_verdict("kanna_ueno", state, [state],
                      {"turbidity_score": 1, "source_post_date": WINTER},
                      {"water_level_status": "平常", "water_trend": "変化なし"},
                      {"mean_temp": 10.0, "max_temp": 12.0, "sunshine_hours": 5.0,
                       "sunshine_estimated": 0}, P, today=WINTER)
    snap = calibration.snapshot_from_verdict(v, WINTER)
    assert snap["reach_id"] == "kanna_ueno" and snap["level"] == v.level
    conn = _conn()
    calibration.save_snapshot(conn, snap)
    got = calibration.latest_snapshot_before(conn, "kanna_ueno", "2026-01-16")
    assert got is not None and got["level"] == v.level and got["tsi"] == v.tsi


def test_latest_before_excludes_same_day_and_other_reach():
    conn = _conn()
    calibration.save_snapshot(conn, _snap(run_date="2026-01-19", level="GO"))
    calibration.save_snapshot(conn, _snap(run_date="2026-01-20"))
    prev = calibration.latest_snapshot_before(conn, "kanna_ueno", "2026-01-20")
    assert prev is not None and prev["run_date"] == "2026-01-19" and prev["level"] == "GO"
    assert calibration.latest_snapshot_before(conn, "kanna_ueno", "2026-01-19") is None
    assert calibration.latest_snapshot_before(conn, "tone_maebashi", "2026-01-21") is None


def test_snapshot_same_day_replaces():
    conn = _conn()
    calibration.save_snapshot(conn, _snap(tsi=10.0))
    calibration.save_snapshot(conn, _snap(tsi=20.0))
    rows = conn.execute("SELECT COUNT(*), MAX(tsi) FROM verdict_snapshot").fetchone()
    assert tuple(rows) == (1, 20.0)


# --------------------------------------------------------------------------- #
# 前回比 (compute_delta)
# --------------------------------------------------------------------------- #
def test_delta_none_without_prev():
    assert calibration.compute_delta(None, _snap()) is None


def test_delta_unchanged_is_empty():
    d = calibration.compute_delta(_snap(), _snap(run_date="2026-01-21", tsi=41.0))
    assert d is not None and d["prev_date"] == "2026-01-20"
    assert d["changes"] == []              # 判定同一・TSI差5未満は変化と見なさない


def test_delta_detects_changes():
    prev = _snap(level="CAUTION", tsi=40.0, turbidity=None, water_status="平常")
    cur = _snap(run_date="2026-01-21", level="NO_GO", tsi=5.0, turbidity=2,
                water_status="注意", effective_quality="増水リセット",
                next_good_date="2026-01-26", observed_post_date="2026/01/21")
    text = " / ".join(calibration.compute_delta(prev, cur)["changes"])
    assert "様子見 → 見送り" in text
    assert "40 → 5" in text
    assert "情報なし → 泥濁り" in text
    assert "平常 → 注意" in text
    assert "次に行くなら" in text
    assert "新しい現場報告" in text              # スラッシュ日付でも検出


def test_delta_old_obs_not_reported_as_new():
    prev = _snap(observed_post_date="2026-01-15")
    cur = _snap(run_date="2026-01-21", observed_post_date="2026-01-10")
    d = calibration.compute_delta(prev, cur)
    assert all("現場報告" not in c for c in d["changes"])


# --------------------------------------------------------------------------- #
# 予実照合 (reconcile)
# --------------------------------------------------------------------------- #
def test_grade_ladder():
    g = calibration._grade
    assert g("好適", "好適") == "exact"
    assert g("好適", "絶好") == "near"          # rank 3 vs 4 = 隣接
    assert g("やや低調", "好適") == "near"
    assert g("低活性", "好適") == "miss"         # rank 1 vs 3 = 離れている
    assert g(None, "好適") == "miss"
    assert g("好適", None) == "miss"


def test_reconcile_empty():
    conn = _conn()
    _seed_weather(conn, 5)
    r = calibration.reconcile(conn, "kanna_ueno", P)
    assert r["n"] == 0 and r["rows"] == [] and "まだありません" in r["note"]


def test_reconcile_lake_short_circuits_without_river_keyerror():
    # 湖は river/water_station キーを持たない → 河川入力の組立てで KeyError を起こしていた回帰。
    # 照合ソース(semantic_source)も無いため、クラッシュせず n=0「照合しない」を返す。
    conn = _conn()
    r = calibration.reconcile(conn, "sugenuma", P)
    assert r["n"] == 0 and r["rows"] == []
    assert "湖" in r["note"]


def test_reconcile_matches_and_normalizes_dates():
    conn = _conn()
    _seed_weather(conn, 20, sunshine=5.0, temp=15.0)   # このパラメータで各日「絶好」
    _seed_obs(conn, "2026-01-16", "2026/01/15", 3, catch=12)   # cond3=絶好 → exact
    r = calibration.reconcile(conn, "kanna_ueno", P)
    assert r["n"] == 1 and r["exact"] == 1
    assert r["rows"][0]["date"] == "2026-01-15" and r["rows"][0]["catch"] == 12
    assert r["rows"][0]["predicted"] == "絶好"
    assert "少ない" in r["note"]                        # サンプル<10 の注意書き
    # 気象履歴の外の観測日は照合対象に数えない
    _seed_obs(conn, "2026-02-01", "2025-01-01", 3)
    r2 = calibration.reconcile(conn, "kanna_ueno", P)
    assert all(row["date"] != "2025-01-01" for row in r2["rows"])


def test_reconcile_same_day_later_ingest_wins():
    conn = _conn()
    _seed_weather(conn, 10)
    _seed_obs(conn, "2026-01-08", "2026-01-08", 0)     # 低活性
    _seed_obs(conn, "2026-01-09", "2026-01-08", 3)     # 後の取り込みが同観測日を上書き
    r = calibration.reconcile(conn, "kanna_ueno", P)
    assert r["n"] == 1 and r["rows"][0]["observed"] == "絶好"
