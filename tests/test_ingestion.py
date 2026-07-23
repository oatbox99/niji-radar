"""収集の純関数部分 (水位パーサ・日次集計・clamp・プロンプト整形) の network-free テスト。"""
from __future__ import annotations

from src.data_ingestion import (
    _SEMANTIC_PROMPT,
    _clamp_nonneg_int,
    _clamp_score,
    _clamp_temp,
    aggregate_daily,
    parse_water_status,
)

# 実 Yahoo 天気・災害 DOM を模したフィクスチャ (万場 行・上昇・注意水位)。
FIX_CAUTION = """
<table><tbody>
<tr class="largeLine levelCaution">
  <td class="stationName"><span class="name">万場（万場第二）</span></td>
  <td><a class="stationLink"><div class="waterLevel">
    <span class="trendIcon"><img src="//s.yimg.jp/.../waterLevelUp_black_l.png"></span>
    <span class="waterLevelLabel">上昇</span>
  </div></a></td>
</tr>
</tbody></table>
"""

FIX_NORMAL = """
<table><tbody>
<tr class="largeLine levelNormal">
  <td class="stationName"><span class="name">前橋（前橋）</span></td>
  <td><a class="stationLink"><div class="waterLevel">
    <span class="trendIcon"><img src="//s.yimg.jp/.../waterLevelUnchange_l.png"></span>
    <span class="waterLevelLabel">平常</span>
  </div></a></td>
</tr>
</tbody></table>
"""


# --- parse_water_status ------------------------------------------------------
def test_parse_water_status_caution_and_trend():
    r = parse_water_status(FIX_CAUTION, "万場")
    assert r["water_level_status"] == "注意"
    assert r["water_trend"] == "上昇"
    assert r["water_level_label"] == "上昇"


def test_parse_water_status_normal_stage():
    r = parse_water_status(FIX_NORMAL, "前橋")
    assert r["water_level_status"] == "平常"       # 平常は None ではない (scraper生存の証)
    assert r["water_trend"] == "変化なし"


def test_parse_water_status_missing_station_is_none():
    r = parse_water_status("<table><tbody></tbody></table>", "万場")
    assert r["water_level_status"] is None and r["water_trend"] is None


# --- aggregate_daily ---------------------------------------------------------
def test_aggregate_daily_sums_sunshine_and_temp_stats():
    blocks = {
        "20260118000000": {"sun1h": [0.0, 0], "temp": [20.0, 0]},
        "20260118001000": {"temp": [20.4, 0]},          # 毎時外: 気温は数える・日照は無視
        "20260118010000": {"sun1h": [0.5, 0], "temp": [21.0, 0]},
        "20260118020000": {"sun1h": [1.0, 0], "temp": [23.0, 0]},
    }
    agg = aggregate_daily(blocks)
    assert agg["sunshine_hours"] == 1.5             # HH:00 のみ 0.0+0.5+1.0
    assert agg["mean_temp"] == round((20.0 + 20.4 + 21.0 + 23.0) / 4, 1)
    assert agg["max_temp"] == 23.0                  # C&R 午後クローズ判定に使う
    assert agg["min_temp"] == 20.0                  # 夏の早朝安全窓


def test_aggregate_daily_empty_is_none():
    agg = aggregate_daily({})
    assert agg["sunshine_hours"] is None and agg["mean_temp"] is None
    assert agg["max_temp"] is None and agg["min_temp"] is None


# --- clamps ------------------------------------------------------------------
def test_clamp_score_rejects_out_of_range_and_wrong_types():
    assert _clamp_score(2, 0, 2) == 2 and _clamp_score(0, 0, 2) == 0
    assert _clamp_score(3, 0, 2) is None and _clamp_score(-1, 0, 2) is None
    assert _clamp_score(True, 0, 2) is None          # bool はスコアでない
    assert _clamp_score("1", 0, 2) is None and _clamp_score(None, 0, 2) is None


def test_clamp_nonneg_int_for_catch_report():
    assert _clamp_nonneg_int(15) == 15 and _clamp_nonneg_int(0) == 0
    assert _clamp_nonneg_int(-3) is None
    assert _clamp_nonneg_int(True) is None
    assert _clamp_nonneg_int("20") is None and _clamp_nonneg_int(None) is None


def test_clamp_temp_accepts_plausible_rejects_absurd():
    assert _clamp_temp(12.0) == 12.0 and _clamp_temp(0.0) == 0.0
    assert _clamp_temp(35.0) == 35.0
    assert _clamp_temp(40.0) is None                 # 範囲外 (>35℃)
    assert _clamp_temp(-1.0) is None
    assert _clamp_temp(True) is None                 # bool 除外
    assert _clamp_temp("12") is None and _clamp_temp(None) is None


# --- ingest_semantic の純関数部分 (プロンプト整形) ----------------------------
def test_semantic_prompt_formats_reach_and_text():
    s = _SEMANTIC_PROMPT.format(reach="神流川 上野村", text="濁りなし 水温12度")
    assert "神流川 上野村" in s and "濁りなし 水温12度" in s
    assert "turbidity_score" in s and "cond_score" in s   # JSON スキーマの二重波括弧が生きている
