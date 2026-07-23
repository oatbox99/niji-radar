"""TSI エンジン（水温台形 × 濁りU字 × 光凸 + C&R死亡率）の network-free テスト。"""
from __future__ import annotations

from src.engine.trout_index import (
    DailyInput,
    OBS_LABELS,
    TroutParams,
    classify_quality,
    compute_series,
    cr_release_risk,
    daily_tsi,
    estimate_water_temp,
    obs_to_quality,
    temp_activity,
    turbidity_mod,
)

P = TroutParams()


# --- temp_activity: 非対称台形 (10-16℃ 満点, 高温側急降下) ----------------------
def test_temp_activity_plateau_is_full():
    assert temp_activity(10.0, P) == 1.0
    assert temp_activity(13.0, P) == 1.0
    assert temp_activity(16.0, P) == 1.0


def test_temp_activity_zero_outside_survivable_band():
    assert temp_activity(3.0, P) == 0.0          # t_min 以下
    assert temp_activity(2.0, P) == 0.0
    assert temp_activity(20.0, P) == 0.0         # t_stress 以上 (摂餌停止)
    assert temp_activity(21.0, P) == 0.0
    assert temp_activity(None, P) == 0.0


def test_temp_activity_high_side_drops_steeply():
    # 16→20℃ を線形に 1.0→0.0。18℃ は中点で 0.5。高温側は単調減少。
    assert temp_activity(18.0, P) == 0.5
    assert temp_activity(17.0, P) > temp_activity(18.0, P) > temp_activity(19.0, P)
    assert temp_activity(19.0, P) > 0.0


def test_temp_activity_low_side_ramps_up():
    # 3→10℃ の立ち上がり。冬期は低いが 0 ではない。
    assert 0.0 < temp_activity(6.0, P) < 1.0
    assert temp_activity(9.0, P) > temp_activity(5.0, P)


# --- daily_tsi ---------------------------------------------------------------
def test_daily_tsi_none_water_is_zero():
    # 水温不明では点けない (盲目では GO しない=保守側)。
    assert daily_tsi(5.0, None, 1, P) == 0.0


def test_daily_tsi_sasa_beats_clear():
    # U字応答: 笹濁り(1) > クリア(0)。
    assert daily_tsi(5.0, 13.0, 1, P) > daily_tsi(5.0, 13.0, 0, P)


def test_daily_tsi_mud_kills_score():
    assert daily_tsi(5.0, 13.0, 2, P) == 0.0     # 泥濁り係数 0


def test_turbidity_mod_unknown_is_neutral_low():
    # 不明は中立やや下 (確信を上げない)。笹濁りより低く、泥濁りより高い。
    assert turbidity_mod(2, P) < turbidity_mod(None, P) < turbidity_mod(1, P)


# --- cr_release_risk: 保全ゲート ----------------------------------------------
def test_cr_release_risk_bands():
    assert cr_release_risk(15.9, P) == "safe"
    assert cr_release_risk(16.0, P) == "caution"    # cr_caution 16℃
    assert cr_release_risk(19.0, P) == "strong"     # cr_strong 18.9℃
    assert cr_release_risk(20.0, P) == "nogo"       # cr_nogo 20℃ (実質catch&kill)
    assert cr_release_risk(None, P) == "unknown"


# --- classify_quality --------------------------------------------------------
def test_classify_scour_resets_regardless_of_tsi():
    assert classify_quality(90.0, 12.0, True, P) == "増水リセット"


def test_classify_high_temp_bands():
    assert classify_quality(80.0, 24.0, False, P) == "高水温危険"   # t_lethal 接近
    assert classify_quality(80.0, 20.0, False, P) == "高水温減退"   # t_stress
    assert classify_quality(80.0, 23.9, False, P) == "高水温減退"   # 20-24 は減退


def test_classify_by_tsi_and_low_temp():
    assert classify_quality(70.0, 13.0, False, P) == "絶好"        # >= go_min 68
    assert classify_quality(60.0, 13.0, False, P) == "好適"        # >= good_min 55
    assert classify_quality(10.0, 5.0, False, P) == "低活性"        # 低水温でスロー
    assert classify_quality(40.0, 13.0, False, P) == "やや低調"     # 適域だが低スコア


# --- compute_series ----------------------------------------------------------
def test_compute_series_is_per_day_independent():
    inputs = [
        DailyInput("2026-01-10", 5.0, 13.0, 1, False),
        DailyInput("2026-01-11", 8.0, 24.0, 0, False),
        DailyInput("2026-01-12", 2.0, 12.0, None, True),   # scour day
    ]
    series = compute_series(inputs, P)
    assert [s.date for s in series] == ["2026-01-10", "2026-01-11", "2026-01-12"]
    assert series[0].quality_state in ("絶好", "好適")
    assert series[1].quality_state == "高水温危険"           # 24℃
    assert series[2].quality_state == "増水リセット"          # scour → tsi 0
    assert series[2].tsi == 0.0
    assert series[2].cr_risk == "safe"                       # 12℃ はC&R安全帯


# --- estimate_water_temp / obs_to_quality ------------------------------------
def test_estimate_water_temp_proxy_formula():
    assert estimate_water_temp(10.0) == 12.0                 # 0.6*10 + 6
    assert estimate_water_temp(24.0) == 20.4
    assert estimate_water_temp(None) is None


def test_obs_to_quality_maps_score_to_vocab():
    assert obs_to_quality(0) == "低活性"
    assert obs_to_quality(1) == "やや低調"
    assert obs_to_quality(2) == "好適"
    assert obs_to_quality(3) == "絶好"
    assert obs_to_quality(None) is None
    assert obs_to_quality(9) is None                         # 範囲外
    assert OBS_LABELS[3] == "絶好"
