"""Go/No-Go 合成 (ニジマス・区間単位) — 公開データのみ、現場観測 > モデル。

鮎レーダーのフラットな hazard 優先を、ニジマス向けに 4 段ゲート階層へ精緻化する。
上位ゲートが NO_GO なら下位を評価せず即 NO_GO:
  ゲート0 安全   : 増水/泥濁り/ダム放流 (categorical stage で近似。深さ×流速の実測は無い)
  ゲート1 合法/営業: 禁漁期/営業期間外/定休/データ未取得
  ゲート2 魚の生存 : C&R リリース死亡 (水温≥20℃)・生息不適 (24℃接近)・午後クローズ (>22.8℃)
  ゲート3 釣果    : 水温適性×濁り×放流後経過 の合成 (GO/CAUTION)

精度と網羅: 各 reach の source_confidence が verified 未満なら、GO は CAUTION に格下げする
(参考レベルのデータで確信 GO を出さない — 鮎レーダーで利根川が確信GOを出さなかった思想)。
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import config, db
from .engine.trout_index import (
    DailyInput,
    TroutParams,
    TState,
    compute_series,
    cr_release_risk,
    estimate_water_temp,
    obs_to_quality,
)

GO, CAUTION, NO_GO = "GO", "CAUTION", "NO_GO"
FRESH_HAZARD_DAYS = 7    # 泥濁り報告がこれより古ければハード NO_GO を強制しない
OBS_FRESH_DAYS = 10      # 体感コンディション観測がこれより古ければ非権威
STOCK_BOOST_DAYS = 3     # 放流からこの日数以内は「荒食い」ブースト
# スレ(放流14日超で減衰)の閾値は guide.fishing_tips 側に持つ(表示のみでゲートに影響しない)。


@dataclass
class Verdict:
    reach_id: str
    reach_label: str
    waterbody: str
    level: str
    headline: str
    mood: str
    tsi: Optional[float]                 # 適性メーター 0–100 (model)
    model_quality: Optional[str]
    observed_quality: Optional[str]
    effective_quality: Optional[str]
    quality_source: str
    cr_risk: Optional[str]               # safe/caution/strong/nogo/unknown
    observed_catch: Optional[int]
    turbidity: Optional[int]
    water_status: Optional[str]
    water_trend: Optional[str]
    air_temp: Optional[float]
    max_temp: Optional[float]
    sunshine_h: Optional[float]
    sunshine_estimated: bool
    water_temp_proxy: Optional[float]
    confidence: float
    staleness_days: Optional[int]
    source_confidence: str
    methods: List[str]
    catch_release: bool
    days_since_stock: Optional[int] = None
    stock_date: Optional[str] = None
    reasons: List[str] = field(default_factory=list)
    caveats: List[str] = field(default_factory=list)
    series: List[TState] = field(default_factory=list)
    stations: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    dam_risk: Optional[Dict[str, Any]] = None
    as_of: Optional[str] = None
    outlook: Optional[Dict[str, Any]] = None
    days_since_scour: Optional[int] = None
    last_scour_date: Optional[str] = None
    observed_excerpt: Optional[str] = None
    observed_confidence: Optional[float] = None
    observed_post_date: Optional[str] = None
    observed_water_temp: Optional[float] = None


# --------------------------------------------------------------------------- #
# 営業/合法ゲート (季節窓 + 定休)
# --------------------------------------------------------------------------- #
def _in_window(month: int, day: int, om: int, od: int, cm: int, cd: int) -> bool:
    """(month,day) が [open, close] の窓内か。冬期C&R(10/15→2/28)の年跨ぎに対応。"""
    cur, op, cl = (month, day), (om, od), (cm, cd)
    if op <= cl:                       # 同一年内 (例 3/1〜9/20)
        return op <= cur <= cl
    return cur >= op or cur <= cl       # 年跨ぎ (例 10/15〜2/28)


def reach_open(reach: Dict[str, Any], today_iso: str) -> Dict[str, Any]:
    """区間が本日 営業/解禁 かを判定。{"open":bool, "reason":str|None}。"""
    d = dt.date.fromisoformat(today_iso)
    wd = reach.get("closed_weekday")
    if wd is not None and d.weekday() == wd:
        names = ["月", "火", "水", "木", "金", "土", "日"]
        return {"open": False, "reason": f"{names[wd]}曜定休です（祝日は営業の場合あり／要確認）"}
    s = reach["season"]
    if not _in_window(d.month, d.day, s["open"][0], s["open"][1], s["close"][0], s["close"][1]):
        if reach.get("waterbody") == "lake":
            return {"open": False,
                    "reason": "湖の営業/解禁期間外の可能性が高い時期です"
                              "（高標高湖は結氷・道路閉鎖の季節。正確な期間は要現地確認）"}
        if reach["catch_release"]:
            return {"open": False,
                    "reason": "冬期C&Rの営業期間外の可能性が高い時期です（正確な期間は漁協でご確認を）"}
        return {"open": False,
                "reason": "一般渓流の禁漁期（概ね9/21〜翌2月末）の可能性が高い時期です（要漁協確認）"}
    return {"open": True, "reason": None}


# --------------------------------------------------------------------------- #
# DB → model assembly
# --------------------------------------------------------------------------- #
def _scour_dates(conn: sqlite3.Connection, river: str, station: str) -> set:
    """この reach の scour 日: primary gauge が 注意水位+、または reach の泥濁り報告。"""
    dates = set()
    for r in conn.execute(
        "SELECT date_time, water_level_status FROM river_physical_data "
        "WHERE river_name = ? AND station = ?", (river, station),
    ):
        if config.LEVEL_SEVERITY.get(r["water_level_status"] or "", 0) >= 2:
            dates.add(r["date_time"][:10])
    return dates


def load_daily_inputs(conn: sqlite3.Connection, location: str, river: str,
                      station: str) -> List[DailyInput]:
    """weather_data + scour フラグから TSI 日次入力を組む (turbidity は履歴不明=None)。"""
    scour = _scour_dates(conn, river, station)
    rows = conn.execute(
        "SELECT date, sunshine_hours, mean_temp FROM weather_data "
        "WHERE location = ? ORDER BY date", (location,),
    ).fetchall()
    return [
        DailyInput(
            date=r["date"],
            sunshine_h=r["sunshine_hours"],
            water_temp_c=estimate_water_temp(r["mean_temp"]),
            turbidity=None,
            is_scour=r["date"] in scour,
        )
        for r in rows
    ]


def load_forecast_inputs(conn: sqlite3.Connection, location: str) -> List[DailyInput]:
    rows = conn.execute(
        "SELECT date, sunshine_hours, mean_temp, is_scour FROM forecast_data "
        "WHERE location = ? ORDER BY date", (location,),
    ).fetchall()
    return [
        DailyInput(date=r["date"], sunshine_h=r["sunshine_hours"],
                   water_temp_c=estimate_water_temp(r["mean_temp"]),
                   turbidity=None, is_scour=bool(r["is_scour"]))
        for r in rows
    ]


def _weekend_outlook(states: List[TState]) -> Optional[Dict[str, Any]]:
    """予報期間の TSI をまとめる (トレンド + 次の好機)。"""
    if not states:
        return None
    weekend = []
    for s in states:
        wd = dt.date.fromisoformat(s.date).weekday()
        if wd in (5, 6):
            weekend.append({"date": s.date, "wd": "土" if wd == 5 else "日",
                            "tsi": s.tsi, "quality": s.quality_state})
    first, last = states[0].tsi, states[-1].tsi
    trend = "上向き📈" if last > first + 5 else "下向き📉" if last < first - 5 else "横ばい➡️"
    best = max(states, key=lambda s: s.tsi)
    good = ("絶好", "好適")
    next_good = next((s for s in states if s.quality_state in good and not s.is_scour), None)
    return {
        "trend": trend, "weekend": weekend,
        "best": {"date": best.date, "tsi": best.tsi, "quality": best.quality_state},
        "next_good": ({"date": next_good.date, "tsi": next_good.tsi,
                       "quality": next_good.quality_state} if next_good else None),
        "scours": [s.date for s in states if s.is_scour],
    }


def latest_station_statuses(conn: sqlite3.Connection, river: str) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for name in config.RIVER_WATER_LEVEL.get(river, {}).get("stations", []):
        row = conn.execute(
            "SELECT water_level_status, water_trend, date_time FROM river_physical_data "
            "WHERE river_name = ? AND station = ? ORDER BY date_time DESC LIMIT 1",
            (river, name),
        ).fetchone()
        out[name] = dict(row) if row else {"water_level_status": None, "water_trend": None}
    return out


def _latest(conn: sqlite3.Connection, sql: str, params: tuple) -> Optional[Dict[str, Any]]:
    row = conn.execute(sql, params).fetchone()
    return dict(row) if row else None


def dam_eta_hours(dam_name: str) -> Optional[tuple]:
    dist = config.DAM_DIST_KM.get(dam_name)
    if dist is None:
        return None
    c_lo, c_hi = config.DAM_CELERITY_MS
    return round(dist * 1000.0 / (c_hi * 3600.0)), round(dist * 1000.0 / (c_lo * 3600.0))


def dam_eta_text(dam_name: str) -> str:
    eta = dam_eta_hours(dam_name)
    if eta is None:
        return "到達時間は不明です（距離データなし）"
    lo, hi = eta
    return (f"約{lo}〜{hi}時間で下流の釣り場へ到達する恐れがあります"
            f"（距離{config.DAM_DIST_KM[dam_name]:.0f}km・粗い目安）")


def dam_risk(conn: sqlite3.Connection, reach_id: str) -> Optional[Dict[str, Any]]:
    """reach が監視するダムの放流急増リスク。ID未確認ダムは monitored に残し欠測として扱う。

    None = 監視ダムが無い(自然流量)。dams_seen==0 = 監視対象だがデータ未取得(不明,≠平穏)。
    """
    monitored = config.reach_dam_names(reach_id)     # 名前ベース (ID有無問わず)
    if not monitored:
        return None
    reach = config.REACHES[reach_id]
    river = reach["river"]
    dams_with_id = config.reach_dams(reach_id)        # ID確認済みのみ
    worst, seen, slope_known = None, 0, 0
    for name in dams_with_id:
        row = conn.execute(
            "SELECT discharge, slope_pct FROM dam_data "
            "WHERE river_name = ? AND dam = ? ORDER BY date_time DESC LIMIT 1",
            (river, name),
        ).fetchone()
        if row is None:
            continue
        seen += 1
        sp, disc = row["slope_pct"], row["discharge"]
        if sp is not None:
            slope_known += 1
        if sp is not None and disc is not None and sp >= config.DAM_SURGE_FRACTION \
                and disc >= config.DAM_MIN_FLOW_M3S:
            if worst is None or sp > worst["slope_pct"]:
                worst = {"dam": name, "slope_pct": sp, "discharge": disc,
                         "eta_hours": dam_eta_hours(name), "eta_text": dam_eta_text(name)}
    return {"risk": worst is not None, "worst": worst, "monitored": monitored,
            "dams_seen": seen, "slope_known": slope_known,
            "id_missing": [n for n in monitored if n not in dams_with_id]}


def days_since_stock(conn: sqlite3.Connection, reach_id: str,
                     as_of: Optional[str]) -> Dict[str, Any]:
    """最新の放流日からの経過日数 (管理C&R区間のコア特徴量)。{"days":int|None,"date":str|None}。"""
    row = conn.execute(
        "SELECT stock_date FROM stocking_data WHERE reach_id = ? "
        "ORDER BY stock_date DESC LIMIT 1", (reach_id,),
    ).fetchone()
    if row is None or not as_of:
        return {"days": None, "date": None}
    d = _staleness_days(row["stock_date"], as_of)
    return {"days": d, "date": row["stock_date"]}


def _staleness_days(post_date: Optional[str], today: str) -> Optional[int]:
    if not isinstance(post_date, str):
        return None
    try:
        d0 = dt.date.fromisoformat(post_date[:10].replace("/", "-"))
        d1 = dt.date.fromisoformat(today)
    except ValueError:
        return None
    return max(0, (d1 - d0).days)


# --------------------------------------------------------------------------- #
# Verdict logic — 4-gate hierarchy
# --------------------------------------------------------------------------- #
def build_verdict(
    reach_id: str,
    latest: Optional[TState],
    series: List[TState],
    sem: Optional[Dict[str, Any]],
    water: Optional[Dict[str, Any]],
    wx: Optional[Dict[str, Any]],
    p: TroutParams,
    *,
    today: Optional[str] = None,
    open_state: Optional[Dict[str, Any]] = None,
    dam: Optional[Dict[str, Any]] = None,
    stock: Optional[Dict[str, Any]] = None,
) -> Verdict:
    """純寄りの合成 (today 注入可)。"""
    today = today or dt.date.today().isoformat()
    reach = config.REACHES[reach_id]
    src_conf = reach.get("source_confidence", "参考")

    model_quality = latest.quality_state if latest else None
    tsi = latest.tsi if latest else None
    turb = sem.get("turbidity_score") if sem else None
    observed_catch = sem.get("catch_report") if sem else None
    observed_excerpt = sem.get("raw_excerpt") if sem else None
    observed_confidence = sem.get("confidence") if sem else None
    observed_post_date = sem.get("source_post_date") if sem else None
    observed_water_temp = sem.get("water_temp_obs") if sem else None
    w_status = water.get("water_level_status") if water else None
    w_trend = water.get("water_trend") if water else None
    sev = config.LEVEL_SEVERITY.get(w_status or "", 0)
    water_known = water is not None and w_status is not None
    stale = _staleness_days(observed_post_date, today)

    air_temp = wx.get("mean_temp") if wx else None
    max_temp = wx.get("max_temp") if wx else None
    sunshine_h = wx.get("sunshine_hours") if wx else None
    sunshine_est = bool(wx.get("sunshine_estimated", 1)) if wx else True
    # 現地水温観測があれば水温はそれを優先 (プロキシより実測寄り)、無ければ気温プロキシ。
    water_temp_proxy = estimate_water_temp(air_temp)
    eff_water_temp = observed_water_temp if observed_water_temp is not None else water_temp_proxy
    cr = cr_release_risk(eff_water_temp, p)

    # 体感コンディション観測をモデルより優先。
    obs_cond = sem.get("cond_score") if sem else None
    obs_fresh = stale is not None and stale <= OBS_FRESH_DAYS
    observed_quality = obs_to_quality(obs_cond) if (obs_fresh and obs_cond is not None) else None
    effective_quality = observed_quality or model_quality
    quality_source = f"現場報告({stale}日前)" if observed_quality else "TSI予想"

    stock_days = stock.get("days") if stock else None
    stock_date = stock.get("date") if stock else None
    mud_fresh = turb == 2 and (stale is None or stale <= FRESH_HAZARD_DAYS)
    afternoon_gate = max_temp is not None and estimate_water_temp(max_temp) is not None \
        and estimate_water_temp(max_temp) >= p.cr_afternoon and reach["catch_release"]

    reasons: List[str] = []
    caveats: List[str] = []
    open_state = open_state or reach_open(reach, today)

    # ---- Gate 0: 安全ハードゲート (categorical stage で近似) ----
    if mud_fresh:
        level = NO_GO
        reasons.append("泥濁り（激濁り）で、竿を出せる状態ではありません")
    elif sev >= 2:
        level = NO_GO
        reasons.append(f"増水しています（{w_status}水位）。安全のため川に近づかないでください")
    elif dam and dam.get("risk"):
        level = NO_GO
        w = dam["worst"]
        pct = "急増" if w["slope_pct"] >= 900 else f"+{int(w['slope_pct'] * 100)}%"
        reasons.append(f"上流の{w['dam']}ダムが放流{pct}です（濁り放流リスク・{w['eta_text']}）")
    # ---- Gate 1: 合法/営業ゲート ----
    elif not open_state["open"]:
        level = NO_GO
        reasons.append(open_state["reason"])
    # ---- Gate 2: 魚の生存ゲート (C&R 保全・魚種特化) — 水位欠測の様子見より優先 ----
    # 致死的な高水温は、水位ステージが取れていなくても魚のため NO_GO にする。
    elif reach["catch_release"] and cr == "nogo":
        level = NO_GO
        reasons.append(f"水温が高く（推定{eff_water_temp:.0f}℃）、リリースした魚が死ぬリスクが高い日です"
                       "（C&Rが実質キャッチ&キルになる水温）。魚のために見送りをおすすめします")
    elif eff_water_temp is not None and eff_water_temp >= p.t_lethal:
        level = NO_GO
        reasons.append(f"水温が生息不適域（推定{eff_water_temp:.0f}℃）に接近しています。魚に致命的です")
    elif not water_known:
        level = CAUTION
        reasons.append("水位情報が取得できていません（安全側でGOを保留します）")
    # ---- Gate 3: 釣果スコア ----
    elif effective_quality in ("絶好", "好適") and (turb in (0, 1)) \
            and sev == 0 and w_trend != "上昇" and cr in ("safe", "caution"):
        level = GO
        turb_word = "クリア" if turb == 0 else "笹濁り" if turb == 1 else "濁り不明"
        base = f"{quality_source}: {effective_quality}・{turb_word}・平水です"
        if stock_days is not None and stock_days <= STOCK_BOOST_DAYS:
            base += f"／{stock_days}日前に放流（荒食いの大チャンス）"
        reasons.append(base)
    elif effective_quality in ("高水温減退", "高水温危険"):
        level = CAUTION
        reasons.append("水温が高めで食いが渋い時間帯があります。朝夕の涼しい時間に絞ってください")
    elif effective_quality == "低活性":
        level = CAUTION
        reasons.append("低水温で活性は低めです（冬期はスロー・ボトム狙いが基本）。ひと工夫で拾えます")
    else:
        level = CAUTION
        reasons.append(f"{quality_source}: {effective_quality or '不明'}で、決め手に欠けます")

    # 午後クローズ (hoot owl) — その日GO/CAUTIONでも高水温予報なら午後を切る注意。
    if afternoon_gate and level in (GO, CAUTION):
        caveats.append("日中の最高水温が高くなる予報です。C&Rは午前の涼しい時間に留め、"
                       "午後（水温が上がる時間）は魚のために控えめに")

    # 観測とモデルの不一致を明記。
    if observed_quality and model_quality and observed_quality != model_quality:
        caveats.append(f"モデル予想（{model_quality}）と現場報告（{observed_quality}）が"
                       "一致していません → 現場報告を優先します")

    # ---- source_confidence ゲート: 参考レベルは確信GOを出さない ----
    if level == GO and src_conf != "verified":
        level = CAUTION
        caveats.append(f"この区間はデータ源が『{src_conf}』レベルのため、確信を持ったGOは出していません"
                       "（物理データと季節から様子見までの表示に留めます。現地確認を前提に）")

    # ---- confidence ----
    conf = 0.95
    conf -= 0.15
    if observed_water_temp is not None:
        caveats.append(f"水温は現場報告値（ブログ記載の{observed_water_temp:.0f}℃）を優先しています"
                       "（ブログ主の計測か体感かは区別できないため参考値）")
    else:
        caveats.append("水温はライブ計測源が無いため、気温からの換算値です（未較正プロキシ）")
    if src_conf != "verified":
        conf -= 0.15
    if sunshine_est:
        conf -= 0.03
    if turb is None:
        conf -= 0.15
        caveats.append("直近の濁り・現場情報がありません（ブログ未取得）")
    if wx and (wx.get("sunshine_hours") is None or wx.get("mean_temp") is None):
        conf -= 0.10
        caveats.append("最新日の気象データが欠測です")
    if not water_known:
        conf -= 0.15
    if stale is None:
        conf -= 0.10
    elif stale > 14:
        conf -= 0.25
        caveats.append(f"現場情報が{stale}日前と古めです")
    elif stale > 7:
        conf -= 0.15
        caveats.append(f"現場情報は{stale}日前のものです")
    if dam and dam.get("id_missing"):
        caveats.append("上流ダム（" + "・".join(dam["id_missing"]) +
                       "）の放流データは未取得です（濁り放流の判定に空白があります）")
    caveats.append("水温閾値は公開エビデンスからの推定です（実釣データによる較正は未実施）")
    conf = max(0.10, min(0.95, conf))

    mood, headline = _mood_and_headline(level, effective_quality, cr, turb, w_status,
                                        stale, reach)

    return Verdict(
        reach_id=reach_id, reach_label=reach["label"],
        waterbody=reach.get("waterbody", "river"), level=level, headline=headline,
        mood=mood, tsi=tsi, model_quality=model_quality, observed_quality=observed_quality,
        effective_quality=effective_quality, quality_source=quality_source, cr_risk=cr,
        observed_catch=observed_catch, turbidity=turb, water_status=w_status,
        water_trend=w_trend, air_temp=air_temp, max_temp=max_temp, sunshine_h=sunshine_h,
        sunshine_estimated=sunshine_est, water_temp_proxy=water_temp_proxy,
        confidence=round(conf, 2), staleness_days=stale, source_confidence=src_conf,
        methods=reach["methods"], catch_release=reach["catch_release"],
        days_since_stock=stock_days, stock_date=stock_date,
        reasons=reasons, caveats=caveats, series=series, dam_risk=dam,
        observed_excerpt=observed_excerpt, observed_confidence=observed_confidence,
        observed_post_date=observed_post_date, observed_water_temp=observed_water_temp,
    )


def _mood_and_headline(level, quality, cr, turb, w_status, stale, reach):
    if level == NO_GO:
        if turb == 2:
            return "grumpy", "本日は見送りをおすすめします。川が泥濁りの状態です"
        if cr == "nogo" and reach["catch_release"]:
            return "grumpy", "水温が高く、リリースした魚が死ぬリスクが高い日です。魚のために見送りを"
        return "grumpy", "コンディションが厳しい状況です。無理は禁物です"
    if level == GO:
        if quality == "絶好":
            return "ecstatic", "絶好です！活性が高く、数釣りが期待できます🎣✨"
        return "ecstatic", "良いコンディションです。今週末は好機です🎣"
    if quality in ("高水温減退", "高水温危険"):
        return "neutral", "水温が高めです。朝夕の涼しい時間に絞ってお楽しみください"
    if quality == "低活性":
        return "sleepy", "低水温で活性は控えめです。スローに、丁寧に誘ってみてください"
    if stale is None or stale > 14:
        return "sleepy", "現場情報が少なめです。物理データで安全側に判定しています。現地確認を前提に"
    return "neutral", "判断の難しい状況です。行かれる場合は現地確認を前提にしてください"


_TURB_WORD = {0: "クリア", 1: "笹濁り", 2: "泥濁り", None: "情報なし"}


def _lake_checklist(v: Verdict) -> List[Dict[str, Any]]:
    """湖用 GO ゲート(増水/濁り/水位でなく 営業/表層水温/深度アクセス)。"""
    p = TroutParams()
    surf = v.water_temp_proxy
    open_ok = reach_open(config.REACHES[v.reach_id],
                         v.as_of or dt.date.today().isoformat())["open"]
    # 湖 GO(_lake_report line 623)も cr in (safe,caution) を必須にしている → SSOT を合わせる。
    cr_ok = v.cr_risk in ("safe", "caution")
    shallow = surf is not None and surf <= p.t_stress          # 表層水温が浅場向き
    return [
        {"label": "営業/解禁期間内", "ok": open_ok, "detail": config.REACHES[v.reach_id]["label"]},
        {"label": "表層水温がC&Rに安全" if v.catch_release else "表層水温が適域",
         "ok": cr_ok, "unknown": v.cr_risk == "unknown", "detail": f"C&Rリスク: {v.cr_risk or '不明'}"},
        {"label": "表層が適水温（浅場で狙える）", "ok": v.effective_quality in ("好適", "絶好"),
         "unknown": surf is None,
         "detail": (f"表層 推定{surf:.0f}℃" if surf is not None else "推定不可")},
        # 躍層/DO/深度は未実測プロキシ → 魚の居場所を断定しない(「可能性」「要探索」表現に留める)。
        {"label": "浅場で狙える可能性（表層水温ベース）", "ok": shallow, "unknown": surf is None,
         "detail": ("表層水温は浅場向き（躍層・DO未実測のため要探索）" if shallow
                    else "表層が高く魚は深場寄り（要深度探索）")},
    ]


def go_checklist(v: Verdict) -> List[Dict[str, Any]]:
    """GO ゲートを初心者向け ✅/❌ に。build_verdict のゲートと SSOT。"""
    if v.waterbody == "lake":
        return _lake_checklist(v)
    sev = config.LEVEL_SEVERITY.get(v.water_status or "", 0)
    dam = v.dam_risk
    dam_hit = bool(dam and dam.get("risk"))
    dam_unread = bool(dam and not dam_hit
                      and (dam.get("slope_known", 0) < dam.get("dams_seen", 0)
                           or dam.get("id_missing")))
    hazard_ok = v.turbidity != 2 and sev < 2 and not dam_hit and v.water_status is not None
    if not hazard_ok:
        hazard_detail = ("泥濁り" if v.turbidity == 2 else "増水" if sev >= 2 else
                         "上流ダム放流" if dam_hit else "水位ステージ未取得")
    elif dam_unread:
        hazard_detail = "泥濁り・増水なし／水位取得済（上流ダム放流は一部欠測・要現地確認）"
    else:
        hazard_detail = "泥濁り・増水・ダム放流なし／水位取得済"
    # build_verdict Gate3 は catch_release に関わらず cr in (safe,caution) を GO 必須にしている
    # (line 370-371)。SSOT を保つため、非C&R でも水温不明(unknown)/高水温(strong/nogo)は非グリーン。
    cr_ok = v.cr_risk in ("safe", "caution")
    return [
        {"label": "危険がない", "ok": hazard_ok, "detail": hazard_detail,
         "unknown": (not hazard_ok and v.water_status is None
                     and v.turbidity != 2 and sev < 2 and not dam_hit)},
        {"label": "営業/解禁期間内", "ok": reach_open(config.REACHES[v.reach_id],
                                                 v.as_of or dt.date.today().isoformat())["open"],
         "detail": config.REACHES[v.reach_id]["label"]},
        {"label": "水温がC&Rに安全" if v.catch_release else "水温が適域",
         "ok": cr_ok, "unknown": v.cr_risk == "unknown",
         "detail": f"C&Rリスク: {v.cr_risk or '不明'}"},
        {"label": "コンディション良好以上", "ok": v.effective_quality in ("好適", "絶好"),
         "unknown": v.effective_quality is None,
         "detail": f"現状: {v.effective_quality or '不明'}"},
        {"label": "濁りOK（クリア/笹濁り）", "ok": v.turbidity in (0, 1),
         "unknown": v.turbidity is None,
         "detail": ("情報なし — 現地で水の色を目視確認してください"
                    if v.turbidity is None else _TURB_WORD.get(v.turbidity))},
        {"label": "水位が平常（上昇していない）",
         "ok": sev == 0 and v.water_status is not None and v.water_trend != "上昇",
         "unknown": v.water_status is None,
         "detail": (v.water_status or "不明（未取得）")
         + ("・上昇中" if v.water_trend == "上昇" else "")},
    ]


# --------------------------------------------------------------------------- #
# Lake path (止水: 増水/濁り/ダム放流は無効。表層水温(標高補正)×季節×営業で判定)
# --------------------------------------------------------------------------- #
# ⚠️ 躍層・DO・魚の居る深度は公開実測源が無く、季節からの推定に留まる(最大の弱点)。
# 表層水温も気温+標高減率補正の二段推定で、標高差が大きい湖ほど誤差が大きい。
def _lake_phase(month: int) -> Dict[str, Any]:
    """湖の季節フェーズ(表層推定を補正する係数と一言)。エビデンス: アイスアウト/ターンオーバー窓。"""
    if month in (4, 5):
        return {"phase": "アイスアウト・春循環", "factor": 1.0,
                "note": "雪解け・氷解直後は浅場(〜3m)に集中する年間屈指の好機。ただし循環進行中は一時食い渋りも。"}
    if month in (6,):
        return {"phase": "春本番", "factor": 1.0, "note": "表層が適水温で浅場・ショアが狙いやすい時期です。"}
    if month in (7, 8, 9):
        return {"phase": "盛夏・成層", "factor": 0.8,
                "note": "水温躍層ができ、魚は躍層直上の中層〜深場へ。表層が高いほど深度探索が要ります(朝夕まづめ有利)。"}
    if month in (10, 11):
        return {"phase": "秋循環", "factor": 1.0,
                "note": "秋のターンオーバーで全層が混ざり、浅場に魚が戻る好機。進行中は一時食い渋りも。"}
    return {"phase": "厳寒・端境", "factor": 0.7,
            "note": "低水温で低活性。高標高湖は結氷・道路閉鎖の時期です(現地・ライブカメラで確認を)。"}


def _lake_report(conn: sqlite3.Connection, reach_id: str, p: TroutParams,
                 today: Optional[str]) -> Verdict:
    today = today or dt.date.today().isoformat()
    reach = config.REACHES[reach_id]
    location = reach["location"]
    src_conf = reach.get("source_confidence", "参考")
    offset = config.lake_temp_offset(reach_id)

    rows = conn.execute(
        "SELECT date, sunshine_hours, mean_temp, max_temp FROM weather_data "
        "WHERE location = ? ORDER BY date", (location,)).fetchall()
    inputs = [DailyInput(date=r["date"], sunshine_h=r["sunshine_hours"],
                         water_temp_c=estimate_water_temp((r["mean_temp"] + offset)
                                                          if r["mean_temp"] is not None else None),
                         turbidity=None, is_scour=False) for r in rows]
    as_of = inputs[-1].date if inputs else None
    fc = [DailyInput(date=r["date"], sunshine_h=r["sunshine_hours"],
                     water_temp_c=estimate_water_temp((r["mean_temp"] + offset)
                                                      if r["mean_temp"] is not None else None),
                     turbidity=None, is_scour=False)
          for r in conn.execute("SELECT date, sunshine_hours, mean_temp FROM forecast_data "
                                "WHERE location = ? ORDER BY date", (location,)).fetchall()
          if as_of is None or r["date"] > as_of]
    series = compute_series(inputs + fc, p)
    n_actual = len(inputs)
    latest = series[n_actual - 1] if n_actual else (series[-1] if series else None)

    surf = latest.water_temp_c if latest else None
    tsi = latest.tsi if latest else None
    model_quality = latest.quality_state if latest else None
    cr = cr_release_risk(surf, p)
    open_state = reach_open(reach, today)
    m = dt.date.fromisoformat(today).month
    phase = _lake_phase(m)
    shore = reach.get("shore_only", False)
    stock = days_since_stock(conn, reach_id, as_of)
    stock_days = stock.get("days")

    reasons: List[str] = []
    caveats: List[str] = []

    if not open_state["open"]:
        level = NO_GO
        reasons.append(open_state["reason"])
    elif reach["catch_release"] and cr == "nogo":
        level = NO_GO
        reasons.append(f"表層水温が高く（推定{surf:.0f}℃）、リリースした魚が死ぬリスクが高い日です。"
                       "魚のために見送りをおすすめします")
    elif surf is not None and surf >= p.t_lethal:
        level = NO_GO
        reasons.append(f"表層水温が生息不適域（推定{surf:.0f}℃）に接近しています")
    elif surf is None:
        level = CAUTION
        reasons.append("気象データが取得できず表層水温を推定できません（安全側でGOを保留）")
    elif surf > p.t_stress:
        level = CAUTION
        deep = ("岸釣り限定のこの湖では厳しめです。朝夕まづめの浅場・流れ込みに絞ってください"
                if shore else "ボート/ディープ（トローリング等）で躍層直上の冷水層を狙ってください")
        reasons.append(f"表層が高水温（推定{surf:.0f}℃）で、ニジマスは深場へ移動しています。{deep}")
    elif model_quality in ("絶好", "好適") and cr in ("safe", "caution"):
        level = GO
        base = f"表層が適水温（推定{surf:.0f}℃）です。{phase['note']}"
        if stock_days is not None and stock_days <= STOCK_BOOST_DAYS:
            base += f"／{stock_days}日前に放流（好機）"
        reasons.append(base)
    elif surf is not None and surf < p.t_opt_lo:
        level = CAUTION
        reasons.append(f"低水温（推定{surf:.0f}℃）で活性は低めです。{phase['note']}")
    else:
        level = CAUTION
        reasons.append(f"表層水温は推定{surf:.0f}℃。{phase['note']}")

    if level == GO and src_conf != "verified":
        level = CAUTION
        caveats.append(f"この湖はデータ源が『{src_conf}』レベルのため、確信を持ったGOは出していません"
                       "（正確な期間・ルール・ニジマスの成立は要現地確認）")

    # confidence — 湖は「会場検証(source_confidence)」と「水温データ品質」を分離して評価する。
    # verified でも表層水温は気温＋標高補正の二段推定なので、確信を高くしすぎない(上限も抑える)。
    conf = 0.95 - 0.15                       # 気温プロキシ
    conf -= 0.15                             # 深度/DO/躍層が実測でない(湖固有の大きな不確実性)
    conf -= 0.02 * abs(offset)              # 標高補正が大きいほど連続的に減点(外挿誤差)
    if src_conf != "verified":
        conf -= 0.15
    if surf is None:
        conf -= 0.20
    conf = max(0.10, min(0.85, conf))       # 湖は実測水温源が無いため上限0.85(河川0.95より低い)

    caveats.append("水温は気温＋標高補正からの推定で未較正です（『実測』ではありません）")
    caveats.append("躍層・溶存酸素・魚の居る深度は公開の実測源が無く、季節からの推定に留まります"
                   "（湖判定の最大の弱点。中層を刻んで探るのが基本）")
    if reach.get("elevation") and config.JMA_STATIONS.get(location, {}).get("elevation"):
        caveats.append(f"気象は{location}観測点（標高{config.JMA_STATIONS[location]['elevation']}m）を"
                       f"湖面（{reach['elevation']}m）へ気温減率で補正しています（気温減率であり水温補正の妥当性は未検証）")
    caveats.append("結氷・水位・実際の水色は、公式ライブカメラや現地でご確認ください")

    mood = ("ecstatic" if level == GO else "grumpy" if level == NO_GO
            else "sleepy" if (surf is not None and surf < p.t_opt_lo) else "neutral")
    headline = _lake_headline(level, surf, cr, reach)

    air = rows[-1]["mean_temp"] if rows else None
    v = Verdict(
        reach_id=reach_id, reach_label=reach["label"], waterbody="lake", level=level,
        headline=headline, mood=mood, tsi=tsi, model_quality=model_quality,
        observed_quality=None, effective_quality=model_quality, quality_source="表層TSI推定",
        cr_risk=cr, observed_catch=None, turbidity=None, water_status=None, water_trend=None,
        air_temp=air, max_temp=None, sunshine_h=(rows[-1]["sunshine_hours"] if rows else None),
        sunshine_estimated=True, water_temp_proxy=surf, confidence=round(conf, 2),
        staleness_days=None, source_confidence=src_conf, methods=reach["methods"],
        catch_release=reach["catch_release"], days_since_stock=stock_days,
        stock_date=stock.get("date"), reasons=reasons, caveats=caveats, series=series,
        as_of=as_of)
    v.outlook = _weekend_outlook(series[n_actual:])
    if v.outlook:
        rel = {r["date"]: r["reliability"] for r in conn.execute(
            "SELECT date, reliability FROM forecast_data WHERE location = ?", (location,))}
        for key in ("best", "next_good"):
            if v.outlook.get(key):
                v.outlook[key]["reliability"] = rel.get(v.outlook[key]["date"])
        for w in v.outlook.get("weekend", []):
            w["reliability"] = rel.get(w["date"])
    return v


def _lake_headline(level, surf, cr, reach):
    if level == NO_GO:
        if cr == "nogo" and reach["catch_release"]:
            return "表層水温が高く、リリースした魚が危険です。魚のために見送りを"
        return "見送りをおすすめします。営業期間・水温をご確認ください"
    if level == GO:
        return "表層が適水温（推定）。浅場・ショアから狙える好機です🎣（水温は推定値）"
    if surf is not None and surf > 20:
        return "表層は高水温。魚は深場です。狙うなら深度を落として、朝夕に"
    return "判断の難しい状況です。深度を刻んで探る前提でお楽しみください"


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def reach_report(
    conn: sqlite3.Connection,
    reach_id: str,
    *,
    params: Optional[TroutParams] = None,
    today: Optional[str] = None,
) -> Verdict:
    p = params or TroutParams()
    if config.is_lake(reach_id):
        return _lake_report(conn, reach_id, p, today)
    reach = config.REACHES[reach_id]
    river, location, station = reach["river"], reach["location"], reach["water_station"]

    actual = load_daily_inputs(conn, location, river, station)
    as_of = actual[-1].date if actual else None
    fc = [f for f in load_forecast_inputs(conn, location) if as_of is None or f.date > as_of]
    series = compute_series(actual + fc, p)
    n_actual = len(actual)
    latest = series[n_actual - 1] if n_actual else (series[-1] if series else None)

    sem = _latest(
        conn, "SELECT * FROM semantic_field_logs WHERE reach_id = ? ORDER BY date DESC LIMIT 1",
        (reach_id,),
    )
    water = _latest(
        conn, "SELECT * FROM river_physical_data WHERE river_name = ? AND station = ? "
        "ORDER BY date_time DESC LIMIT 1", (river, station),
    )
    wx = _latest(
        conn, "SELECT * FROM weather_data WHERE location = ? ORDER BY date DESC LIMIT 1",
        (location,),
    )

    v = build_verdict(
        reach_id, latest, series, sem, water, wx, p, today=today,
        open_state=reach_open(reach, today or dt.date.today().isoformat()),
        dam=dam_risk(conn, reach_id),
        stock=days_since_stock(conn, reach_id, as_of),
    )
    v.as_of = as_of
    actual_states = series[:n_actual]
    scour_dates = [s.date for s in actual_states if s.is_scour]
    if scour_dates and as_of:
        v.last_scour_date = max(scour_dates)
        v.days_since_scour = _staleness_days(v.last_scour_date, as_of)
    v.outlook = _weekend_outlook(series[n_actual:])
    if v.outlook:
        rel = {r["date"]: r["reliability"] for r in conn.execute(
            "SELECT date, reliability FROM forecast_data WHERE location = ?", (location,))}
        for key in ("best", "next_good"):
            item = v.outlook.get(key)
            if item:
                item["reliability"] = rel.get(item["date"])
        for w in v.outlook.get("weekend", []):
            w["reliability"] = rel.get(w["date"])
    v.stations = latest_station_statuses(conn, river)
    return v


if __name__ == "__main__":
    conn = db.connect()
    try:
        for rid in config.UI_REACHES:
            v = reach_report(conn, rid)
            print(v.level, "|", v.reach_label, "| TSI:", v.tsi, "| conf:", v.confidence,
                  "| src:", v.source_confidence)
            for r in v.reasons:
                print("   ", r)
    finally:
        conn.close()
