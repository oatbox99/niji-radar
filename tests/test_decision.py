"""4段ゲート判定 (安全→合法/営業→魚の生存(C&R)→釣果) の network-free テスト。

区間(reach)単位。build_verdict は today 注入で純化、reach_report は tmp DB を直接 INSERT。
"""
from __future__ import annotations

from src import db
from src.decision import (
    CAUTION,
    GO,
    NO_GO,
    build_verdict,
    go_checklist,
    reach_open,
    reach_report,
)
from src.engine.trout_index import TroutParams, TState

P = TroutParams()
WINTER = "2026-01-15"        # kanna_ueno(冬季C&R) の営業期間内・木曜(定休火曜でない)
SUMMER = "2026-07-15"        # 一般渓流(kanna_oniishi 等) の解禁期間内

GOOD_WATER = {"water_level_status": "平常", "water_trend": "変化なし"}
# 気温10℃ → 水温プロキシ12℃ (10-16℃ 至適・C&R safe)、最高12℃ (午後クローズ非該当)
WX_COOL = {"mean_temp": 10.0, "max_temp": 12.0, "sunshine_hours": 5.0,
           "sunshine_estimated": 0}
SEM_SASA = {"turbidity_score": 1, "cond_score": None, "source_post_date": WINTER}


def _state(quality="好適", tsi=60.0, water_temp=12.0, cr="safe", date=WINTER):
    return TState(date=date, tsi=tsi, temp_activity=1.0, water_temp_c=water_temp,
                  quality_state=quality, cr_risk=cr, is_scour=False)


def _verdict(reach_id, state, sem, water, wx=WX_COOL, today=WINTER, dam=None, stock=None):
    return build_verdict(reach_id, state, [state], sem, water, wx, P,
                         today=today, dam=dam, stock=stock)


# --------------------------------------------------------------------------- #
# reach_open — 営業/合法ゲートの純関数
# --------------------------------------------------------------------------- #
def test_reach_open_in_season_non_closed_day():
    ueno = {"season": {"open": (10, 15), "close": (2, 28)},
            "closed_weekday": 1, "catch_release": True}
    assert reach_open(ueno, WINTER)["open"] is True


def test_reach_open_closed_weekday_blocks():
    ueno = {"season": {"open": (10, 15), "close": (2, 28)},
            "closed_weekday": 1, "catch_release": True}
    r = reach_open(ueno, "2026-01-13")           # 2026-01-13 は火曜
    assert r["open"] is False and "定休" in r["reason"]


def test_reach_open_out_of_season_cr_vs_general():
    cr = {"season": {"open": (10, 15), "close": (2, 28)},
          "closed_weekday": 1, "catch_release": True}
    gen = {"season": {"open": (3, 1), "close": (9, 20)},
           "closed_weekday": None, "catch_release": False}
    cr_off = reach_open(cr, SUMMER)              # 冬季C&Rの夏 = 期間外
    gen_off = reach_open(gen, WINTER)            # 一般渓流の冬 = 禁漁期
    assert cr_off["open"] is False and "C&R" in cr_off["reason"]
    assert gen_off["open"] is False and "禁漁" in gen_off["reason"]


# --------------------------------------------------------------------------- #
# Gate 0: 安全ハードゲート (泥濁り / 増水 / ダム放流)
# --------------------------------------------------------------------------- #
def test_fresh_mud_forces_no_go():
    v = _verdict("kanna_ueno", _state(), {"turbidity_score": 2, "source_post_date": WINTER},
                 GOOD_WATER)
    assert v.level == NO_GO and v.mood == "grumpy"
    assert any("泥濁り" in r for r in v.reasons)


def test_flood_stage_forces_no_go():
    v = _verdict("kanna_ueno", _state(), SEM_SASA,
                 {"water_level_status": "注意", "water_trend": "上昇"})
    assert v.level == NO_GO


def test_dam_surge_forces_no_go():
    dam = {"risk": True,
           "worst": {"dam": "下久保", "slope_pct": 0.5, "discharge": 80.0,
                     "eta_hours": (2, 3), "eta_text": "約2〜3時間で到達の恐れ"},
           "monitored": ["下久保"], "dams_seen": 1, "slope_known": 1, "id_missing": []}
    v = _verdict("kanna_oniishi", _state(date=SUMMER), SEM_SASA, GOOD_WATER,
                 today=SUMMER, dam=dam)
    assert v.level == NO_GO and any("下久保" in r for r in v.reasons)


# --------------------------------------------------------------------------- #
# Gate 1: 合法/営業ゲート
# --------------------------------------------------------------------------- #
def test_out_of_season_forces_no_go():
    # 好条件でも営業期間外なら NO_GO (kanna_ueno は夏は期間外)
    v = _verdict("kanna_ueno", _state(date=SUMMER), SEM_SASA, GOOD_WATER, today=SUMMER)
    assert v.level == NO_GO


def test_unknown_water_status_blocks_go_as_caution():
    v = _verdict("kanna_ueno", _state(), SEM_SASA,
                 {"water_level_status": None, "water_trend": None})
    assert v.level == CAUTION
    assert any("水位情報が取得できていません" in r for r in v.reasons)


# --------------------------------------------------------------------------- #
# Gate 2: 魚の生存ゲート (C&R 水温連動)
# --------------------------------------------------------------------------- #
def test_catch_release_reach_hot_water_is_no_go():
    # C&R区間で水温≥20℃ (気温24℃→水温20.4℃) → リリース死亡リスクで見送り
    hot = {"mean_temp": 24.0, "max_temp": 26.0, "sunshine_hours": 5.0,
           "sunshine_estimated": 0}
    v = _verdict("kanna_ueno", _state(quality="高水温減退", cr="nogo"), SEM_SASA,
                 GOOD_WATER, wx=hot)
    assert v.level == NO_GO and any("魚のために" in r for r in v.reasons)


def test_non_catch_release_reach_hot_water_not_cr_no_go():
    # 一般渓流(catch_release=False)は同じ水温でも C&R nogo ゲートを適用しない
    hot = {"mean_temp": 24.0, "max_temp": 26.0, "sunshine_hours": 5.0,
           "sunshine_estimated": 0}
    v = _verdict("kanna_oniishi", _state(quality="高水温減退", cr="nogo", date=SUMMER),
                 SEM_SASA, GOOD_WATER, wx=hot, today=SUMMER)
    assert v.level != NO_GO           # 20.4℃ < 24℃ なので生息不適でもなく NO_GO にならない


# --------------------------------------------------------------------------- #
# Gate 3: 釣果スコア + source_confidence 格下げ
# --------------------------------------------------------------------------- #
def test_good_plus_sasa_plus_calm_is_go_for_verified_reach():
    v = _verdict("kanna_ueno", _state(quality="好適"), SEM_SASA, GOOD_WATER)
    assert v.level == GO and v.mood == "ecstatic"
    assert v.source_confidence == "verified"


def test_go_downgraded_to_caution_for_reference_reach():
    # 同じ好条件でも source_confidence='参考' の区間は確信GOを出さない → CAUTION
    v = _verdict("tone_maebashi", _state(quality="好適"), SEM_SASA, GOOD_WATER)
    assert v.level == CAUTION and v.source_confidence == "参考"
    assert any("参考" in c for c in v.caveats)


def test_missing_field_intel_is_caution_not_go():
    # 濁り情報なし(sem=None) → クリア/笹濁りを確認できない → GO非発行
    v = _verdict("kanna_ueno", _state(quality="好適"), None, GOOD_WATER)
    assert v.level == CAUTION and v.turbidity is None


def test_observed_condition_overrides_model():
    # モデルは低活性だが新しい現場報告が好適(cond=2) → 観測が上書きし GO
    v = _verdict("kanna_ueno", _state(quality="低活性"),
                 {"turbidity_score": 1, "cond_score": 2, "source_post_date": WINTER},
                 GOOD_WATER)
    assert v.observed_quality == "好適" and v.effective_quality == "好適"
    assert "現場報告" in v.quality_source and v.level == GO


def test_days_since_stock_boost_wording():
    v = _verdict("kanna_ueno", _state(quality="好適"), SEM_SASA, GOOD_WATER,
                 stock={"days": 2, "date": "2026-01-13"})
    assert v.level == GO and v.days_since_stock == 2
    assert any("放流" in r and "大チャンス" in r for r in v.reasons)


def test_afternoon_hoot_owl_caveat_on_high_max_temp():
    # 平均は涼しく safe だが日中最高が高温 (気温30℃→水温24℃≥22.8) → 午後クローズ caveat
    wx = {"mean_temp": 10.0, "max_temp": 30.0, "sunshine_hours": 5.0,
          "sunshine_estimated": 0}
    v = _verdict("kanna_ueno", _state(quality="好適"), SEM_SASA, GOOD_WATER, wx=wx)
    assert v.level in (GO, CAUTION)
    assert any("午後" in c for c in v.caveats)


# --------------------------------------------------------------------------- #
# confidence 逓減
# --------------------------------------------------------------------------- #
def test_confidence_never_certain_and_drops_with_gaps():
    full = _verdict("kanna_ueno", _state(quality="好適"), SEM_SASA, GOOD_WATER)
    # 参考区間 (src penalty)
    ref = _verdict("tone_maebashi", _state(quality="好適"), SEM_SASA, GOOD_WATER)
    # 欠測 (濁りなし・現場情報なし・水位不明)
    gappy = _verdict("kanna_ueno", _state(quality="好適"), None,
                     {"water_level_status": None, "water_trend": None})
    assert full.confidence <= 0.95              # 断定しない
    assert ref.confidence < full.confidence     # 参考で逓減
    assert gappy.confidence < full.confidence   # 欠測で逓減


# --------------------------------------------------------------------------- #
# go_checklist ↔ build_verdict の SSOT
# --------------------------------------------------------------------------- #
def test_go_checklist_all_green_iff_go():
    go = _verdict("kanna_ueno", _state(quality="好適"), SEM_SASA, GOOD_WATER)
    go.as_of = WINTER                            # 営業判定は as_of 基準 (今日でなく判定日)
    assert go.level == GO
    assert all(r["ok"] for r in go_checklist(go))

    mud = _verdict("kanna_ueno", _state(quality="好適"),
                   {"turbidity_score": 2, "source_post_date": WINTER}, GOOD_WATER)
    mud.as_of = WINTER
    rows = go_checklist(mud)
    assert mud.level == NO_GO and not all(r["ok"] for r in rows)
    hazard = next(r for r in rows if r["label"] == "危険がない")
    assert hazard["ok"] is False and hazard["detail"] == "泥濁り"


def test_go_checklist_labels_match_catch_release_flag():
    # C&R区間は「水温がC&Rに安全」、一般区間は「水温が適域」
    cr = _verdict("kanna_ueno", _state(quality="好適"), SEM_SASA, GOOD_WATER)
    cr.as_of = WINTER
    gen = _verdict("kanna_oniishi", _state(quality="好適", date=SUMMER), SEM_SASA,
                   GOOD_WATER, today=SUMMER)
    gen.as_of = SUMMER
    cr_labels = {r["label"] for r in go_checklist(cr)}
    gen_labels = {r["label"] for r in go_checklist(gen)}
    assert "水温がC&Rに安全" in cr_labels
    assert "水温が適域" in gen_labels


def test_go_checklist_marks_unknown_turbidity():
    v = _verdict("kanna_ueno", _state(quality="好適"), None, GOOD_WATER)
    v.as_of = WINTER
    turb = next(r for r in go_checklist(v) if r["label"].startswith("濁りOK"))
    assert turb["ok"] is False and turb["unknown"] is True
    assert "情報なし" in turb["detail"]


# --------------------------------------------------------------------------- #
# reach_report — DB エントリポイント (tmp DB を直接 INSERT)
# --------------------------------------------------------------------------- #
def test_reach_report_assembles_verdict_from_db(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    conn = db.connect(p)
    try:
        for d in ("2026-01-13", "2026-01-14", "2026-01-15"):
            conn.execute(
                "INSERT INTO weather_data (date, location, sunshine_hours, mean_temp, "
                "max_temp, min_temp, sunshine_estimated, source) "
                "VALUES (?, '上野村', 5.0, 12.0, 13.0, 10.0, 0, 't')", (d,))
        conn.execute(
            "INSERT INTO river_physical_data (date_time, river_name, station, "
            "water_level_status, water_trend, source) "
            "VALUES ('2026-01-15T09:00', '神流川', '万場', '平常', '変化なし', 't')")
        conn.execute(
            "INSERT INTO semantic_field_logs (date, source_name, reach_id, "
            "turbidity_score, source_post_date, confidence) "
            "VALUES ('2026-01-15', 'x', 'kanna_ueno', 1, '2026-01-15', 0.9)")
        conn.commit()
        v = reach_report(conn, "kanna_ueno", params=P, today=WINTER)
    finally:
        conn.close()
    assert v.reach_id == "kanna_ueno"
    assert v.as_of == "2026-01-15"
    assert v.series and v.series[-1].date == "2026-01-15"
    assert v.level == GO                          # verified + 好条件 + 笹濁り + 平水
    assert v.stations                             # 観測点ステータスが載る
