"""自己監査層 — システム自身の「言ったこと」を台帳に残し、「当たったか」を照合する。

2つの独立した仕組み（どちらも自分の釣果は使わない設計のまま）:

  1. verdict_snapshot 台帳: 日次更新のたびに、その区間の判定（レベル/TSI/根拠）を1行で記録。
     点時刻の一次記録なので後から改変なしに検証でき、「前回更新からの変化」の差分元にもなる。
  2. reconcile(): モデル（TSI）が各日に予想したコンディション状態 vs ブログの体感観測(cond)の
     突き合わせ。TSI の水温閾値は未較正（公開エビデンスからの推定）なので、他者の現場報告との
     一致率の蓄積だけが較正の根拠になる。

独立性: TSI 履歴系列は水温×日照から算出し、ブログの体感コンディション(cond)は入力に入れない。
scour(増水リセット)は水位ステージ由来でブログ観測とは別源。したがって cond との一致/不一致は
基本的に独立な検証になる。UI は常にサンプル数と注意書きを併記する（断定しない）。
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from typing import Any, Dict, List, Optional

from . import config, decision, guide
from .engine.trout_index import TroutParams, compute_series, obs_to_quality

# 活性/釣れ具合の距離に使う並び。高水温危険/増水リセットは活性ゼロ側。
QUALITY_RANK: Dict[str, int] = {
    "高水温危険": 0, "増水リセット": 0, "高水温減退": 1, "低活性": 1,
    "やや低調": 2, "好適": 3, "絶好": 4,
}
GRADE_WORD = {"exact": "○ 一致", "near": "△ 近い", "miss": "× 外れ"}
_LEVEL_JP = {"GO": "行くべし", "CAUTION": "様子見", "NO_GO": "見送り"}
_TURB_WORD = {0: "クリア", 1: "笹濁り", 2: "泥濁り", None: "情報なし"}


def _norm_date(s: Optional[str]) -> Optional[str]:
    """LLM 由来のスラッシュ日付('2026/07/10')等を YYYY-MM-DD に正規化。不正は None。"""
    if not isinstance(s, str) or len(s) < 10:
        return None
    cand = s[:10].replace("/", "-")
    try:
        dt.date.fromisoformat(cand)
    except ValueError:
        return None
    return cand


# --------------------------------------------------------------------------- #
# 1. 判定スナップショット台帳（前回比・事後検証の一次記録）
# --------------------------------------------------------------------------- #
def snapshot_from_verdict(v: Any, run_date: str) -> Dict[str, Any]:
    """Verdict → 台帳1行（純関数）。区間(reach_id)単位。"""
    next_good = (v.outlook or {}).get("next_good") if v.outlook else None
    return {
        "run_date": run_date,
        "reach_id": v.reach_id,
        "as_of": v.as_of,
        "level": v.level,
        "tsi": v.tsi,
        "model_quality": v.model_quality,
        "observed_quality": v.observed_quality,
        "effective_quality": v.effective_quality,
        "quality_source": v.quality_source,
        "cr_risk": v.cr_risk,
        "turbidity": v.turbidity,
        "water_status": v.water_status,
        "water_temp_proxy": v.water_temp_proxy,
        "confidence": v.confidence,
        "source_confidence": v.source_confidence,
        "days_since_stock": v.days_since_stock,
        "next_good_date": next_good.get("date") if next_good else None,
        "observed_post_date": v.observed_post_date,
    }


def save_snapshot(conn: sqlite3.Connection, snap: Dict[str, Any]) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO verdict_snapshot
           (run_date, reach_id, as_of, level, tsi, model_quality, observed_quality,
            effective_quality, quality_source, cr_risk, turbidity, water_status,
            water_temp_proxy, confidence, source_confidence, days_since_stock,
            next_good_date, observed_post_date)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (snap["run_date"], snap["reach_id"], snap["as_of"], snap["level"], snap["tsi"],
         snap["model_quality"], snap["observed_quality"], snap["effective_quality"],
         snap["quality_source"], snap["cr_risk"], snap["turbidity"], snap["water_status"],
         snap["water_temp_proxy"], snap["confidence"], snap["source_confidence"],
         snap["days_since_stock"], snap["next_good_date"], snap["observed_post_date"]),
    )


def latest_snapshot_before(conn: sqlite3.Connection, reach_id: str,
                           run_date: str) -> Optional[Dict[str, Any]]:
    """`run_date` より厳密に前の最新スナップショット（同日再実行は前回に数えない）。"""
    row = conn.execute(
        "SELECT * FROM verdict_snapshot WHERE reach_id = ? AND run_date < ? "
        "ORDER BY run_date DESC LIMIT 1",
        (reach_id, run_date),
    ).fetchone()
    return dict(row) if row else None


def compute_delta(prev: Optional[Dict[str, Any]],
                  cur: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """前回スナップショットとの人間可読な差分（純関数）。前回が無ければ None。"""
    if prev is None:
        return None
    changes: List[str] = []
    if prev["level"] != cur["level"]:
        changes.append(f"判定が {_LEVEL_JP.get(prev['level'], prev['level'])} → "
                       f"{_LEVEL_JP.get(cur['level'], cur['level'])} に変わりました")
    pt, ct = prev.get("tsi"), cur.get("tsi")
    if pt is not None and ct is not None and abs(ct - pt) >= 5:
        arrow = "↑" if ct > pt else "↓"
        changes.append(f"適性メーター {pt:.0f} → {ct:.0f}（{arrow}{abs(ct - pt):.0f}）")
    if prev.get("effective_quality") != cur.get("effective_quality"):
        changes.append(f"コンディション {prev.get('effective_quality') or '不明'} → "
                       f"{cur.get('effective_quality') or '不明'}")
    if prev.get("turbidity") != cur.get("turbidity"):
        changes.append(f"濁り {_TURB_WORD.get(prev.get('turbidity'))} → "
                       f"{_TURB_WORD.get(cur.get('turbidity'))}")
    if prev.get("water_status") != cur.get("water_status"):
        changes.append(f"水位 {prev.get('water_status') or '不明'} → "
                       f"{cur.get('water_status') or '不明'}")
    if prev.get("next_good_date") != cur.get("next_good_date"):
        old = guide.jp_date(prev.get("next_good_date")) if prev.get("next_good_date") else "なし"
        new = guide.jp_date(cur.get("next_good_date")) if cur.get("next_good_date") else "なし"
        changes.append(f"『次に行くなら』 {old} → {new}")
    p_obs = _norm_date(prev.get("observed_post_date"))
    c_obs = _norm_date(cur.get("observed_post_date"))
    if c_obs and c_obs != p_obs and (p_obs is None or c_obs > p_obs):
        changes.append(f"新しい現場報告（ブログ {guide.jp_date(c_obs)} 投稿）を取り込みました")
    return {"prev_date": prev["run_date"], "changes": changes}


# --------------------------------------------------------------------------- #
# 2. 予実照合（モデルのコンディション予想 vs ブログの体感観測）
# --------------------------------------------------------------------------- #
def _grade(predicted: Optional[str], observed: Optional[str]) -> str:
    if predicted is None or observed is None:
        return "miss"
    if predicted == observed:
        return "exact"
    pr, ob = QUALITY_RANK.get(predicted), QUALITY_RANK.get(observed)
    if pr is None or ob is None:
        return "miss"
    return "near" if abs(pr - ob) == 1 else "miss"


def reconcile(conn: sqlite3.Connection, reach_id: str,
              params: Optional[TroutParams] = None,
              max_rows: int = 8) -> Dict[str, Any]:
    """モデル系列（実績のみ）と、日付が特定できる体感コンディション観測を突き合わせる。

    観測日は source_post_date（無ければ取得日）を正規化。同一観測日に複数あれば後の取り込み
    （取り込み日、同日タイは source_name 順）を採用。モデル系列に無い日の観測は数えない。
    """
    p = params or TroutParams()
    reach = config.REACHES[reach_id]
    if config.is_lake(reach_id):
        # 湖は現場ブログの体感照合ソースを持たない(semantic_source=None)。河川入力(river/water_station)
        # も持たないため、照合系列は組まず「照合対象なし」を正直に返す。
        return {"n": 0, "exact": 0, "near": 0, "miss": 0, "rows": [],
                "note": "湖は現場報告（体感）の照合ソースを設定していません"
                        "（表層水温＋季節からの推定判定のため、予実照合は行いません）。"}
    series = compute_series(
        decision.load_daily_inputs(conn, reach["location"], reach["river"],
                                   reach["water_station"]), p)
    by_date = {s.date: s for s in series}

    obs_by_date: Dict[str, sqlite3.Row] = {}
    for r in conn.execute(
        "SELECT date, source_post_date, cond_score, catch_report FROM semantic_field_logs "
        "WHERE reach_id = ? AND cond_score IS NOT NULL ORDER BY date, source_name",
        (reach_id,),
    ):
        d = _norm_date(r["source_post_date"]) or _norm_date(r["date"])
        if d:
            obs_by_date[d] = r

    rows: List[Dict[str, Any]] = []
    counts = {"exact": 0, "near": 0, "miss": 0}
    for d in sorted(obs_by_date):
        st = by_date.get(d)
        if st is None:
            continue
        observed = obs_to_quality(obs_by_date[d]["cond_score"])
        grade = _grade(st.quality_state, observed)
        counts[grade] += 1
        rows.append({"date": d, "predicted": st.quality_state, "observed": observed,
                     "grade": grade, "catch": obs_by_date[d]["catch_report"]})
    n = len(rows)
    if n == 0:
        note = "照合できる現場報告（体感の記載があるブログ）はまだありません。蓄積待ちです。"
    elif n < 10:
        note = f"サンプルは{n}件 — 傾向を語るにはまだ少ない数です（較正は10件以上たまってから）。"
    else:
        hit = counts["exact"] + counts["near"]
        note = f"サンプル{n}件・一致+近い {hit}/{n}。一致率は目安であり、保証ではありません。"
    return {"n": n, **counts, "rows": rows[-max_rows:], "note": note}
