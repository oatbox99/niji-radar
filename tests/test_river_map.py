"""区間模式マップ SVG (地名ハードコードなし・mark強調・取得失敗表示) のテスト。"""
from __future__ import annotations

from app.river_map import river_map_svg

_POINTS = [{"name": "前橋", "status": "平常", "trend": "変化なし", "sev": 0,
            "mark": True, "zone_label": "○ 好機"}]


def test_river_map_uses_river_name_and_captions():
    svg = river_map_svg(_POINTS, river="利根川",
                        upstream="上流（渋川）", downstream="下流（八斗島）")
    assert 'aria-label="利根川マップ"' in svg       # 引数由来 (polyline でない・退行ガード)
    assert "上流（渋川）" in svg and "下流（八斗島）" in svg
    assert "前橋" in svg and "<polyline" in svg


def test_river_map_mark_shows_rod_emoji():
    svg = river_map_svg(_POINTS, river="利根川")
    assert "🎣" in svg                               # mark=True の区間を強調
    plain = river_map_svg([{"name": "前橋", "status": "平常", "trend": "変化なし",
                            "sev": 0, "mark": False, "zone_label": ""}], river="利根川")
    assert "🎣" not in plain


def test_river_map_no_hardcoded_geography():
    svg = river_map_svg(_POINTS, river="利根川", upstream="上流", downstream="下流")
    assert "神流" not in svg and "上野村" not in svg


def test_river_map_missing_status_shows_failure():
    pts = [{"name": "前橋", "status": None, "trend": None, "sev": 0,
            "mark": False, "zone_label": ""}]
    assert "取得失敗" in river_map_svg(pts, river="利根川")
