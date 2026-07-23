"""SQLite schema + connection (ニジマスレーダー).

Design premise: システムは自分の釣果を一切使わない。予想は公開データ (JMA気象履歴 /
観測点別の水位ステージ / 上流ダム放流 / 他者の釣況ブログ) だけで立てる。ブログの
体感コンディション / 濁り / 放流 / 釣果が「答え合わせ」なので、自己記録テーブルは持たない。

鮎レーダーとの構造差分:
- 判定単位が「区間 (reach)」— 同じ河川名でも上野村(自然流量)と鬼石(下久保ダム放流支配)は
  真逆になるため、river ではなく reach_id で観測点/ダム/営業ルール/釣況源を紐付ける。
- ニジマス観測は cond_score (体感コンディション 0–3) と water_temp_obs (ブログ水温写真プロキシ)。
  鮎の moss_score (垢) は持たない。
- 放流後経過日数が管理C&R区間のコア特徴量なので stocking_data (放流告知) を独立テーブルに。
- 水温にライブ源が無い制約は鮎と同じ → water_temp は nullable + water_temp_estimated フラグ。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "niji.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS river_physical_data (
    date_time            TEXT NOT NULL,          -- ISO8601 (JST)
    river_name           TEXT NOT NULL,
    station              TEXT NOT NULL DEFAULT '',-- gauge name (万場/前橋/...) for the map
    water_level          REAL,                   -- numeric gauge (m); NULL where categorical only
    water_level_status   TEXT,                   -- 平常/待機/注意/避難/危険 (Yahoo 水防 stage)
    water_trend          TEXT,                   -- 上昇/下降/変化なし
    water_temp           REAL,                   -- nullable: no live sensor exists
    water_temp_estimated INTEGER DEFAULT 1,      -- 1 = proxied/estimated, 0 = measured
    rainfall             REAL,                   -- upstream basin rain mm/h (turbidity precursor)
    dam_discharge        REAL,                   -- dam-fed reaches only; else NULL
    source               TEXT,
    PRIMARY KEY (date_time, river_name, station)
);

CREATE TABLE IF NOT EXISTS weather_data (
    date               TEXT NOT NULL,            -- YYYY-MM-DD
    location           TEXT NOT NULL,            -- AMeDAS location key
    sunshine_hours     REAL,                     -- DAILY total (see fetch_jma_daily)
    mean_temp          REAL,                     -- DAILY mean
    max_temp           REAL,                     -- DAILY max (C&R 午後クローズ判定に使う)
    min_temp           REAL,                     -- DAILY min (夏の早朝安全窓)
    sunshine_estimated INTEGER DEFAULT 1,        -- 1 = 推計, 0 = 観測 (日照計あり)
    source             TEXT,
    PRIMARY KEY (date, location)
);

CREATE TABLE IF NOT EXISTS semantic_field_logs (
    date             TEXT NOT NULL,              -- ingestion date
    source_name      TEXT NOT NULL,
    reach_id         TEXT,                       -- which reach this report is about
    turbidity_score  INTEGER,                    -- 0 clear / 1 笹濁り / 2 泥濁り / NULL
    cond_score       INTEGER,                    -- 0 低活性 / 1 やや低調 / 2 好調 / 3 絶好 / NULL
    water_temp_obs   REAL,                       -- 現地水温 (ブログの水温写真等) ℃ / NULL
    catch_report     INTEGER,                    -- other anglers' 釣果(匹) — validation only
    source_post_date TEXT,                       -- when the post was WRITTEN (staleness)
    confidence       REAL,                       -- LLM self-reported 0..1
    raw_excerpt      TEXT,                       -- evidence quote for auditability
    PRIMARY KEY (date, source_name)
);

CREATE TABLE IF NOT EXISTS stocking_data (
    reach_id         TEXT NOT NULL,              -- reach the stocking applies to
    stock_date       TEXT NOT NULL,              -- date fish were stocked (放流日)
    source_name      TEXT,
    source_post_date TEXT,                       -- when the notice was posted (staleness)
    confidence       REAL,                       -- LLM self-reported 0..1
    raw_excerpt      TEXT,
    PRIMARY KEY (reach_id, stock_date)
);

CREATE TABLE IF NOT EXISTS dam_data (
    date_time    TEXT NOT NULL,                  -- reading time (JST)
    river_name   TEXT NOT NULL,
    dam          TEXT NOT NULL,                  -- 下久保/八ッ場/草木...
    rainfall     REAL,                           -- 流域平均雨量 mm/h
    discharge    REAL,                           -- 放流量 m3/s (latest)
    slope_pct    REAL,                           -- 1h放流増加率 (0.30 = +30%; 999 = 0→放流開始)
    source       TEXT,
    PRIMARY KEY (date_time, river_name, dam)
);

CREATE TABLE IF NOT EXISTS verdict_snapshot (
    run_date           TEXT NOT NULL,            -- YYYY-MM-DD (JST) the system SAID this
    reach_id           TEXT NOT NULL,            -- judgment unit (区間)
    as_of              TEXT,                     -- latest actual-data date behind it
    level              TEXT,                     -- GO/CAUTION/NO_GO
    tsi                REAL,                     -- 適性メーター 0-100 (model)
    model_quality      TEXT,
    observed_quality   TEXT,
    effective_quality  TEXT,
    quality_source     TEXT,
    cr_risk            TEXT,                     -- C&R release-mortality band
    turbidity          INTEGER,
    water_status       TEXT,
    water_temp_proxy   REAL,
    confidence         REAL,
    source_confidence  TEXT,                     -- verified / 参考 / 未確認 (per-reach data trust)
    days_since_stock   INTEGER,                  -- 放流後経過日数 (NULL if unknown)
    next_good_date     TEXT,                     -- outlook "次に行くなら" promised that day
    observed_post_date TEXT,                     -- freshest blog post seen at this run
    PRIMARY KEY (run_date, reach_id)
);

CREATE TABLE IF NOT EXISTS forecast_data (
    date           TEXT NOT NULL,                -- future YYYY-MM-DD (JMA weekly forecast)
    location       TEXT NOT NULL,
    sunshine_hours REAL,                         -- derived from weather code (予報)
    mean_temp      REAL,                         -- (min+max)/2 + location offset
    max_temp       REAL,                         -- forecast daily max (C&R afternoon gate)
    is_scour       INTEGER DEFAULT 0,            -- forecast heavy rain → projected 増水リセット
    weather        TEXT,                         -- human weather text
    reliability    TEXT,                         -- JMA forecast reliability A/B/C
    source         TEXT,
    PRIMARY KEY (date, location)
);
"""


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Additively add any SCHEMA columns missing from an existing DB.

    `CREATE TABLE IF NOT EXISTS` never alters an existing table, so evolving SCHEMA
    would otherwise fail at write time with 'no such column'. This diffs against a
    fresh in-memory copy of SCHEMA and ALTERs in the gaps (加算のみ・破壊なし).
    """
    ref = sqlite3.connect(":memory:")
    try:
        ref.executescript(SCHEMA)
        for (table,) in ref.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            want = list(ref.execute(f"PRAGMA table_info({table})"))
            have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            for _cid, name, ctype, *_rest in want:
                if name not in have:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ctype}")
    finally:
        ref.close()


def init_db(db_path: Path = DB_PATH) -> None:
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
    print(f"initialized {DB_PATH}")
