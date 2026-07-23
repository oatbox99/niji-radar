"""静的 config (REACHES・派生ヘルパ・信頼度格付け) の整合テスト。"""
from __future__ import annotations

from src import config

_REQUIRED_KEYS = {
    "label", "river", "location", "water_station", "dams", "methods",
    "catch_release", "season", "closed_weekday", "source_confidence",
    "official_url", "info_url",
}


def test_ui_reaches_all_exist_in_reaches():
    assert set(config.UI_REACHES) <= set(config.REACHES)
    assert config.UI_REACHES[0] == "kanna_ueno"        # verified を先頭に


def test_every_reach_has_required_keys():
    for rid, reach in config.REACHES.items():
        assert _REQUIRED_KEYS <= set(reach), f"{rid} に必須キー欠落"
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
        river = config.REACHES[rid]["river"]
        for name, dam_id in config.reach_dams(rid).items():
            assert config.DAM_DISCHARGE.get(river, {}).get(name) == dam_id
