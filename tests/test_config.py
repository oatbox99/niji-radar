"""静的 config (REACHES・派生ヘルパ・信頼度格付け) の整合テスト。"""
from __future__ import annotations

from src import config

# 河川/湖 共通の必須キー
_COMMON_KEYS = {
    "label", "location", "methods", "catch_release", "season", "closed_weekday",
    "source_confidence", "official_url", "info_url",
}
_RIVER_KEYS = {"river", "water_station", "dams"}
_LAKE_KEYS = {"elevation"}


def test_ui_reaches_all_exist_in_reaches():
    assert set(config.UI_REACHES) <= set(config.REACHES)
    assert config.UI_REACHES[0] == "kanna_ueno"        # verified を先頭に


def test_every_reach_has_required_keys():
    for rid, reach in config.REACHES.items():
        assert _COMMON_KEYS <= set(reach), f"{rid} に共通必須キー欠落"
        extra = _LAKE_KEYS if reach.get("waterbody") == "lake" else _RIVER_KEYS
        assert extra <= set(reach), f"{rid} に{'湖' if extra is _LAKE_KEYS else '河川'}必須キー欠落"
        assert set(reach["season"]) == {"open", "close"}
        assert isinstance(reach["catch_release"], bool)
        assert isinstance(reach["methods"], list) and reach["methods"]


def test_source_confidence_is_a_known_grade():
    allowed = {"verified", "参考", "未確認"}
    for rid, reach in config.REACHES.items():
        assert reach["source_confidence"] in allowed, f"{rid} の信頼度が不正"
    # verified アンカーが最低1区間ある (確信GOを出せる区間)
    assert any(r["source_confidence"] == "verified" for r in config.REACHES.values())


def test_unique_locations_and_rivers_dedupe():
    locs, rivers = config.unique_locations(), config.unique_rivers()
    assert len(locs) == len(set(locs)) and len(rivers) == len(set(rivers))
    # 神流川は上野村(自然)と鬼石(ダム)の2区間だが river は1つに畳まれる
    assert "神流川" in rivers
    assert all(loc in config.JMA_STATIONS for loc in locs)


def test_reach_dams_only_returns_verified_dam_ids():
    # 下久保は DAM_DISCHARGE に実在ID → 名前:ID を返す
    oniishi = config.reach_dams("kanna_oniishi")
    assert oniishi == {"下久保": config.DAM_DISCHARGE["神流川"]["下久保"]}
    # 八ッ場は ID 未確認 (DAM_DISCHARGE に無い) → 妄想IDを返さず空
    assert config.reach_dams("agatsuma_bando") == {}
    assert config.reach_dam_names("agatsuma_bando") == ["八ッ場"]   # 名前は残す(未確認明示)
    # 自然流量区間はダム監視なし
    assert config.reach_dams("kanna_ueno") == {}
    assert config.reach_dam_names("kanna_ueno") == []


def test_reach_dam_ids_exist_in_dam_discharge_or_absent():
    # 妄想でない: reach_dams が返す ID は必ず DAM_DISCHARGE の実在値
    for rid in config.REACHES:
        river = config.REACHES[rid].get("river", "")   # 湖は river 無し
        for name, dam_id in config.reach_dams(rid).items():
            assert config.DAM_DISCHARGE.get(river, {}).get(name) == dam_id


def test_lakes_are_wired_with_elevation_and_no_river_keys():
    lakes = [rid for rid in config.REACHES if config.is_lake(rid)]
    assert lakes, "湖区間が最低1つある"
    for rid in lakes:
        r = config.REACHES[rid]
        assert isinstance(r["elevation"], (int, float))
        assert "river" not in r and "dams" not in r     # 河川キーは持たない
        assert config.reach_dams(rid) == {}              # 湖はダム監視なし
        # 湖の観測点は標高必須: 無いと lake_temp_offset が 0 になり標高補正が無効化する。
        # (近傍でも 片品42106/榛名山42241 は降水専用で気温欠測になる罠への回帰ガード)
        station = config.JMA_STATIONS[r["location"]]
        assert isinstance(station.get("elevation"), (int, float)), \
            f"{rid} の観測点 {r['location']} に elevation が無い→標高補正が無効化"
        assert config.lake_temp_offset(rid) != 0 or r["elevation"] == station["elevation"]
    # verified 湖が最低1つ (確信GOを出せる)
    assert any(config.REACHES[rid]["source_confidence"] == "verified" for rid in lakes)
