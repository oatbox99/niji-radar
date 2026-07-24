"""群馬・河川ニジマスレーダー — 公開データのみで区間ごとの釣行判断（Streamlit）。

自分の釣果は使わない設計。判断は src/decision.py（4段ゲート）に集約し、ここは表示のみ。
判定単位は「区間(reach)」。水温がニジマス活性の第一決定因子で、C&R区間では「釣れるか」以前に
「離した魚が生きるか」で判定を止める。水温はライブ源が無く気温からの未較正プロキシ（現場報告
があればそちらを優先）。全指標に「意味」と「出所」を併記し、鱒マスコットが判定に反応する。

standalone（scripts/export_html.py は import しない）。ローカルUI確認用。
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path
from typing import Any, List

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.river_map import river_map_svg  # noqa: E402
from app.trout_art import trout_svg  # noqa: E402
from src import calibration, config, db, decision, guide  # noqa: E402
from src.engine.trout_index import TroutParams  # noqa: E402

P = TroutParams()

st.set_page_config(page_title="ニジマスレーダー（群馬）", page_icon="🐟", layout="wide")

# 注: `.fish-spark` は app/trout_art.py が出力する装飾クラス名に合わせている。
CSS = """
<style>
@keyframes fishbob { 0%{transform:translateY(0) rotate(-1.5deg)} 50%{transform:translateY(-12px) rotate(1.5deg)} 100%{transform:translateY(0) rotate(-1.5deg)} }
@keyframes fishspark { 0%,100%{opacity:.15} 50%{opacity:1} }
.fish-bob { animation: fishbob 3s ease-in-out infinite; }
.fish-spark { animation: fishspark 1.6s ease-in-out infinite; }
.hero { border-radius:20px; padding:22px 26px; color:#fff; box-shadow:0 8px 26px rgba(0,0,0,.18);
        display:flex; align-items:center; gap:20px; }
.water-clear{background:linear-gradient(135deg,#4fc3f7,#0277bd);} .water-sasa{background:linear-gradient(135deg,#4db6ac,#00695c);}
.water-doro{background:linear-gradient(135deg,#a1887f,#5d4037);} .water-unknown{background:linear-gradient(135deg,#90a4ae,#455a64);}
.hero-fish{width:210px;height:126px;flex:0 0 210px;} .hero-body{flex:1;}
.badge{display:inline-block;padding:6px 16px;border-radius:999px;font-weight:800;font-size:1.05rem;letter-spacing:.04em;}
.badge-GO{background:#43a047;color:#fff;} .badge-CAUTION{background:#fb8c00;color:#fff;} .badge-NO_GO{background:#e53935;color:#fff;}
.headline{font-size:1.5rem;font-weight:800;margin:10px 0 4px;text-shadow:0 1px 4px rgba(0,0,0,.25);}
.subline{opacity:.93;font-size:.95rem;}
.meter{position:relative;height:26px;border-radius:13px;background:rgba(255,255,255,.28);overflow:hidden;margin-top:12px;}
.meter-fill{height:100%;border-radius:13px;background:linear-gradient(90deg,#81d4fa,#4db6ac,#aed581);}
.meter-go{position:absolute;top:-4px;width:0;height:34px;border-left:3px dashed #fff;}
.meter-lab{font-size:.82rem;opacity:.92;margin-top:4px;}
.chip{font-size:.7rem;padding:2px 8px;border-radius:8px;background:rgba(0,0,0,.22);margin-left:6px;}
.hero .one{font-size:1.05rem;font-weight:700;margin:8px 0 2px;}
/* metric + zone cards */
.card{border:1px solid rgba(128,128,128,.25);border-radius:14px;padding:12px 14px;height:100%;}
.card .k{font-size:.8rem;opacity:.72;} .card .v{font-size:1.32rem;font-weight:800;}
.card .mean{font-size:.72rem;opacity:.7;margin-top:3px;}
.card a{color:inherit;text-decoration:underline;text-underline-offset:2px;}
.src{font-size:.62rem;padding:1px 6px;border-radius:7px;background:rgba(128,128,128,.2);margin-left:5px;}
.zone{border:1px solid rgba(128,128,128,.25);border-radius:14px;padding:12px 14px;text-align:center;}
/* 基準日バナー */
.datebar{display:flex;flex-wrap:wrap;align-items:baseline;gap:8px 12px;padding:12px 16px;margin:2px 0 4px;
  border:1px solid rgba(128,128,128,.3);border-left:5px solid #43a047;border-radius:12px;background:rgba(128,128,128,.05);}
.datebar .dcal{font-size:1.5rem;font-weight:800;} .datebar .dnote{font-size:.95rem;opacity:.7;}
.fresh{font-size:.72rem;font-weight:700;padding:2px 9px;border-radius:999px;}
.fresh-ok{background:rgba(46,125,50,.16);color:#2e7d32;border:1px solid rgba(46,125,50,.4);}
.fresh-warn{background:rgba(239,108,0,.16);color:#e65100;border:1px solid rgba(239,108,0,.45);}
@media (prefers-color-scheme:dark){.fresh-ok{color:#a5d6a7;} .fresh-warn{color:#ffcc80;}}
/* GO条件チェックリスト */
.ckhead{font-weight:800;margin:2px 0 4px;}
.checklist{list-style:none;padding-left:0;margin:0;}
.checklist li{padding:3px 0;font-size:.93rem;border-top:1px solid rgba(128,128,128,.2);}
.checklist li:first-child{border-top:none;}
.ck-no{font-weight:700;} .ckd{opacity:.6;font-size:.82rem;margin-left:4px;}
/* 今日の狙い方 */
.tips{border:1px solid rgba(67,160,71,.35);border-radius:14px;padding:12px 16px;background:rgba(124,179,66,.10);}
.tips ul{margin:6px 0 0;padding-left:20px;} .tips li{margin:4px 0;font-size:.95rem;line-height:1.5;}
/* 水温帯ガイド */
.stages{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;}
.stage{border:1px solid rgba(128,128,128,.25);border-radius:12px;padding:0 0 9px;overflow:hidden;background:rgba(128,128,128,.04);}
.stage.on{border:2px solid #43a047;box-shadow:0 4px 14px rgba(67,160,71,.28);}
.sbar{height:7px;} .shd{padding:8px 9px 4px;font-weight:800;font-size:.9rem;}
.snow{display:inline-block;font-size:.62rem;background:#2e7d32;color:#fff;border-radius:7px;padding:0 6px;margin-left:4px;vertical-align:middle;}
.srow{font-size:.72rem;line-height:1.4;padding:2px 9px;opacity:.9;} .srow b{opacity:.6;font-weight:700;}
.show{font-size:.76rem;font-weight:700;padding:4px 9px 0;}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)

WATER_CLASS = {0: "water-clear", 1: "water-sasa", 2: "water-doro", None: "water-unknown"}
WATER_WORD = {0: "クリア", 1: "笹濁り", 2: "泥濁り", None: "情報なし"}
LEVEL_JP = {"GO": "行くべし", "CAUTION": "様子見", "NO_GO": "見送り"}
SRC_BADGE = {"verified": "✅ 公式確認済み", "参考": "⚠ 参考データ", "未確認": "⚠ 未確認"}
CR_WORD = {
    "safe": ("安全", "#2e7d32", "リリースに安全な水温帯です（16℃未満）"),
    "caution": ("配慮", "#f9a825", "水温が上がり始めています。丁寧なリリースを心がけてください"),
    "strong": ("要配慮", "#fb8c00", "水温がやや高めです。水から出す時間を最小にして素早くリリースを"),
    "nogo": ("見送り推奨", "#e53935", "高水温でリリースした魚が死ぬリスクが高い日です。魚のために見送りを"),
    "unknown": ("不明", "#9e9e9e", "水温が不明のため、C&Rリスクを判定できません"),
}


def _fmt(v: Any, unit: str = "", nd: int = 1) -> str:
    if v is None:
        return "—"
    return f"{v:.{nd}f}{unit}" if isinstance(v, float) else f"{v}{unit}"


# --------------------------------------------------------------------------- #
def _select_reach() -> str:
    st.sidebar.title("🐟 ニジマスレーダー")
    st.sidebar.caption("群馬・河川ニジマス／区間別の釣行判断（公開データのみ・非公式）")
    ids: List[str] = list(config.UI_REACHES)   # verified を先頭に並べた表示順
    labels: List[str] = []
    for rid in ids:
        r = config.REACHES[rid]
        sc = r.get("source_confidence", "参考")
        suffix = "（公式確認）" if sc == "verified" else f"（{sc}）"
        labels.append(r["label"] + suffix)
    idx = st.sidebar.selectbox("区間を選ぶ", range(len(ids)),
                               format_func=lambda i: labels[i], index=0)

    pending = st.session_state.pop("seed_msg", None)
    if pending:
        (st.sidebar.success if pending[0] == "ok" else st.sidebar.error)(pending[1])
    st.sidebar.markdown("**データ操作（ローカル確認用）**")
    if st.sidebar.button("🌱 デモデータ投入", width="stretch"):
        try:
            from scripts import seed_demo
            seed_demo.main()
            st.session_state["seed_msg"] = ("ok", "デモデータを投入しました")
        except Exception as exc:  # noqa: BLE001
            st.session_state["seed_msg"] = ("err", f"投入失敗: {exc}")
        st.rerun()
    st.sidebar.caption("ライブ収集は `python -m scripts.run_ingest` を別途実行してください。")
    return ids[idx]


def _hero(v: decision.Verdict) -> None:
    tsi_val = v.tsi if v.tsi is not None else 0.0
    tsi_txt = f"{v.tsi:.0f}" if v.tsi is not None else "—"
    label = LEVEL_JP[v.level]
    src = v.source_confidence
    src_badge = SRC_BADGE.get(src, f"⚠ {src}")
    method = guide.method_label(v.methods, v.catch_release)
    quality = v.effective_quality or "情報が薄い"
    st.markdown(
        f"""
<div class="hero {WATER_CLASS.get(v.turbidity, 'water-unknown')}">
  <div class="hero-fish fish-bob">{trout_svg(v.mood)}</div>
  <div class="hero-body">
    <span class="badge badge-{v.level}">{label}</span>
    <span class="chip">信頼度 {int(v.confidence * 100)}%</span>
    <span class="chip">{src_badge}</span>
    <div class="one">{guide.VERDICT_ONELINER[v.level]}</div>
    <div class="headline">{v.headline}</div>
    <div class="subline">適性メーター {tsi_txt}/100 ・ {quality}
      （{v.quality_source}）・ 水色: {WATER_WORD.get(v.turbidity)}</div>
    <div class="meter"><div class="meter-fill" style="width:{tsi_val:.0f}%"></div>
      <div class="meter-go" style="left:{P.go_min:.0f}%"></div></div>
    <div class="meter-lab">{method}　／　破線は「絶好」ライン（未較正・公開データからの推定）</div>
  </div>
</div>""",
        unsafe_allow_html=True,
    )


def _datebar(v: decision.Verdict) -> None:
    fr = guide.freshness(v.as_of)
    st.markdown(
        f'<div class="datebar"><span class="dcal">📅 {guide.jp_date(v.as_of, with_year=True)}</span>'
        f'<span class="dnote">時点の状況 ／ 前後は下の推移グラフ（実線=実績・破線=予報）</span>'
        f'<span class="fresh fresh-{fr["level"]}">{fr["label"]}</span></div>',
        unsafe_allow_html=True)
    if fr["level"] == "warn":
        st.warning(f"⚠️ 直近データが古めです（{fr['label']}）。現地は変わっている可能性があります。")


def _checklist(v: decision.Verdict) -> None:
    rows = decision.go_checklist(v)
    n_fail = sum(1 for r in rows if not r["ok"])
    if v.level == "GO":
        head = "✅ 条件をすべて満たしています（行くべし）"
    elif n_fail == 0:
        # 全ゲート充足だが GO でない = 参考データ源の格下げ/午後高水温等の追加注意。
        head = ("GO条件は満たしていますが、参考データ源のため確信GOを保留しています"
                if v.source_confidence != "verified"
                else "GO条件は満たしていますが、追加の注意で様子見にしています")
    else:
        head = f"『行くべし』に届かない理由（未充足 {n_fail}件）"
    lis = "".join(
        f'<li class="{"ck-ok" if r["ok"] else "ck-no"}">'
        f'{"✅" if r["ok"] else "❔" if r.get("unknown") else "❌"} {r["label"]}'
        f'<span class="ckd">（{r["detail"]}）</span></li>' for r in rows)
    st.markdown("#### 🧮 なぜこの判定か（GO条件チェック）")
    st.markdown(f'<div class="card"><div class="ckhead">{head}</div>'
                f'<ul class="checklist">{lis}</ul></div>', unsafe_allow_html=True)


def _water_cr(v: decision.Verdict) -> None:
    st.markdown("#### 💧 水温 と キャッチ&リリース（魚の生存）")
    if v.observed_water_temp is not None:
        cf = (f"・抽出確信度{int(v.observed_confidence * 100)}%"
              if isinstance(v.observed_confidence, (int, float)) else "")
        wt_val, wt_src = _fmt(v.observed_water_temp, "℃"), f"現場報告（ブログ記載・優先{cf}）"
    else:
        wt_val, wt_src = _fmt(v.water_temp_proxy, "℃"), "気温からの換算（未較正プロキシ）"
    cr_word, cr_col, cr_desc = CR_WORD.get(v.cr_risk or "unknown", CR_WORD["unknown"])
    cols = st.columns(3)
    cols[0].markdown(
        f'<div class="card"><div class="k">🌡️ 推定水温</div>'
        f'<div class="v">{wt_val}</div><div class="mean">{wt_src}</div></div>',
        unsafe_allow_html=True)
    if v.catch_release:
        cr_v = f'<span style="color:{cr_col}">{cr_word}</span>'
        cr_mean = "リリース死亡率は水温連動（16℃から上昇・20℃で実質キャッチ&キル）"
    else:
        cr_v = "一般区間"
        cr_mean = "キープ可（要遊漁券）。水温は活性の目安として参照"
    cols[1].markdown(
        f'<div class="card"><div class="k">🐟 C&Rリスク（水温連動）</div>'
        f'<div class="v">{cr_v}</div><div class="mean">{cr_mean}</div></div>',
        unsafe_allow_html=True)
    if v.waterbody == "lake":
        shore = config.REACHES[v.reach_id].get("shore_only", False)
        depth_msg = ("本日は見送り推奨。狙い方は次の好機日に。" if v.level == "NO_GO"
                     else guide.lake_depth_note(v.water_temp_proxy, shore_only=shore))
        cols[2].markdown(
            f'<div class="card"><div class="k">🪝 狙う深度（推定）</div>'
            f'<div class="v" style="font-size:.98rem;line-height:1.4">{depth_msg}</div>'
            f'<div class="mean">躍層・DO・魚の層は実測源なし＝季節推定（中層を刻んで探る前提）</div></div>',
            unsafe_allow_html=True)
    else:
        cols[2].markdown(
            f'<div class="card"><div class="k">🍃 濁り（水色）</div>'
            f'<div class="v">{WATER_WORD.get(v.turbidity)}</div>'
            f'<div class="mean">笹濁り=最好窓／クリア=警戒／泥濁り=竿NG</div></div>',
            unsafe_allow_html=True)
    note = guide.cr_note(v.cr_risk, v.catch_release)
    if note:
        (st.error if v.cr_risk == "nogo" else st.warning)("🐟 " + note)
    elif v.catch_release:
        st.caption("🐟 " + cr_desc + "（この区間は全キャッチ&リリースです）。")
    st.caption("※ 水温は現場報告があればそれを優先し、無ければ気温から換算した未較正プロキシです"
               "（『実測』ではありません）。")


def _tips(v: decision.Verdict) -> None:
    wt = v.observed_water_temp if v.observed_water_temp is not None else v.water_temp_proxy
    tips = guide.fishing_tips(v.level, v.effective_quality, v.turbidity, v.water_trend,
                              v.methods, v.days_since_stock, water_temp=wt)
    lis = "".join(f"<li>{t}</li>" for t in tips)
    st.markdown("#### 🎣 今日の狙い方（条件から導いた一般的な定石）")
    st.markdown(f'<div class="tips"><ul>{lis}</ul></div>', unsafe_allow_html=True)
    st.caption("※ 一般的な定石です。最終的な立ち位置・安全判断は現地とあなたの目で。")


def _stages(v: decision.Verdict) -> None:
    st.markdown("#### 🌡️🐟 水温とニジマスの関係 — なぜこれで釣果が決まるのか")
    st.markdown(f'<div class="card" style="color:inherit">{guide.WHY_TROUT}</div>',
                unsafe_allow_html=True)
    cur = v.effective_quality
    cards = []
    for s in guide.TROUT_STAGES:
        on = " on" if s["state"] == cur else ""
        now = '<span class="snow">今ここ</span>' if s["state"] == cur else ""
        cards.append(
            f'<div class="stage{on}"><div class="sbar" style="background:{s["color"]}"></div>'
            f'<div class="shd">{s["emoji"]} {s["state"]}{now}</div>'
            f'<div class="srow"><b>水温:</b> {s["temp"]}</div>'
            f'<div class="srow"><b>魚:</b> {s["fish"]}</div>'
            f'<div class="show">{s["how"]}</div></div>')
    st.markdown(f'<div class="stages">{"".join(cards)}</div>', unsafe_allow_html=True)
    if cur is None:
        st.caption("いまの水温帯は情報が薄く未確定です。現地で水温・水色をご確認ください。")
    else:
        st.caption(f"いまは緑枠の『{cur}』（{guide.STATE_SHORT.get(cur, '')}）。"
                   "上の『魚』『釣れ方』が今の期待値です。")


def _season(v: decision.Verdict) -> None:
    note = (guide.lake_season_note() if v.waterbody == "lake" else guide.season_note())
    if note is None:
        return
    (st.warning if note["level"] == "warn" else st.info)("🗓️ " + note["msg"])


def _outlook(v: decision.Verdict) -> None:
    if not v.series:
        return
    st.markdown("#### 📈 適性メーター(TSI)の推移と見通し — 実線=実績 / 破線=気象庁予報投影")
    import pandas as pd
    df = pd.DataFrame([{"date": s.date, "tsi": s.tsi, "scour": s.is_scour} for s in v.series])
    df["date"] = pd.to_datetime(df["date"])
    try:
        import altair as alt
        as_of = pd.to_datetime(v.as_of) if v.as_of else df["date"].max()
        yscale = alt.Scale(domain=[0, 100])
        area = alt.Chart(df[df["date"] <= as_of]).mark_area(
            line={"color": "#00897b"}, color="#80cbc4", opacity=0.35).encode(
            x=alt.X("date:T", title=None),
            y=alt.Y("tsi:Q", scale=yscale, title="適性メーター(0-100)"))
        layers = [area]
        fc_df = df[df["date"] >= as_of]                 # include as_of so the lines join
        if len(fc_df) > 1:
            layers.append(alt.Chart(fc_df).mark_line(
                strokeDash=[5, 4], color="#ef6c00", point=True).encode(
                x="date:T", y=alt.Y("tsi:Q", scale=yscale)))
        layers.append(alt.Chart(pd.DataFrame({"y": [P.go_min]})).mark_rule(
            color="#ef6c00", strokeDash=[2, 3]).encode(y="y:Q"))
        layers.append(alt.Chart(pd.DataFrame({"x": [as_of]})).mark_rule(
            color="#607d8b", strokeDash=[4, 4]).encode(x="x:T"))
        scours = df[df["scour"]]
        if not scours.empty:
            layers.append(alt.Chart(scours).mark_rule(color="#8d6e63", size=2).encode(x="date:T"))
        st.altair_chart(alt.layer(*layers).properties(height=250), width="stretch")
        st.caption("灰破線＝基準日(今日) ／ 橙破線＝予報投影 ／ 茶縦線＝増水リセット ／ 橙点線＝『絶好』閾値。")
    except Exception:  # noqa: BLE001
        st.line_chart(df.set_index("date")["tsi"])
    _outlook_summary(v)


def _outlook_summary(v: decision.Verdict) -> None:
    o = v.outlook
    if not o:
        return
    c = st.columns(2)
    c[0].metric("1週間の傾向", o["trend"])
    b = o.get("best")
    if b:
        rel = f"・信頼度{b['reliability']}" if b.get("reliability") else ""
        c[1].metric("ピーク予想", f"{b['tsi']:.0f} / {b['quality']}", help=f"{b['date']} 頃{rel}")
    if o.get("weekend"):
        parts = []
        for w in o["weekend"]:
            rel = f"・信頼度{w['reliability']}" if w.get("reliability") else ""
            parts.append(f"**{w['date'][5:]}({w['wd']})** 適性{w['tsi']:.0f}・{w['quality']}{rel}")
        st.markdown("🗓️ **次の週末**： " + "　".join(parts))
    ng = o.get("next_good")
    if ng and ng.get("closed"):
        # 合法/営業ゲートは釣果より上位 — 期間外の日を「次に行くなら」と推奨しない。
        st.info("🎯 予報上は水温が好適になる日がありますが、営業/解禁期間外（または定休日）"
                "の可能性があるため候補には出しません。営業期間の確認が先です。")
    elif ng:
        rel = f"・予報信頼度{ng['reliability']}" if ng.get("reliability") else ""
        # 参考区間は確信GOを出さない方針。前向きの「次に行くなら」も断定を弱めて目安と明示。
        tref = "（参考区間・予報上の目安）" if v.source_confidence != "verified" else ""
        msg = (f"🎯 **次に行くなら {ng['date'][5:]} 頃** — 適性{ng['tsi']:.0f}・{ng['quality']}{rel}"
               f"（予報上、水温が好適で増水も無い最初の日）{tref}")
        (st.info if v.source_confidence != "verified" else st.success)(msg)
    else:
        st.info("🎯 今後の予報期間には『行くべし』級の日が見当たりません（水温が外れる/増水懸念）。")
    if o.get("scours"):
        st.warning("☔ 予報上の増水・リセット懸念日: " + ", ".join(s[5:] for s in o["scours"]))
    st.caption("⚠️ 天気コード→日照は粗い換算、気温は前橋予報＋標高オフセット。予報は不確実です。")


def _river(v: decision.Verdict) -> None:
    reach = config.REACHES[v.reach_id]
    river = reach["river"]
    cfg = config.RIVER_WATER_LEVEL.get(river, {})
    target = reach["water_station"]
    st.markdown("#### 🗺️ 川マップ（各観測点の増水状況・🎣＝この区間の観測点）")
    points = []
    for name in cfg.get("stations", []):
        stt = v.stations.get(name, {})
        sev = config.LEVEL_SEVERITY.get(stt.get("water_level_status") or "", 0)
        is_target = name == target
        points.append({
            "name": name,
            "status": stt.get("water_level_status"),
            "trend": stt.get("water_trend"),
            "sev": sev,
            "mark": is_target,
            "zone_label": "対象区間" if is_target else "",
        })
    svg = river_map_svg(points, river=river, upstream=cfg.get("map_upstream", "上流"),
                        downstream=cfg.get("map_downstream", "下流"))
    st.markdown(f'<div class="card" style="color:inherit">{svg}</div>', unsafe_allow_html=True)
    st.caption(f"⚠️ 気象・水温は AMeDAS 1地点を区間共通で使用。観測点ごとに違うのは水位ステージのみ"
               f"（この区間の判定は {target} を基準）。")


def _dam(v: decision.Verdict) -> None:
    dr = v.dam_risk
    st.markdown("#### 🌊 上流ダム放流（濁りの前兆）")
    if dr is None:
        st.info("この区間は自然流量です。上流ダムの放流監視は不要です（下久保等は下流のため無関係）。")
        return
    if dr.get("risk"):
        w = dr["worst"]
        pct = "急増" if w["slope_pct"] >= 900 else f"+{int(w['slope_pct'] * 100)}%"
        st.error(f"⚠️ {w['dam']}ダムが放流{pct}（直近 {w['discharge']:.0f} m³/s）→ 濁り放流リスク。"
                 f"{w['eta_text']}。")
    else:
        id_missing = dr.get("id_missing") or []
        seen = dr.get("dams_seen", 0)
        slope_known = dr.get("slope_known", 0)
        if id_missing:
            st.warning("監視対象ダム（" + "・".join(id_missing) + "）は放流量取得IDが未確認です。"
                       "濁り放流の判定に空白があります。現地・水位でご確認ください（正直に未確認と表示）。")
        if seen == 0 and not id_missing:
            st.warning("上流ダムの放流データが未取得です（平穏とは限りません）。")
        elif seen > 0:
            missing_trend = seen - slope_known
            if missing_trend:
                msg = f"上流ダムのうち{missing_trend}基は放流傾向が欠測"
                if slope_known:
                    msg += f"（判明{slope_known}基は急放流なし）"
                st.warning(msg + "。現地・水位でご確認ください。")
            else:
                st.success(f"上流{seen}ダムに急放流はありません（濁り放流の前兆なし）。")
    st.caption("上流ダムの放流増を濁りの前兆として監視します。到達時間は距離と流速の仮定から出した"
               "粗い目安です（実測ゲージなし・濁り本体は水位波よりさらに遅れて到達）。")


def _lake_context(v: decision.Verdict) -> None:
    """湖専用: 成層の因果（なぜ深度で釣果が決まるか）。河川の川マップ/ダムに相当する枠。"""
    st.markdown("#### 🏞 なぜ深度で釣果が決まるのか（湖の成層）")
    st.markdown(f'<div class="card" style="color:inherit">{guide.WHY_LAKE}</div>',
                unsafe_allow_html=True)
    if v.level == "NO_GO":
        st.info("🪝 本日は見送り推奨（営業期間外／表層高温）。深度戦略は次の好機日にご覧ください。")
    else:
        shore = config.REACHES[v.reach_id].get("shore_only", False)
        st.info("🪝 " + guide.lake_depth_note(v.water_temp_proxy, shore_only=shore))
    st.caption("⚠️ 湖は増水・濁り・上流ダム放流の判定を行いません（止水のため）。"
               "表層水温は気温＋標高補正の推定で未較正、躍層・溶存酸素・魚の居る深度は"
               "公開の実測源が無く季節推定です。結氷・水位・水色は現地/公式でご確認ください。")


def _indicator_guide(v: decision.Verdict) -> None:
    with st.expander("📖 指標の読み方（高い/低いとどうなる）と判定の根拠・弱点", expanded=False):
        rows = "\n".join(
            f"| **{g['key']}** | {g['what']} | {g['high_low']} | {g['source']} |"
            for g in guide.INDICATOR_GUIDE)
        st.markdown("| 指標 | 何の値？ | 高い/低いとどうなる | 何を元に |\n"
                    "|---|---|---|---|\n" + rows)
        st.markdown("**この判定の理由**")
        for r in v.reasons:
            st.markdown(f"- {r}")
        st.markdown("**割引いた理由（正直な弱点）**")
        for c in v.caveats:
            st.markdown(f"- ⚠️ {c}")
        st.info("自分の釣果は使わない設計です。精度の天井は公開データの質で決まります。"
                "泥濁り・増水・高水温は問答無用でNGです。最終判断はこの数値＋あなたの目で。")


def _sources(v: decision.Verdict) -> None:
    reach = config.REACHES[v.reach_id]
    location = reach["location"]
    code = config.JMA_STATIONS[location]["code"]
    jma = config.JMA_AMEDAS_PAGE.format(code=code)
    river = reach.get("river")                       # 湖は river 無し
    water = config.RIVER_WATER_LEVEL.get(river, {}).get("yahoo_url") if river else None
    off = reach.get("official_url")
    info = reach.get("info_url")
    catch = reach.get("catch_ref_url")
    with st.expander("📚 情報源（クリックで開く）"):
        items = [
            ("公式サイト（漁協・釣場）", off),
            ("情報・釣況ページ", info if info != off else None),
            ("他の人の釣果まとめ（外部・自動解析はしない）", catch if catch not in (off, info) else None),
            ("気象・日照（気象庁AMeDAS）", jma),
            ("週間予報（気象庁）", config.JMA_FORECAST_PAGE),
            ("水位（Yahoo川の防災情報）", water),
        ]
        for label, url in items:
            st.markdown(f"- [{label}]({url}) ↗" if url else f"- {label}: （この区間では未使用/なし）")


def _calibration(conn, v: decision.Verdict) -> None:
    st.markdown("#### 🧾 前回比 と 予実照合（自己監査）")
    run_date = dt.date.today().isoformat()
    cur = calibration.snapshot_from_verdict(v, run_date)
    prev = calibration.latest_snapshot_before(conn, v.reach_id, run_date)
    delta = calibration.compute_delta(prev, cur)
    if delta is None:
        st.caption("前回スナップショットが無く、前回比はまだ表示できません（日次更新の蓄積待ち）。")
    elif not delta["changes"]:
        st.info(f"前回（{guide.jp_date(delta['prev_date'])}）から目立った変化はありません。")
    else:
        st.markdown(f"**前回（{guide.jp_date(delta['prev_date'])}）からの変化**")
        for ch in delta["changes"]:
            st.markdown(f"- {ch}")

    rec = calibration.reconcile(conn, v.reach_id)
    st.markdown(f"**予実照合（モデル予想 vs ブログ体感）** — {rec['note']}")
    if rec["n"]:
        st.caption(f"内訳: {calibration.GRADE_WORD['exact']} {rec['exact']}件／"
                   f"{calibration.GRADE_WORD['near']} {rec['near']}件／"
                   f"{calibration.GRADE_WORD['miss']} {rec['miss']}件")
        head = "| 日付 | モデル予想 | 現場(ブログ) | 判定 | 釣果 |\n|---|---|---|---|---|\n"
        body = "\n".join(
            f"| {guide.jp_date(r['date'])} | {r['predicted'] or '—'} | {r['observed'] or '—'} | "
            f"{calibration.GRADE_WORD.get(r['grade'], r['grade'])} | "
            f"{r['catch'] if r['catch'] is not None else '—'} |"
            for r in rec["rows"])
        st.markdown(head + body)
    st.caption("※ モデル系列は水温×日照のみから算出し、ブログ体感は入力に含めません（独立検証）。"
               "ただしサンプルが少ないうちは一致率は目安にすぎず、保証ではありません。")


def _empty_state(reach_id: str) -> None:
    reach = config.REACHES[reach_id]
    st.markdown(
        f'<div class="hero water-unknown"><div class="hero-fish fish-bob">'
        f'{trout_svg("sleepy")}</div><div class="hero-body">'
        f'<div class="headline">データがまだありません</div>'
        f'<div class="subline">『{reach["label"]}』の公開データが未取得です。'
        f'サイドバーの「🌱 デモデータ投入」を押すか、ターミナルで '
        f'<code>python -m scripts.seed_demo</code> を実行してください。</div>'
        f'</div></div>', unsafe_allow_html=True)


def main() -> None:
    db.init_db()               # 起動時にスキーマ作成（存在すれば no-op）
    conn = db.connect()
    try:
        reach_id = _select_reach()
        reach = config.REACHES[reach_id]
        st.title(f"🐟 {reach['label']}")
        st.caption("週末遠征の無駄足を減らす個人用フィルター（群馬・河川ニジマス）／ "
                   "公開データのみ・自分の釣果は使わない・区間単位で判定")
        st.caption("※ 非公式です。正確な解禁期間・区間・遊漁ルールは各漁協で必ずご確認ください。"
                   "水温はライブ計測源が無く、気温からの未較正プロキシです（現場報告があればそちらを優先）。")

        v = decision.reach_report(conn, reach_id)
        if v.as_of is None:                # DBが空
            _empty_state(reach_id)
            return

        is_lake = v.waterbody == "lake"
        _hero(v)
        _datebar(v)
        if is_lake:
            st.markdown("🟢 行くべし＝表層が適水温・浅場が効く ／ 🟠 様子見＝深度を刻む "
                        "／ 🔴 見送り＝営業期間外・表層高温 （C&R湖は魚の生存を優先）")
        else:
            st.markdown("🟢 行くべし＝好条件 ／ 🟠 様子見＝決め手なし ／ 🔴 見送り＝増水・濁り・高水温 "
                        "（C&R区間は魚の生存を優先）")
        _season(v)
        st.write("")
        _checklist(v)
        st.write("")
        _water_cr(v)
        st.write("")
        _tips(v)
        st.write("")
        _stages(v)
        st.write("")
        _outlook(v)
        st.write("")
        if is_lake:
            _lake_context(v)
        else:
            _river(v)
            _dam(v)
        st.write("")
        _sources(v)
        _indicator_guide(v)
        _calibration(conn, v)
    finally:
        conn.close()


main()
