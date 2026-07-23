"""表示SSOT (解説・釣り方ヒント・C&R保全・季節・和暦/鮮度) の network-free テスト。"""
from __future__ import annotations

from src import guide
from src.engine.trout_index import OBS_LABELS


# --- jp_date / freshness -----------------------------------------------------
def test_jp_date_weekday_and_year():
    assert guide.jp_date("2026-07-18") == "7月18日(土)"       # 2026-07-18 は土曜
    assert guide.jp_date("2026-07-18", with_year=True) == "2026年7月18日(土)"


def test_jp_date_none_and_bad():
    assert guide.jp_date(None) == "—"
    assert guide.jp_date("not-a-date") == "not-a-date"


def test_freshness_levels_by_age():
    assert guide.freshness("2026-07-18", "2026-07-18")["days"] == 0
    assert guide.freshness("2026-07-18", "2026-07-18")["level"] == "ok"
    assert guide.freshness("2026-07-17", "2026-07-18")["label"] == "前日まで反映"
    assert guide.freshness("2026-07-15", "2026-07-18")["level"] == "ok"      # 3日
    assert guide.freshness("2026-07-10", "2026-07-18")["level"] == "warn"    # 8日
    assert guide.freshness(None, "2026-07-18")["level"] == "warn"


# --- fishing_tips: methods 分岐 ----------------------------------------------
def test_tips_no_go_is_single_hold_message_only():
    tips = guide.fishing_tips("NO_GO", "絶好", 1, "上昇")     # ハザードが全てを上書き
    assert len(tips) == 1 and "竿を出せる状態ではありません" in tips[0]


def test_tips_method_branches_lure_fly_bait():
    lure = " ".join(guide.fishing_tips("GO", "好適", None, None, methods=["ルアー"]))
    fly = " ".join(guide.fishing_tips("GO", "好適", None, None, methods=["フライ"]))
    bait = " ".join(guide.fishing_tips("GO", "好適", None, None, methods=["エサ"]))
    assert "ルアー" in lure and "フライ" not in lure
    assert "フライ" in fly and "ルアー" not in fly
    assert "エサ" in bait and "ルアー" not in bait


def test_tips_low_activity_is_slow_bottom():
    tips = " ".join(guide.fishing_tips("CAUTION", "低活性", 0, None,
                                       methods=["ルアー", "フライ"]))
    assert "スロー" in tips or "ボトム" in tips


def test_tips_high_temp_targets_cool_water():
    tips = " ".join(guide.fishing_tips("CAUTION", "高水温減退", 0, None))
    assert "朝夕" in tips


def test_tips_fresh_stock_and_sasa_hints():
    tips = guide.fishing_tips("GO", "好適", 1, None,
                              methods=["ルアー", "フライ", "エサ"], days_since_stock=2)
    assert any("笹濁り" in t for t in tips)                  # turbidity==1 のヒント
    assert any("放流" in t for t in tips)                    # 放流0-3日ブースト


def test_tips_none_quality_falls_back_to_eyeball():
    tips = guide.fishing_tips("CAUTION", None, 0, None)
    assert any("ご自身の目" in t for t in tips)


# --- cr_note: C&R 保全メッセージ ---------------------------------------------
def test_cr_note_by_risk_band():
    assert "見送り" in guide.cr_note("nogo", True)
    assert guide.cr_note("strong", True) is not None
    assert guide.cr_note("caution", True) is not None
    assert guide.cr_note("safe", True) is None
    assert guide.cr_note(None, True) is None
    assert guide.cr_note("nogo", False) is None              # 非C&R区間には出さない


# --- season_note -------------------------------------------------------------
def test_season_note_winter_is_cr_info():
    n = guide.season_note("2026-01-15")
    assert n["level"] == "info" and "冬期" in n["msg"]


def test_season_note_midsummer_is_high_temp_warn():
    for d in ("2026-07-15", "2026-08-10"):
        n = guide.season_note(d)
        assert n["level"] == "warn" and "高水温" in n["msg"]


def test_season_note_snowmelt_spring():
    n = guide.season_note("2026-04-20")
    assert "雪代" in n["msg"]


# --- method_label ------------------------------------------------------------
def test_method_label_joins_methods_and_cr():
    assert guide.method_label(["ルアー", "フライ"], True) == "ルアー・フライ／全キャッチ&リリース"
    assert "キープ可" in guide.method_label(["エサ"], False)


# --- TROUT_STAGES vocabulary == quality_state 語彙 ---------------------------
def test_trout_stages_states_match_quality_vocabulary():
    vocab = {"絶好", "好適", "やや低調", "低活性", "高水温減退", "高水温危険", "増水リセット"}
    stage_states = {s["state"] for s in guide.TROUT_STAGES}
    assert stage_states == vocab
    assert set(guide.STATE_SHORT) == vocab
    # 観測→quality の写像先も同じ語彙に収まる
    assert set(OBS_LABELS.values()) <= vocab


def test_verdict_oneliner_covers_all_levels():
    assert set(guide.VERDICT_ONELINER) == {"GO", "CAUTION", "NO_GO"}


def test_lake_depth_note_shore_only_drops_boat_advice():
    # shore_only の湖(野反湖など)は高水温時でもボート/トローリングを主推奨しない
    boat = guide.lake_depth_note(24.0, shore_only=False)
    shore = guide.lake_depth_note(24.0, shore_only=True)
    assert "ボート" in boat and "トローリング" in boat
    assert "ボート" not in shore and "トローリング" not in shore
    assert "岸釣り" in shore
    # 適水温帯では両者とも岸から狙える案内(ボート限定にしない)
    assert "ショア" in guide.lake_depth_note(14.0, shore_only=True)
