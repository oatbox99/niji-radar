"""Static source config (ニジマスレーダー). 判定単位は「区間 (reach)」。

⚠️ 妄想エンドポイント禁止 — ここに載せる station ID / URL / ダムID は、鮎レーダーの実地偵察
(2026-07, 各主張を敵対的検証) か、本プロジェクトの多軸エビデンス調査 (2026-07) で実在確認した
ものだけ。未確認のものは値を入れず None + source_confidence を下げる。

精度と網羅の両立: 全区間をアーキテクチャに載せる (網羅) が、各 reach の `source_confidence` で
精度を正直に格付けする。verified = 公式ソース実在確認済みで確信 GO を出せる。参考 = 物理データと
caveat は出すが確信 GO は出さない (鮎レーダーで利根川がブログ源なし→確信GOを出さなかった思想)。
"""
from __future__ import annotations

# --------------------------------------------------------------------------- #
# JMA AMeDAS — the ONLY clean JSON API. 水温プロキシと天候/日照の基盤。
# --------------------------------------------------------------------------- #
JMA_AMEDAS_BASE = "https://www.jma.go.jp/bosai/amedas"
JMA_FORECAST_AREA = "100000"          # 群馬県
JMA_FORECAST_TEMP_AREA = "前橋"
# 予報気温は 前橋 基準 + 標高オフセット (山間ほど冷たい)。
LOCATION_TEMP_OFFSET = {
    "前橋": 0.0, "桐生": 0.0,
    "上野村": -2.5, "中之条": -1.5, "沼田": -1.5,
}
JMA_AMEDAS_PAGE = "https://www.jma.go.jp/bosai/amedas/#amdno={code}"
JMA_FORECAST_PAGE = ("https://www.jma.go.jp/bosai/forecast/"
                     "#area_type=class20s&area_code=100000")

JMA_STATIONS = {
    "上野村": {"code": "42396", "sunshine_estimated": True},   # 神流(日照推計)
    "前橋": {"code": "42251", "sunshine_estimated": False},    # 官署(日照実測)
    "桐生": {"code": "42266", "sunshine_estimated": True},
    "中之条": {"code": "42186", "sunshine_estimated": True, "elevation": 354},   # 吾妻／榛名湖
    "沼田": {"code": "42146", "sunshine_estimated": True, "elevation": 390},
    # 湖用(本プロジェクト調査・JMA amedastable の alt/elems で実在＆気温観測を確認)。
    # elev は観測点標高で、湖面との差を気温減率(LAPSE_C_PER_100M)で表層水温プロキシに補正する。
    # ⚠️ TRAP: 近傍の 片品(42106)・榛名山(42241) は elems=01000000 の降水専用で気温を観測しない。
    #   → 気温観測点(elems 先頭=1)のうち湖に最も近い/標高差の小さいものを採用:
    "藤原": {"code": "42046", "sunshine_estimated": True, "elevation": 700},    # 菅沼/丸沼/大尻沼
    "草津": {"code": "42121", "sunshine_estimated": True, "elevation": 1223},   # 野反湖(近接・高標高で最精度)
    # TRAP: AMeDAS 上野(53112) は三重県 — 群馬・上野村は 神流(42396)。
}

# 気温減率(標高100m当たりの気温低下℃)。観測点→湖面の標高差を表層水温プロキシに反映する。
# ⚠️ 気温減率であって水温補正の妥当性は未検証。標高差が大きい湖ほど誤差大。
LAPSE_C_PER_100M = 0.6

# --------------------------------------------------------------------------- #
# River water level — categorical flood-stage per gauge (Yahoo mirror of 川の防災情報)。
# stations は上流→下流順。神流川・利根川は鮎レーダーで実在検証済みのミラーを流用。
# 吾妻川・渡良瀬川は本プロジェクトで追加 (station 名は要現地確認 → 参考扱い)。
# --------------------------------------------------------------------------- #
RIVER_WATER_LEVEL = {
    "神流川": {
        "yahoo_url": "https://typhoon.yahoo.co.jp/weather/river/8303030696/",
        "primary_station": "万場",
        "stations": ["万場", "鬼石", "若泉", "浄法寺", "勅使河原"],
        "map_upstream": "上流（上野村・最上流）",
        "map_downstream": "下流（神流湖・鬼石）",
    },
    "利根川": {
        "yahoo_url": "https://typhoon.yahoo.co.jp/weather/river/8303030001/",
        "primary_station": "前橋",
        "stations": ["岩本", "前橋", "上福島", "八斗島"],
        "map_upstream": "上流（渋川・沼田方面）",
        "map_downstream": "下流（伊勢崎・八斗島）",
    },
    "吾妻川": {
        # Yahoo river ID 8303030920 (本プロジェクト調査)。station 名は未確認 → 参考。
        "yahoo_url": "https://typhoon.yahoo.co.jp/weather/river/8303030920/",
        "primary_station": "中之条",
        "stations": ["中之条"],
        "map_upstream": "上流（長野原・八ッ場方面）",
        "map_downstream": "下流（渋川・利根川合流）",
    },
    "渡良瀬川": {
        # 水位観測点(高津戸/足利)は Yahoo ミラーID 未確認 → 水位は参考/欠測扱い。
        "yahoo_url": None,
        "primary_station": "高津戸",
        "stations": ["高津戸"],
        "map_upstream": "上流（草木ダム・大間々方面）",
        "map_downstream": "下流（桐生・足利）",
    },
}

# 水防ステージ severity。"注意"(2) 以上 == 増水 == scour/no-go。
LEVEL_SEVERITY = {"平常": 0, "待機": 1, "注意": 2, "避難": 3, "危険": 4, "氾濫": 5}

# --------------------------------------------------------------------------- #
# Dam discharge — 濁り放流リスク。ID は EUC-JP DspDamData から確認したものだけ。
# 下久保(神流川鬼石) = 本プロジェクト確認。利根川上流5基 = 鮎レーダー確認済み流用。
# 八ッ場(吾妻)・草木(渡良瀬) は ID 未確認 → 空 (妄想IDを入れない)。当該 reach は
# ダム放流を「未確認」表示にする。
# --------------------------------------------------------------------------- #
DAM_ENDPOINT = "http://www1.river.go.jp/cgi-bin/DspDamData.exe?ID={id}&KIND=3&PAGE=0"
DAM_DISCHARGE = {
    "利根川": {
        "矢木沢": "1368030375010",
        "奈良俣": "1368030375020",
        "藤原": "1368030375030",
        "相俣": "1368030375090",
        "薗原": "1368030375130",
    },
    "神流川": {
        "下久保": "1368030375210",   # 本プロジェクト確認 (鬼石 reach の濁り支配)
    },
    # "吾妻川": {"八ッ場": "<ID未確認>"},   # ID確認でき次第追加
    # "渡良瀬川": {"草木": "<ID未確認>"},
}
DAM_SURGE_FRACTION = 0.30
DAM_MIN_FLOW_M3S = 30.0
# 前橋方面までのダム距離 (km, 地図目測) と波の celerity 帯。粗い到達目安のみ。
DAM_DIST_KM = {
    "矢木沢": 80.0, "奈良俣": 78.0, "藤原": 70.0, "相俣": 55.0, "薗原": 40.0,
    "下久保": 15.0,   # 下久保→鬼石は近い
}
DAM_CELERITY_MS = (1.5, 2.5)

# --------------------------------------------------------------------------- #
# REACHES — 判定単位。同じ河川名でも上野村(自然流量)と鬼石(下久保ダム支配)は真逆。
# 河川名でなく reach_id で観測点/ダム/営業ルール/釣況源/信頼度を紐付ける。
#   river/location/water_station: 物理データの引き先 (RIVER_WATER_LEVEL/JMA_STATIONS)
#   dams: 濁り放流監視するダム名のリスト ([] = 自然流量で監視不要)
#   methods: 使用可の釣法 (C&R区間は大半エサ禁止)
#   catch_release: True=全C&R / False=一般 (キープ可の一般渓流)
#   season: {"open":(月,日), "close":(月,日)} — 概ねの営業/解禁期間 (正確な日は漁協確認)
#   closed_weekday: 定休曜日 (0=月..6=日) or None
#   source_confidence: "verified" | "参考" | "未確認"
# --------------------------------------------------------------------------- #
REACHES = {
    "kanna_ueno": {
        "label": "神流川 上野村（冬季ハコスチC&R）",
        "river": "神流川",
        "location": "上野村",
        "water_station": "万場",
        "dams": [],                       # 上野村は最上流の自然流量 (下久保は下流=無関係)
        "methods": ["ルアー", "フライ", "テンカラ"],
        "catch_release": True,
        "season": {"open": (10, 15), "close": (2, 28)},   # 概ね10月中旬〜2月下旬
        "closed_weekday": 1,              # 火曜定休 (祝日除く)
        "semantic_source": "上野村漁協速報",
        "official_url": "https://www.ueno-fc.com/winter",
        "info_url": "https://www.ueno-fc.com/infomation",
        "catch_ref_url": "https://www.ueno-fc.com/infomation",
        "source_confidence": "verified",
        "notes": "神流川最上流1.5〜1.7km。シングルバーブレス・全C&R・持ち帰り禁止。",
    },
    "tone_maebashi": {
        "label": "利根川 前橋（冬期ニジマスC&R）",
        "river": "利根川",
        "location": "前橋",
        "water_station": "前橋",
        "dams": ["矢木沢", "奈良俣", "藤原", "相俣", "薗原"],   # 上流放流の濁り前兆
        # C&R区間はエサ(飲み込み=深フッキング)でリリース死亡率が上がるためルアー/フライに限定
        # (kanna_ueno と整合)。正確な許可釣法は群馬漁協で要確認。
        "methods": ["ルアー", "フライ"],
        "catch_release": True,
        "season": {"open": (10, 1), "close": (3, 31)},
        "closed_weekday": None,
        "semantic_source": "群馬漁協釣況",
        "official_url": "https://gunmagyokyo.com/",
        "info_url": "https://gunmagyokyo.com/",
        "catch_ref_url": "https://anglers.jp/areas/2511",
        "source_confidence": "参考",       # 群馬漁協は年度別URL変動 → 確信GOは出さない
        "notes": "本流の冬期ニジマス放流区間。正確な区間/期間は群馬漁協で要確認。",
    },
    "agatsuma_bando": {
        "label": "吾妻川 阪東・子持エリア",
        "river": "吾妻川",
        "location": "中之条",
        "water_station": "中之条",
        "dams": ["八ッ場"],               # ID未確認 → ダム放流は未確認表示
        "methods": ["ルアー", "フライ", "エサ"],
        "catch_release": False,
        "season": {"open": (3, 1), "close": (9, 20)},
        "closed_weekday": None,
        "semantic_source": None,
        "official_url": "https://bando-fc.com/",
        "info_url": "https://bando-fc.com/",
        "catch_ref_url": "https://anglers.jp/",
        "source_confidence": "参考",
        "notes": "八ッ場ダム放流の影響区間。情報が薄く、期間/料金は電話確認前提。",
    },
    "watarase_kiryu": {
        "label": "渡良瀬川 桐生エリア",
        "river": "渡良瀬川",
        "location": "桐生",
        "water_station": "高津戸",
        "dams": ["草木"],                 # ID未確認 → ダム放流は未確認表示
        "methods": ["ルアー", "フライ", "エサ"],
        "catch_release": False,
        "season": {"open": (3, 1), "close": (9, 20)},
        "closed_weekday": None,
        "semantic_source": None,
        "official_url": "http://ryomo-fishing.com/",   # ⚠️SSL失効=http直
        "info_url": "http://ryomo-fishing.com/",
        "catch_ref_url": "https://anglers.jp/",
        "source_confidence": "参考",
        "notes": "草木ダム放流の影響区間。両毛漁協サイトはSSL失効。水位観測点ID未確認。",
    },
    "kanna_oniishi": {
        "label": "神流川 鬼石エリア（下久保ダム下流）",
        "river": "神流川",
        "location": "上野村",             # 気象は神流42396を共用 (近傍)
        "water_station": "鬼石",
        "dams": ["下久保"],               # 濁りは下久保ダム放流が支配
        "methods": ["ルアー", "フライ", "エサ"],
        "catch_release": False,
        "season": {"open": (3, 1), "close": (9, 20)},
        "closed_weekday": None,
        "semantic_source": None,
        "official_url": "https://www.fishpass.co.jp/",
        "info_url": "https://www.fishpass.co.jp/",
        "catch_ref_url": "https://anglers.jp/",
        "source_confidence": "参考",
        "notes": "下久保ダム放流で水位・濁りが人為的に急変。上野村とは真逆の挙動。",
    },

    # ------------------------------------------------------------------- #
    # 湖(止水)。河川と判定軸が別: 増水/濁り/ダム放流は無効、表層水温(標高補正)×季節×
    # ターンオーバー×営業日で判定。⚠️躍層/DO/深度は公開実測源が無く季節推定に留まる(正直に明示)。
    # 河川フィールド(river/water_station/dams)は持たない。elevation=湖面標高、shore_only=岸釣り限定。
    # ------------------------------------------------------------------- #
    "sugenuma": {
        "label": "菅沼（片品・ボートC&R）",
        "waterbody": "lake",
        "location": "藤原",
        "elevation": 1731,
        "shore_only": False,          # 手漕ぎボートのみ
        "methods": ["ルアー", "フライ"],
        "catch_release": True,
        "season": {"open": (6, 1), "close": (10, 31)},   # 指定営業日のみ・要確認
        "closed_weekday": None,
        "semantic_source": None,
        "official_url": "https://sugenuma.com/fishing/",
        "info_url": "https://sugenuma.com/fishing/",
        "catch_ref_url": "https://anglers.jp/",
        "source_confidence": "verified",
        "notes": "標高1731m/水深75m/透明度15m。全C&R・バーブレスシングル・手漕ぎボートのみ。"
                 "深湖で深度戦略が効く。営業は指定日のみ・金精峠開通(4月下旬)〜閉鎖依存。要漁協確認。",
    },
    "marunuma": {
        "label": "丸沼（片品）",
        "waterbody": "lake",
        "location": "藤原",
        "elevation": 1428,
        "shore_only": False,
        "methods": ["ルアー", "フライ", "エサ"],
        "catch_release": False,       # キープ5尾まで(20cm以下はリリース)
        "season": {"open": (4, 25), "close": (11, 30)},
        "closed_weekday": None,
        "semantic_source": None,
        "official_url": "https://www.marunuma.jp/",
        "info_url": "https://www.marunuma.jp/",
        "catch_ref_url": "https://anglers.jp/",
        "source_confidence": "verified",
        "notes": "標高1428m。エサ/ルアー/フライ可・キープ5尾(20cm以下release)。菅沼より入門向け。"
                 "金精峠開通(4月下旬)〜11月末。正確な期間・料金は要確認。",
    },
    "nozorilake": {
        "label": "野反湖（中之条・六合／岸釣り）",
        "waterbody": "lake",
        "location": "草津",
        "elevation": 1513,
        "shore_only": True,           # ボート/カヌー禁止=岸釣りのみ
        "methods": ["ルアー", "フライ", "エサ"],
        "catch_release": False,
        "season": {"open": (5, 1), "close": (11, 10)},
        "closed_weekday": None,
        "semantic_source": None,
        "official_url": "https://www.town.nakanojo.gunma.jp/",
        "info_url": "https://www.town.nakanojo.gunma.jp/",
        "catch_ref_url": "https://anglers.jp/",
        "source_confidence": "verified",
        "notes": "標高1513m。岸釣りのみ→風向・岸アクセス重視、強風時は安全注意。"
                 "5〜10月に月約3回ニジマス放流(放流直後が短期の好機)。遊漁5/1〜11/10。",
    },
    "oshirinuma": {
        "label": "大尻沼（片品・予約制ボートC&R）",
        "waterbody": "lake",
        "location": "藤原",
        "elevation": 1400,
        "shore_only": False,
        "methods": ["ルアー", "フライ"],
        "catch_release": True,
        "season": {"open": (4, 25), "close": (11, 30)},
        "closed_weekday": None,
        "semantic_source": None,
        "official_url": "https://www.marunuma.jp/",   # 受付は環湖荘(丸沼)導線
        "info_url": "https://www.marunuma.jp/",
        "catch_ref_url": "https://anglers.jp/",
        "source_confidence": "参考",
        "notes": "標高1400m。ボート専用・全C&R・予約制。料金/ルールは二次情報のため要確認。",
    },
    "harunako": {
        "label": "榛名湖（高崎）",
        "waterbody": "lake",
        "location": "中之条",
        "elevation": 1084,
        "shore_only": False,
        "methods": ["ルアー", "フライ", "エサ"],
        "catch_release": False,
        "season": {"open": (3, 15), "close": (12, 15)},   # 冬季は結氷でワカサギ氷上へ移行
        "closed_weekday": None,
        "semantic_source": None,
        "official_url": "https://www.gunfish.jp/turiba/harunaba.htm",
        "info_url": "https://www.gunfish.jp/turiba/harunaba.htm",
        "catch_ref_url": "https://anglers.jp/",
        "source_confidence": "参考",
        "notes": "標高1084m。近傍の榛名山AMeDASは気温を観測しないため、気温は中之条(354m)を"
                 "標高差730mの気温減率で補正した推定(誤差大・参考)。ニジマス通年可は二次情報で要現地確認。"
                 "冬季は結氷しワカサギ氷上釣りへ(トラウトは対象外)。",
    },
}

# UI の区間セレクタ順 (河川→湖、各 verified を先頭に)。
UI_REACHES = ["kanna_ueno", "tone_maebashi", "agatsuma_bando",
              "watarase_kiryu", "kanna_oniishi",
              "sugenuma", "marunuma", "nozorilake", "oshirinuma", "harunako"]


# --------------------------------------------------------------------------- #
# Derived ingestion targets — 物理データは (river, location) 単位で重複排除して取得し、
# semantic/stocking は reach 単位で取得する。
# --------------------------------------------------------------------------- #
def unique_locations() -> list:
    """天候取得が必要な AMeDAS location の重複排除リスト。"""
    seen = []
    for r in REACHES.values():
        if r["location"] not in seen:
            seen.append(r["location"])
    return seen


def unique_rivers() -> list:
    """水位/ダム取得が必要な river の重複排除リスト(河川区間のみ・湖は除外)。"""
    seen = []
    for r in REACHES.values():
        if r.get("waterbody", "river") == "river" and r["river"] not in seen:
            seen.append(r["river"])
    return seen


def reach_dams(reach_id: str) -> dict:
    """reach が監視するダムの {name: id} (ID未確認/湖は除外)。"""
    reach = REACHES[reach_id]
    river_dams = DAM_DISCHARGE.get(reach.get("river", ""), {})
    return {name: river_dams[name] for name in reach.get("dams", []) if name in river_dams}


def reach_dam_names(reach_id: str) -> list:
    """reach が監視するダム名 (ID有無を問わず。未確認の明示に使う)。"""
    return list(REACHES[reach_id].get("dams", []))


def is_lake(reach_id: str) -> bool:
    return REACHES[reach_id].get("waterbody", "river") == "lake"


def lake_temp_offset(reach_id: str) -> float:
    """湖: 観測点→湖面の標高差を気温減率で表層水温プロキシに反映するオフセット(℃)。

    観測点より湖面が高ければ負(冷たい)。⚠️気温減率であり水温補正の妥当性は未検証。
    """
    reach = REACHES[reach_id]
    st = JMA_STATIONS.get(reach["location"], {})
    st_elev = st.get("elevation")
    lake_elev = reach.get("elevation")
    if st_elev is None or lake_elev is None:
        return 0.0
    return -LAPSE_C_PER_100M * (lake_elev - st_elev) / 100.0
