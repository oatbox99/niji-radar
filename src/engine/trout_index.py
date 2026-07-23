"""Trout Suitability Index (TSI) — ニジマス釣行の適性を公開データから推定する v0.

⚠️ TRANSPARENT HEURISTIC, NOT A FITTED MODEL. 閾値は 2026-07 の多軸エビデンス調査
(群馬県水試 / EPA / USGS / 査読 / 青森県産技セ 等の一次情報)から引いた「防御可能な事前分布」で、
神流川の実釣データに較正したものではない。システムは自分の釣果を使わず、他者ブログの現場観測
(体感水温 / 放流 / 濁り / 釣果)で答え合わせする。新しい現場観測はこの推定より優先する。

鮎の G-Index (石垢の蓄積を積分するモデル)とは設計が根本的に異なる:
  ニジマスは変温動物で、摂餌・活性は「水温」で一義的に決まる瞬間条件が主軸。垢のような
  遅い蓄積状態は持たない。したがって TSI は日ごとに独立で、履歴の積分をしない。

TSI の骨格 (乗算モデル。どれか一つが 0 なら釣りにならない):
  TSI = 100 × 水温活性(spine) × 濁り係数(U字) × 光係数(凸)
  - 水温活性: 10–16℃ を満点とする台形。低温側はなだらか、高温側は 20℃ で 0 へ急降下
    (高温は致死・摂餌停止側なので減衰を急峻に)。
  - 濁り: U字応答。笹濁り=最好、クリア=警戒心で減点、泥濁り=0。
  - 光: 凸応答。曇天=有利、快晴=わずかに減点 (日次粒度なので弱ウェイト)。

C&R (キャッチ&リリース) 死亡率は水温で指数的に上がるため、釣果とは別軸の「保全ゲート」として
`cr_release_risk` を用意する。冬期釣場は C&R 前提なので、釣れるか以前に「釣って離して魚が生きるか」で
判定を止めることがある(鮎レーダーに無い魚種特化レイヤ)。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass(frozen=True)
class TroutParams:
    # --- 水温活性の cardinal temps (℃) — 摂餌至適 10–16℃ (群馬県水試/EPA/実釣) ---
    t_min: float = 3.0        # これ未満はほぼ休止 (冬期C&Rは成立するが低活性・スロー)
    t_opt_lo: float = 10.0    # 摂餌スイートスポット下端
    t_opt_hi: float = 16.0    # 摂餌スイートスポット上端
    t_stress: float = 20.0    # 摂餌停止域 (68°F) — ここで活性 0
    t_lethal: float = 24.0    # 生息不適・慢性致死接近
    # --- C&R リリース死亡率ゲート (℃) — 保全レイヤ。慣用20℃より低い16℃から上昇 ---
    cr_caution: float = 16.0     # 死亡率が上がり始める (61°F)
    cr_strong: float = 18.9      # 保守ガイドの中止推奨線 (66°F)
    cr_nogo: float = 20.0        # 実質 catch&kill 境界 (68°F)
    cr_afternoon: float = 22.8   # 73°F 予報日 → 午前のみ (hoot owl)
    # --- 濁り U字係数 (0 clear / 1 笹濁り / 2 泥濁り / None 不明) ---
    turb_clear: float = 0.80     # 高透明=警戒心で減点
    turb_sasa: float = 1.00      # 笹濁り=最好窓
    turb_mud: float = 0.0        # 泥濁り=釣りにならない
    turb_unknown: float = 0.75   # 情報なし=中立やや下 (確信を上げない)
    # --- 光の凸係数 (日照時間ベース・弱ウェイト) ---
    light_overcast: float = 1.00     # 曇天=有利
    light_bluebird: float = 0.88     # 快晴=わずかに減点
    light_unknown: float = 0.95
    sun_bluebird_h: float = 8.0      # 日照これ以上=快晴寄り
    # --- TSI → quality_state の閾値 ---
    tsi_cap: float = 100.0
    good_min: float = 55.0    # 好適
    go_min: float = 68.0      # 絶好 (釣果ゲートの GO 候補)


@dataclass(frozen=True)
class DailyInput:
    date: str                       # YYYY-MM-DD
    sunshine_h: Optional[float]     # daily sunshine hours (JMA)
    water_temp_c: Optional[float]   # proxied from air temp (no live sensor)
    turbidity: Optional[int]        # 0/1/2/None — usually only known for "今日" (semantic)
    is_scour: bool                  # 増水(注意水位+)/泥濁り のあった日


@dataclass(frozen=True)
class TState:
    date: str
    tsi: float                 # 0–tsi_cap (適性メーター)
    temp_activity: float       # 0–1 (水温スパイン)
    water_temp_c: Optional[float]
    quality_state: str         # 絶好 / 好適 / やや低調 / 低活性 / 高水温減退 / 高水温危険 / 増水リセット
    cr_risk: str               # safe / caution / strong / nogo / unknown
    is_scour: bool


def temp_activity(t: Optional[float], p: TroutParams) -> float:
    """水温→摂餌活性 [0,1]。10–16℃ を満点とする非対称台形 (高温側の減衰が急)。"""
    if t is None:
        return 0.0
    if t <= p.t_min or t >= p.t_stress:
        return 0.0
    if t < p.t_opt_lo:                                   # 低温側の立ち上がり
        return (t - p.t_min) / (p.t_opt_lo - p.t_min)
    if t <= p.t_opt_hi:                                  # 至適プラトー
        return 1.0
    return (p.t_stress - t) / (p.t_stress - p.t_opt_hi)  # 高温側の急降下 (16→20℃)


def turbidity_mod(turbidity: Optional[int], p: TroutParams) -> float:
    """濁り→係数 (U字)。笹濁りが最好、泥濁り=0、不明は中立やや下。"""
    if turbidity is None:
        return p.turb_unknown
    return {0: p.turb_clear, 1: p.turb_sasa, 2: p.turb_mud}.get(int(turbidity), p.turb_unknown)


def light_mod(sunshine_h: Optional[float], p: TroutParams) -> float:
    """日照→係数 (凸・弱ウェイト)。曇天=有利、快晴=わずか減点。"""
    if sunshine_h is None:
        return p.light_unknown
    if sunshine_h >= p.sun_bluebird_h:
        return p.light_bluebird
    # 0h(完全曇天/雨)〜bluebird の間を overcast→bluebird で線形補間 (曇天側が高い)
    frac = max(0.0, min(1.0, sunshine_h / p.sun_bluebird_h))
    return p.light_overcast - frac * (p.light_overcast - p.light_bluebird)


def daily_tsi(sunshine_h: Optional[float], water_temp_c: Optional[float],
              turbidity: Optional[int], p: TroutParams) -> float:
    """その日の適性指数 0–100。水温不明なら 0 (盲目では点けない=保守側)。"""
    if water_temp_c is None:
        return 0.0
    tsi = p.tsi_cap * temp_activity(water_temp_c, p) * turbidity_mod(turbidity, p) \
        * light_mod(sunshine_h, p)
    return round(max(0.0, min(p.tsi_cap, tsi)), 1)


def cr_release_risk(water_temp_c: Optional[float], p: TroutParams) -> str:
    """C&R リリース死亡リスク帯 (保全ゲート用)。水温が高いほど危険。"""
    if water_temp_c is None:
        return "unknown"
    if water_temp_c >= p.cr_nogo:
        return "nogo"
    if water_temp_c >= p.cr_strong:
        return "strong"
    if water_temp_c >= p.cr_caution:
        return "caution"
    return "safe"


def classify_quality(tsi: float, water_temp_c: Optional[float], is_scour: bool,
                     p: TroutParams) -> str:
    """(TSI, 水温, 増水) を釣りに関わる状態語へ。"""
    if is_scour:
        return "増水リセット"
    if water_temp_c is not None and water_temp_c >= p.t_lethal:
        return "高水温危険"
    if water_temp_c is not None and water_temp_c >= p.t_stress:
        return "高水温減退"
    if tsi >= p.go_min:
        return "絶好"
    if tsi >= p.good_min:
        return "好適"
    if water_temp_c is not None and water_temp_c < p.t_opt_lo:
        return "低活性"       # 低水温でスロー (冬期C&Rは成立するが渋め)
    return "やや低調"


def compute_series(inputs: List[DailyInput], p: TroutParams) -> List[TState]:
    """日次入力を日ごと独立に TSI 系列へ (鮎と違い積分しない)。"""
    out: List[TState] = []
    for inp in inputs:
        ta = temp_activity(inp.water_temp_c, p)
        tsi = 0.0 if inp.is_scour else daily_tsi(
            inp.sunshine_h, inp.water_temp_c, inp.turbidity, p)
        out.append(TState(
            date=inp.date,
            tsi=round(tsi, 1),
            temp_activity=round(ta, 3),
            water_temp_c=inp.water_temp_c,
            quality_state=classify_quality(tsi, inp.water_temp_c, inp.is_scour, p),
            cr_risk=cr_release_risk(inp.water_temp_c, p),
            is_scour=inp.is_scour,
        ))
    return out


def estimate_water_temp(air_temp: Optional[float]) -> Optional[float]:
    """PROXY water temp from air temp — ニジマス釣場にライブ水温源が無いため気温から換算。

    v0 placeholder ONLY (未較正)。水温がニジマス判定の主軸なので、この換算値は
    「実測」と偽らず必ず未較正プロキシとして扱い、信頼度を下げる。
    """
    if air_temp is None:
        return None
    return round(0.6 * air_temp + 6.0, 1)       # PLACEHOLDER


# ブログ観測の体感コンディション score (0–3) をモデルと同じ語彙へ写像。
# 0 渋い/低活性 / 1 ぼちぼち / 2 好調 / 3 絶好。
OBS_LABELS = {0: "低活性", 1: "やや低調", 2: "好適", 3: "絶好"}


def obs_to_quality(obs_score: Optional[int]) -> Optional[str]:
    """観測された体感コンディション(0–3)をモデルの quality_state 語彙へ。"""
    if obs_score is None:
        return None
    return OBS_LABELS.get(int(obs_score))
