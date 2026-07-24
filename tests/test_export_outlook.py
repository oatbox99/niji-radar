"""公開HP(export_html)の7日アウトルック — 営業ゲート/参考格下げ/欠測日のnetwork-freeテスト。

敵対レビュー確定RED「_day_glyph が季節/営業ゲートを見ず、閉鎖区間の期間外日に▲GOを出す」
の回帰ガード。strip は未来7日を示す唯一のシグナルのため、4段ゲート(合法/営業が釣果より上位)
を表示層でもバイパスさせない。
"""
from __future__ import annotations

import datetime as _dt

from scripts.export_html import _day_glyph, _outlook_strip
from src import config
from src.decision import _annotate_outlook_closed, build_verdict, reach_open
from src.engine.trout_index import TroutParams, TState

P = TroutParams()
WINTER = "2026-01-15"


def _state(date, tsi=70.0, temp=12.0, scour=False):
    return TState(date=date, tsi=tsi, temp_activity=1.0, water_temp_c=temp,
                  quality_state="好適", cr_risk="safe", is_scour=scour)


def test_day_glyph_closed_day_is_no_even_with_good_temp():
    # 営業期間外の日は水温12℃・TSI70でも ▲ を出さない(RED回帰: 密漁誘導の遮断)
    s = _state("2026-07-25")
    assert _day_glyph(s, P, open_ok=False) == "no"
    assert _day_glyph(s, P, open_ok=True) == "go"          # 開いていれば従来どおり


def test_day_glyph_reference_reach_caps_go_to_caution():
    # 参考区間は glyph でも確信▲を出さない(source_confidence 格下げの glyph 版)
    s = _state("2026-01-16")
    assert _day_glyph(s, P, open_ok=True, is_ref=True) == "cau"
    # NO_GO 条件(高水温/増水/閉鎖)は参考でも ■ のまま
    assert _day_glyph(_state("2026-01-16", temp=21.0), P, open_ok=True, is_ref=True) == "no"


def _verdict_with_series(reach_id, today, series):
    sem = {"turbidity_score": 1, "cond_score": None, "source_post_date": today}
    water = {"water_level_status": "平常", "water_trend": "変化なし"}
    wx = {"mean_temp": 10.0, "max_temp": 12.0, "sunshine_hours": 5.0, "sunshine_estimated": 0}
    v = build_verdict(reach_id, series[0], series, sem, water, wx, P, today=today)
    v.series = series
    v.as_of = today
    return v


def test_outlook_strip_closed_days_render_as_no_glyph():
    # kanna_ueno は 2/28 close。2/26-27=営業内(go)、3/1以降=期間外(■) — 好水温でも。
    today = "2026-02-25"
    series = [_state(today)] + [
        _state((_dt.date(2026, 2, 25) + _dt.timedelta(days=i)).isoformat())
        for i in range(1, 8)
    ]
    assert not reach_open(config.REACHES["kanna_ueno"], "2026-03-02")["open"]  # 前提確認
    html = _outlook_strip(_verdict_with_series("kanna_ueno", today, series))
    assert '"go"' in html                       # 期間内の日は行くべし
    assert '"no"' in html                       # 期間外の日は水温好適でも■
    assert "営業/解禁期間外の日を含みます" in html   # 凡例に明示


def test_outlook_strip_missing_temp_day_gets_placeholder_not_dropped():
    # 温度None日を無言で脱落させない(「7日」が左詰め6本になり明日を誤読させる回帰)
    today = "2026-01-15"
    days = [(_dt.date(2026, 1, 15) + _dt.timedelta(days=i)).isoformat() for i in range(8)]
    series = [_state(days[0])] + [
        _state(days[1], temp=None, tsi=0.0),    # 明日=欠測
    ] + [_state(d) for d in days[2:]]
    html = _outlook_strip(_verdict_with_series("kanna_ueno", today, series))
    assert '"na"' in html and "—=予報欠測日" in html


def test_annotate_outlook_closed_marks_out_of_season_dates():
    reach = config.REACHES["kanna_ueno"]        # 冬季C&R: 夏は期間外
    outlook = {"best": {"date": "2026-07-25", "tsi": 70.0, "quality": "好適"},
               "next_good": {"date": "2026-01-16", "tsi": 70.0, "quality": "好適"},
               "weekend": [{"date": "2026-07-25", "wd": "土", "tsi": 70.0, "quality": "好適"}]}
    _annotate_outlook_closed(outlook, reach)
    assert outlook["best"]["closed"] is True            # 夏=期間外
    assert outlook["next_good"]["closed"] is False      # 冬=営業内
    assert outlook["weekend"][0]["closed"] is True
