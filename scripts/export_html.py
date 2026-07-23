"""現在のDB状態を、サーバ不要の自己完結HTML（無料公開用）に書き出す。

Streamlitライブ版と違い、これは「その時点のスナップショット」。GitHub Pages 等に置くか、
GitHub Action で日次再生成すれば無料の公開HPになる（APIキーはAction内でのみ使用、出力の
静的HTMLには残らない）。初心者が一目で〈行く/様子見/見送り〉と〈なぜ・どう釣るか〉を
掴めることを最優先に構成する。静的ファイルなので「データ基準日」と「ページ生成時刻」を
別々に明示し、鮮度を誤認させない。

判定単位は「区間(reach)」。同じ河川でも自然流量区間とダム下流区間は挙動が真逆のため、
河川ではなく区間で描く。source_confidence が verified の区間を先頭・強調し、参考区間は
「参考」バッジ付きで後段に並べる（物理データと注意書きは出すが確信 GO は出さない）。

  python -m scripts.export_html [出力パス]
"""
from __future__ import annotations

import datetime as dt
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.river_map import river_map_svg  # noqa: E402
from app.trout_art import trout_svg  # noqa: E402
from src import calibration, config, db, decision, guide  # noqa: E402

logger = logging.getLogger(__name__)

JST = dt.timezone(dt.timedelta(hours=9))
WATER_CLASS = {0: "water-clear", 1: "water-sasa", 2: "water-doro", None: "water-unknown"}
WATER_WORD = {0: "クリア", 1: "笹濁り", 2: "泥濁り", None: "情報なし"}
LEVEL_JP = {"GO": "行くべし", "CAUTION": "様子見", "NO_GO": "見送り"}
# source_confidence → (見出しバッジ文言, CSSクラス)
SRC_CONF = {
    "verified": ("確度: 高", "rb-ok"),
    "参考": ("参考", "rb-ref"),
    "未確認": ("未確認", "rb-unk"),
}
# C&Rリスク帯 → (表示語, CSSクラス)
CR_WORD = {
    "safe": ("安全", "cr-safe"),
    "caution": ("注意", "cr-caution"),
    "strong": ("強い注意", "cr-strong"),
    "nogo": ("見送り推奨", "cr-nogo"),
    "unknown": ("不明", "cr-unk"),
}


def _source_urls(reach_id):
    """この区間の一次情報リンク（reach の公式/釣況 + 気象/水位/ダムの公開源）。"""
    reach = config.REACHES[reach_id]
    river, location = reach["river"], reach["location"]
    code = config.JMA_STATIONS[location]["code"]
    water = config.RIVER_WATER_LEVEL.get(river, {})
    has_dam_id = bool(config.reach_dams(reach_id))     # ID確認済みダムを持つか
    return {
        "気象・日照(AMeDAS)": config.JMA_AMEDAS_PAGE.format(code=code),
        "週間予報": config.JMA_FORECAST_PAGE,
        "水位(Yahoo川)": water.get("yahoo_url"),
        "漁協・公式情報": reach.get("official_url"),
        "釣況・釣果(外部)": reach.get("catch_ref_url"),
        "ダム放流": ("https://www.ktr.mlit.go.jp/tonedamu/"
                  if (has_dam_id and river == "利根川") else None),
    }


def _fmt(x, unit=""):
    return "—" if x is None else (f"{x:.1f}{unit}" if isinstance(x, float) else f"{x}{unit}")


def _temp_txt(t):
    return "—" if t is None else f"{t:.0f}℃"


def _poly(seg, extra):
    if len(seg) <= 1:
        return ""
    pstr = " ".join(f"{x:.0f},{y:.0f}" for x, y in seg)
    return f'<polyline points="{pstr}" fill="none" stroke-width="2.5" {extra}/>'


def _sparkline(series, as_of):
    """Inline SVG of the TSI trajectory — solid+area=actual, dashed=forecast.

    Colors come from CSS classes (not attributes) so the chart follows the theme.
    """
    if not series:
        return ""
    pts = series[-21:]
    w, h = 340, 76
    n = len(pts)
    xs = [w * i / (n - 1) if n > 1 else 0 for i in range(n)]
    ys = [h - 8 - (s.tsi / 100.0) * (h - 16) for s in pts]
    act = [(x, y) for x, y, s in zip(xs, ys, pts) if as_of is None or s.date <= as_of]
    fc = [(x, y) for x, y, s in zip(xs, ys, pts) if as_of is not None and s.date >= as_of]
    today_x = act[-1][0] if act else 0.0
    grid = "".join(
        f'<line class="spark-grid" x1="0" y1="{h - 8 - v / 100.0 * (h - 16):.0f}" '
        f'x2="{w}" y2="{h - 8 - v / 100.0 * (h - 16):.0f}"/>' for v in (0, 50, 100))
    area = ""
    if len(act) > 1:
        pstr = " ".join(f"{x:.0f},{y:.0f}" for x, y in act)
        area = (f'<polygon class="spark-area" points="{act[0][0]:.0f},{h - 8} {pstr} '
                f'{act[-1][0]:.0f},{h - 8}"/>')
    act_poly = _poly(act, 'class="spark-act"')
    fc_poly = _poly(fc, 'class="spark-fc"')
    return (f'<svg viewBox="0 0 {w} {h}" width="100%" height="76" '
            f'preserveAspectRatio="none" aria-label="適性メーター(TSI)の推移">{grid}{area}'
            f'{act_poly}{fc_poly}'
            f'<line class="spark-today" x1="{today_x:.0f}" y1="6" x2="{today_x:.0f}" '
            f'y2="{h - 6}"/></svg>')


def _hero_html(v):
    tsi = v.tsi or 0
    sc_word, sc_cls = SRC_CONF.get(v.source_confidence, (v.source_confidence, "rb-unk"))
    return f"""
  <div class="hero {WATER_CLASS.get(v.turbidity, 'water-unknown')}">
    <div class="fish">{trout_svg(v.mood)}</div>
    <div class="hero-body">
      <div class="hero-top"><span class="badge badge-{v.level}">{LEVEL_JP[v.level]}</span>
        <span class="chip">信頼度 {int(v.confidence * 100)}%</span>
        <span class="hchip {sc_cls}">データ源 {sc_word}</span></div>
      <div class="one">{guide.VERDICT_ONELINER[v.level]}</div>
      <div class="headline">{v.headline}</div>
      <div class="sub">適性メーター(TSI) {tsi:.0f}/100・{v.effective_quality or '—'}（{v.quality_source}）・水色:{WATER_WORD.get(v.turbidity)}</div>
      <div class="meter"><div class="meter-fill" style="width:{tsi:.0f}%"></div>
        <span class="meter-tick" style="left:55%" title="好適の目安(TSI55)"></span>
        <span class="meter-tick" style="left:68%" title="絶好の目安(TSI68)"></span></div>
      <div class="method">{guide.method_label(v.methods, v.catch_release)}</div>
    </div>
  </div>"""


def _next_good_banner(v):
    """最も行動につながる出力（次に狙える日）をヒーロー直下に。"""
    o = v.outlook
    if not o:
        return ""
    ng = o.get("next_good")
    # 参考区間は今日の判定で確信GOを出さない方針。前向きの「次に行くなら」も同じトーンに揃え、
    # 緑の断定表示を弱めて「予報上の目安」と明示する（方針を表示層まで貫徹）。
    tentative = v.source_confidence != "verified"
    day_cls = "go-day tentative-day" if tentative else "go-day"
    ref_note = ("（参考区間・予報上の目安です）" if tentative else "")
    if ng:
        rel = f"・予報信頼度{ng['reliability']}" if ng.get("reliability") else ""
        return (f'<div class="panel nextgood"><span class="ng-label">次に行くなら</span>'
                f'<span class="{day_cls}">{guide.jp_date(ng["date"])} 頃</span>'
                f'<span class="src">TSI{ng["tsi"]:.0f}・{ng["quality"]}{rel}'
                f' — 予報上、水温が適域で増水も無い最初の日です{ref_note}</span></div>')
    return ('<div class="panel nextgood"><span class="ng-label">次に行くなら</span>'
            '<span class="go-day muted-day">今後1週間は『行くべし』級の日が見当たりません</span>'
            '<span class="src">水温が適域から外れる、または増水の懸念があります</span></div>')


def _watertemp_html(v):
    """水温 & C&R パネル（魚種特化の目玉）。水温がニジマス判定の主軸であることを明示。"""
    obs = v.observed_water_temp
    temp = obs if obs is not None else v.water_temp_proxy
    if obs is not None:
        cf = (f"（抽出確信度 {int(v.observed_confidence * 100)}%）"
              if isinstance(v.observed_confidence, (int, float)) else "")
        temp_tag = '<span class="wt-tag wt-obs">現場報告（ブログ記載）</span>'
        src_note = ("釣況ブログの記載からGeminiが読み取った水温です" + cf +
                    "。ブログ主の計測値か体感かは区別できないため、参考としてご覧ください")
    else:
        temp_tag = '<span class="wt-tag wt-proxy">気温からの換算・未較正プロキシ</span>'
        src_note = ("ライブ水温源が無いため気温からの換算値です（未較正プロキシ）。"
                    "「実測」とは偽らず、現場報告があればそちらを優先します")
    cr_word, cr_cls = CR_WORD.get(v.cr_risk, ("不明", "cr-unk"))
    if v.catch_release:
        cr_chip = f'<span class="cr-chip {cr_cls}">C&Rリスク: {cr_word}</span>'
        cr_msg = guide.cr_note(v.cr_risk, v.catch_release)
        msg_html = f'<div class="wt-msg {cr_cls}">{cr_msg}</div>' if cr_msg else ""
        tail = ("この区間は全キャッチ&リリースです。『釣れるか』の前に"
                "『釣って離した魚が生きられる水温か』を保全ゲートとして判定します。")
    else:
        cr_chip = f'<span class="cr-chip {cr_cls}">水温の目安: {cr_word}</span>'
        msg_html = ""
        tail = "この区間はキープ可の一般区間です。水温はニジマスの活性（釣果）の主軸として表示します。"
    return ('<div class="panel wt"><b class="ph">水温 と キャッチ&リリース — この判定の主軸</b>'
            '<div class="wt-grid">'
            f'<div class="wt-fig"><span class="wt-val">{_temp_txt(temp)}</span>'
            '<span class="wt-cap">推定水温</span></div>'
            f'<div class="wt-box">{temp_tag}{cr_chip}</div></div>'
            f'{msg_html}'
            f'<div class="src">水温はニジマス（冷水魚）の活性・摂餌・C&R安全性をほぼ決める主軸です。'
            f'摂餌スイートスポットは10〜16℃、20℃超で摂餌停止・C&R危険。{src_note}。{tail}</div></div>')


def _checklist_html(v):
    """GO ゲートを設計されたゲート部品として可視化（『なぜ行くべしでないか』）。

    充足数のセグメントゲージ + CSS描画の合否マーク（絵文字に頼らない）。build_verdict と SSOT。
    """
    rows = decision.go_checklist(v)
    n_ok = sum(1 for r in rows if r["ok"])
    n_unk = sum(1 for r in rows if not r["ok"] and r.get("unknown"))
    n_fail = len(rows) - n_ok - n_unk
    segs = "".join('<span class="on"></span>' if i < n_ok else "<span></span>"
                   for i in range(len(rows)))
    lis = "".join(
        f'<li class="{"g-ok" if r["ok"] else "g-unk" if r.get("unknown") else "g-no"}">'
        f'<span class="gmark"></span>'
        f'<span class="glabel">{r["label"]}</span>'
        f'<span class="gdetail">{r["detail"]}</span></li>'
        for r in rows)
    if v.level == "GO":
        head = '<div class="ckhead allok">GO条件をすべて充足 — 『行くべし』</div>'
    else:
        parts = []
        if n_fail:
            parts.append(f"未達 {n_fail}件")
        if n_unk:
            parts.append(f"情報待ち {n_unk}件（？）")
        head = (f'<div class="ckhead">『行くべし』に届かない条件: {"・".join(parts)}'
                '。情報が揃わない項目がある間は GO を出しません</div>')
    return (f'<div class="panel"><b class="ph">なぜこの判定か — GO条件</b>'
            f'<div class="gate-top"><span class="gate-count">{n_ok}'
            f'<span class="gate-of">/{len(rows)}</span></span>'
            f'<div class="gate-seg">{segs}</div></div>'
            f'{head}<ul class="gate-list">{lis}</ul></div>')


def _tips_html(v):
    wt = v.observed_water_temp if v.observed_water_temp is not None else v.water_temp_proxy
    tips = guide.fishing_tips(v.level, v.effective_quality, v.turbidity, v.water_trend,
                             v.methods, v.days_since_stock, water_temp=wt)
    lis = "".join(f"<li>{t}</li>" for t in tips)
    return ('<div class="panel tips"><b class="ph">今日の狙い方</b>'
            '<div class="src">条件から導いたルアー/フライ/エサの一般的な定石です。'
            '立ち位置と安全は、現地でご自身の目でご判断ください。</div>'
            f'<ul>{lis}</ul></div>')


def _stages_html(v):
    """水温帯ガイド。今の状態(effective_quality)をハイライトし、WHY_TROUT を併記。"""
    cur = v.effective_quality
    cards = []
    for s in guide.TROUT_STAGES:
        on = " on" if s["state"] == cur else ""
        now = '<span class="mnow">今ここ</span>' if s["state"] == cur else ""
        cards.append(
            f'<div class="mstage{on}"><div class="mbar" style="background:{s["color"]}"></div>'
            f'<div class="mhd">{s["emoji"]} {s["state"]}{now}</div>'
            f'<div class="mrow"><b>水温:</b> {s["temp"]}</div>'
            f'<div class="mrow"><b>魚:</b> {s["fish"]}</div>'
            f'<div class="mcatch">{s["how"]}</div></div>')
    if cur in guide.STATE_SHORT:
        foot = f"いまは緑枠の『{cur}』（{guide.STATE_SHORT[cur]}）が今の期待値です。"
    else:
        foot = "いまの状態は情報が薄く未確定です。現地で水温・水色をご確認ください。"
    return ('<div class="panel"><b class="ph">水温とニジマスの関係 — なぜこれで釣果が決まるのか</b>'
            f'<p class="why">{guide.WHY_TROUT}</p>'
            f'<div class="stagegrid">{"".join(cards)}</div>'
            f'<div class="src">{foot}</div></div>')


def _outlook_html(v):
    o = v.outlook
    if not o:
        return ""
    ng = o.get("next_good")
    tref = "（参考区間・予報上の目安）" if v.source_confidence != "verified" else ""
    if ng:
        rel = f"・予報信頼度{ng['reliability']}" if ng.get("reliability") else ""
        head = (f'<div class="ostat"><b>次に行くなら {guide.jp_date(ng["date"])} 頃</b>'
                f' — TSI{ng["tsi"]:.0f}・{ng["quality"]}{rel}{tref}</div>')
    else:
        head = ('<div class="ostat">今後1週間は、予報上『行くべし』級の日が'
                '見当たりません（水温が適域から外れる、または増水の懸念があります）。</div>')
    best = o["best"]
    best_rel = f"・信頼度{best['reliability']}" if best.get("reliability") else ""
    wk = "　".join(f'{w["date"][5:]}({w["wd"]}) TSI{w["tsi"]:.0f}' for w in o["weekend"])
    okv = [("傾向", o["trend"]),
           ("ピーク", f'TSI{best["tsi"]:.0f}（{guide.jp_date(best["date"])}頃・'
                     f'{best["quality"]}{best_rel}）')]
    if wk:
        okv.append(("次の週末", wk))
    if o["scours"]:
        okv.append(("☔ 増水・リセット懸念日", "、".join(guide.jp_date(s) for s in o["scours"])))
    rows = "".join(f'<div class="okv"><span class="ok-k">{k}</span>'
                   f'<span class="ok-v">{val}</span></div>' for k, val in okv)
    return ('<div class="panel"><b class="ph">今後1週間の見通し（気象庁 週間予報ベース・予報は不確実）</b>'
            f'{head}'
            f'<div class="ostats">{rows}</div>'
            f'{_sparkline(v.series, v.as_of)}'
            '<div class="src">実線=実績 ／ 橙破線=予報投影 ／ 灰線=今日（基準日）。'
            '予報信頼度は気象庁の A＞B＞C です。TSI は水温を主軸とした適性推定で、'
            '日ごとに独立に算出しています（蓄積状態は持ちません）。</div></div>')


def _delta_html(delta):
    """前回更新からの差分。初回（前回スナップショットなし）は出さない。"""
    if delta is None:
        return ""
    if delta["changes"]:
        body = "".join(f"<li>{c}</li>" for c in delta["changes"])
        body = f'<ul class="chg">{body}</ul>'
    else:
        body = ('<div class="src">大きな変化はありません'
                '（判定・適性メーター・水位・濁りとも前回と同等です）</div>')
    return (f'<div class="panel"><b class="ph">前回更新（{guide.jp_date(delta["prev_date"])}）'
            f'からの変化</b>{body}</div>')


def _calibration_html(recon):
    """予実照合 — モデルのコンディション予想が現場報告とどれだけ合っているか（正直に）。"""
    if recon is None:
        return ""
    if recon["n"] == 0:
        body = f'<div class="src">{recon["note"]}</div>'
    else:
        rows = "".join(
            f'<tr><td>{guide.jp_date(r["date"])}</td><td>{r["predicted"]}</td>'
            f'<td>{r["observed"]}'
            + (f'（釣果{r["catch"]}匹）' if r["catch"] is not None else "")
            + f'</td><td>{calibration.GRADE_WORD[r["grade"]]}</td></tr>'
            for r in recon["rows"])
        shown = ("" if recon["n"] <= len(recon["rows"])
                 else f'（表は直近{len(recon["rows"])}件・集計は全{recon["n"]}件）')
        body = ('<div class="tablewrap"><table class="ind recon"><thead><tr><th>観測日</th>'
                '<th>モデル予想</th><th>現場報告（ブログ）</th><th>照合</th></tr></thead>'
                f'<tbody>{rows}</tbody></table></div>'
                f'<div class="src">{recon["note"]}{shown}</div>')
    return ('<details class="panel"><summary><b class="ph">予実照合 — モデルはどれだけ当たっているか</b>'
            '</summary>'
            '<p class="why">TSI（適性）は未較正の推定です。釣り人のブログ報告（体感コンディション）との'
            '一致率を毎日蓄積し、これを較正の根拠にします'
            '（運営者の釣果ではなく、公開の現場報告で検証する方針です）。</p>'
            + body +
            '<div class="src">⚠️ 泥濁り・増水報告はリセットとしてモデル入力（水位ステージ）にも使われるため、'
            '増水直後の「リセット一致」は独立の検証ではありません。体感コンディション観測そのものは'
            'モデルに入らないため、それ以外の照合は独立です。また、ブログの体感語彙は4段階で、'
            'モデルの「高水温危険」「増水リセット」を表現できないため、その日の照合は構造的に'
            '控えめに出ます。</div></details>')


def _flow_html(v, urls):
    tsi = v.tsi or 0
    sun_src = "気象庁AMeDAS(推計)" if v.sunshine_estimated else "気象庁AMeDAS(観測)"
    obs_age = ""
    if v.observed_quality is not None or v.observed_catch is not None:
        obs_age = f"（{v.staleness_days}日前）" if v.staleness_days is not None else "（投稿日不明）"
    temp = v.observed_water_temp if v.observed_water_temp is not None else v.water_temp_proxy
    temp_src = "現場報告(ブログ)" if v.observed_water_temp is not None else "気温から換算(未較正)"
    flow_items = [
        ("☀️ 日照", _fmt(v.sunshine_h, "h/日"), sun_src, urls["気象・日照(AMeDAS)"]),
        ("🌡️ 気温", _fmt(v.air_temp, "℃"), "気象庁AMeDAS", urls["気象・日照(AMeDAS)"]),
        ("🌊 水位", f"{v.water_status or '—'} {v.water_trend or ''}", "Yahoo川ミラー",
         urls["水位(Yahoo川)"]),
        ("📝 現場報告", f"{WATER_WORD.get(v.turbidity)}／{v.observed_quality or 'コンディション報告なし'}"
         f"／釣果{_fmt(v.observed_catch)}{obs_age}",
         "釣況/漁協", urls["釣況・釣果(外部)"] or urls["漁協・公式情報"]),
        ("💧 水温", _temp_txt(temp), temp_src, None),
        ("🎣 適性メーター(TSI)", f"{tsi:.0f}/100 ({v.model_quality or '—'})",
         "水温×濁り×日照から推定", None),
    ]
    flow = "".join(
        f'<div class="fi"><span class="fi-k">{t}</span><span class="fi-v">{val}</span>'
        '<span class="src">元: '
        + (f'<a href="{u}" target="_blank" rel="noopener">{s} ↗</a>' if u else s) + "</span></div>"
        for t, val, s, u in flow_items)
    trace = ""
    if v.observed_excerpt:
        conf = (f"／抽出確信度(LLM自己申告) {int(v.observed_confidence * 100)}%"
                if isinstance(v.observed_confidence, (int, float)) else "")
        trace = (f'<div class="fieldtrace">現場報告の根拠（ブログ引用）：'
                 f'「{v.observed_excerpt}」{conf}</div>')
    return ('<div class="panel"><b class="ph">この判定の作り方（何を元に何を出したか・出所はクリックで開く）</b>'
            f'<div class="flow">{flow}</div>{trace}'
            '<div class="src">公開データ（生）→ 導出（水温/TSI）→ 判定、の順で組み立てます。'
            '優先順位は 危険（増水・泥濁り・ダム）→ 魚の生存（C&R水温）→ 現場報告 → TSI の順です。'
            '</div></div>')


def _map_html(v):
    reach = config.REACHES[v.reach_id]
    river = reach["river"]
    cfg = config.RIVER_WATER_LEVEL.get(river, {})
    stations = cfg.get("stations", [])
    points = []
    for name in stations:
        stt = v.stations.get(name, {})
        status = stt.get("water_level_status")
        is_reach = name == reach["water_station"]
        points.append({"name": name, "status": status,
                       "trend": stt.get("water_trend"),
                       "sev": config.LEVEL_SEVERITY.get(status or "", 0),
                       "mark": is_reach,
                       "zone_label": "本区間" if is_reach else ""})
    rmap = river_map_svg(points, river=river, upstream=cfg.get("map_upstream", "上流"),
                         downstream=cfg.get("map_downstream", "下流"))
    missing = not stations or all(
        v.stations.get(n, {}).get("water_level_status") is None for n in stations)
    note = ""
    if missing:
        note = ('<div class="warnbar">この区間は水位観測点のミラーが未確認、または欠測です'
                '（渡良瀬川など）。水位が取れないため増水は現地・公式の防災情報で必ずご確認ください。'
                '判定は安全側（GO保留）に倒しています。</div>')
    return ('<div class="panel"><b class="ph">川マップ（各観測点の増水状況）</b>'
            f'{rmap}{note}'
            '<div class="src">⚠️ 気象・水温は観測点が1つのため区間内で共通です。'
            '観測点ごとに違うのは水位のみで、🎣（本区間）が判定の代表観測点です。</div></div>')


def _dam_html(v):
    dr = v.dam_risk
    if dr is None:
        return ('<div class="panel"><b class="ph">上流ダム放流（濁りの前兆）</b>'
                '<div class="ok">この区間は上流にダムが無い自然流量です'
                '（ダム放流による急な濁りの心配はありません）。</div></div>')
    if dr.get("risk"):
        w = dr["worst"]
        pct = "急増" if w["slope_pct"] >= 900 else f"+{int(w['slope_pct'] * 100)}%"
        body = (f'<div class="alert">⚠️ {w["dam"]}ダムが放流{pct}（直近 {w["discharge"]:.0f} m³/s）'
                f'→ 濁り放流リスク。{w["eta_text"]}。</div>')
    else:
        seen = dr["dams_seen"]
        id_missing = dr.get("id_missing") or []
        if id_missing:
            body = ('<div class="warnbar">監視対象ダム（' + "・".join(id_missing) +
                    '）はダムIDが未確認で放流データを取得できていません（<b>未確認</b>）。'
                    'この区間の濁り前兆に空白があります。現地・水位でご確認ください。</div>')
        elif seen == 0:
            body = ('<div class="warnbar">上流ダムの放流データが取得できていません'
                    '（<b>不明</b>。平穏の確認ではありません）。現地・水位でご確認ください。</div>')
        else:
            unread = seen - dr.get("slope_known", seen)
            if unread:
                extra = f"（判明している{dr['slope_known']}基に急な放流はありません）"
                body = (f'<div class="warnbar">上流ダムのうち{unread}基は放流傾向が欠測です{extra}。'
                        '濁りの前兆に空白があるため、現地や水位でご確認ください。</div>')
            else:
                body = (f'<div class="ok">上流{seen}ダムに急な放流はありません'
                        '（濁り放流の前兆なし）</div>')
    return ('<div class="panel"><b class="ph">上流ダム放流（濁りの前兆）</b>' + body
            + '<div class="src">ダム下流の区間では、上流ダムの放流増を濁り水の前兆として監視します。'
            '到達時間は各ダムの距離と流速の仮定から出した粗い目安です（実測ではありません。水位の波の'
            '到達時間で、濁り本体はさらに遅れて届きます）。</div></div>')


def _indicator_html():
    rows = "".join(
        f'<tr><td class="ik">{g["key"]}</td><td>{g["what"]}</td>'
        f'<td>{g["high_low"]}</td><td class="src">{g["source"]}</td></tr>'
        for g in guide.INDICATOR_GUIDE)
    return ('<details class="panel"><summary><b class="ph">指標の読み方（高い/低いとどうなる）</b></summary>'
            '<div class="tablewrap"><table class="ind"><thead><tr><th>指標</th><th>何の値？</th>'
            f'<th>高い/低いとどうなる</th><th>何を元に</th></tr></thead><tbody>{rows}</tbody></table>'
            '</div></details>')


def _sources_html(v, urls):
    src_links = " ・ ".join(f'<a href="{u}" target="_blank" rel="noopener">{k} ↗</a>'
                           for k, u in urls.items() if u)
    caveats = "".join(f"<li>{c}</li>" for c in v.caveats)
    return ('<details class="panel"><summary><b class="ph">情報源と『信じすぎない』注意書き</b></summary>'
            f'<p><b>情報源:</b> {src_links}</p>'
            f'<p><b>この判定を過信しないための注意点:</b></p><ul class="cav">{caveats}</ul></details>')


def render_reach(v, reach, run_date, delta=None, recon=None, ref=False):
    # Static-surface guard: 実データが無い区間は判定を出さない（架空の live verdict を公開しない）。
    if v.as_of is None:
        return (f'<section><h2>{reach["label"]}</h2>'
                '<div class="warnbar">この区間はまだ実データがありません（収集待ち）。'
                '判定は表示しません。</div></section>')
    sc_word, sc_cls = SRC_CONF.get(v.source_confidence, (v.source_confidence, "rb-unk"))
    urls = _source_urls(v.reach_id)
    fresh = guide.freshness(v.as_of, run_date)
    obs_times = [s.get("date_time") for s in v.stations.values() if s.get("date_time")]
    latest_obs = max(obs_times) if obs_times else None
    upd = ""
    if latest_obs and len(latest_obs) >= 16:
        if latest_obs[:10] != v.as_of:
            upd = f'・水位更新 {latest_obs[5:10].replace("-", "/")} {latest_obs[11:16]}'
        else:
            upd = f'・水位更新 {latest_obs[11:16]}'
    asof = (f'<div class="asof"><span class="cal">{guide.jp_date(v.as_of, with_year=True)} のデータ</span>'
            f'<span class="fresh fresh-{fresh["level"]}">{fresh["label"]}</span>'
            f'<span class="src">{upd} ／ 前後の推移は見通しグラフ（実線=実績・破線=予報）</span></div>')
    ref_note = ""
    if ref:
        ref_note = ('<div class="warnbar">この区間は公式データ源の実在確認が『参考』レベルです。'
                    '物理データ（気象・水位・水温・ダム）と季節の注意書きは出しますが、'
                    '確信を持った『行くべし』は出していません（自動で様子見へ格下げ済み）。'
                    '正確な期間・区間・ルールは各漁協でご確認ください。</div>')
    return f"""
<section>
  <h2>{reach["label"]}<span class="rbadge {sc_cls}">{sc_word}</span></h2>
  {asof}
  {ref_note}
  {_hero_html(v)}
  {_delta_html(delta)}
  {_next_good_banner(v)}
  {_watertemp_html(v)}
  {_checklist_html(v)}
  {_tips_html(v)}
  {_stages_html(v)}
  {_outlook_html(v)}
  {_flow_html(v, urls)}
  {_calibration_html(recon)}
  {_dam_html(v)}
  {_map_html(v)}
  {_indicator_html()}
  {_sources_html(v, urls)}
</section>"""


CSS = """<style>
/* ---- palette: 渓の青緑 / 若草 / ニジマスの桃色側線。和紙がかった白緑の地 ---- */
:root{--bg:#eef3f0;--panel:#f8fbf9;--ink:#20302a;--muted:#5c7268;--line:#d4e0da;
  --accent:#2f8f7a;--gold:#d1788f;--bad:#b5472f;--warn:#c07a2b;
  --serif:"Hiragino Mincho ProN","Yu Mincho","Noto Serif JP",serif;
  --sans:"Hiragino Sans","Yu Gothic","Noto Sans JP",system-ui,sans-serif}
@media(prefers-color-scheme:dark){:root{--bg:#0c1310;--panel:#121b16;--ink:#dce8e2;
  --muted:#8aa398;--line:#23322b;--accent:#5fb89e;--gold:#e29bae;--bad:#dd7a5f;--warn:#d9a04e}}
:root[data-theme="dark"]{--bg:#0c1310;--panel:#121b16;--ink:#dce8e2;--muted:#8aa398;
  --line:#23322b;--accent:#5fb89e;--gold:#e29bae;--bad:#dd7a5f;--warn:#d9a04e}
:root[data-theme="light"]{--bg:#eef3f0;--panel:#f8fbf9;--ink:#20302a;--muted:#5c7268;
  --line:#d4e0da;--accent:#2f8f7a;--gold:#d1788f;--bad:#b5472f;--warn:#c07a2b}
*{box-sizing:border-box}body,.wrap{margin:0}
.wrap{background:var(--bg);color:var(--ink);min-height:100vh;padding:34px 16px 64px;
  font-family:var(--sans);line-height:1.55;font-feature-settings:"palt"}
.inner{max-width:980px;margin:0 auto}
a{color:inherit;text-underline-offset:3px;text-decoration-thickness:1px;text-decoration-color:var(--muted)}
/* ---- masthead ---- */
.eyebrow{font-size:.76rem;letter-spacing:.24em;color:var(--muted);margin-bottom:6px}
h1{font-family:var(--serif);font-size:2.15rem;font-weight:600;margin:0;letter-spacing:.06em;text-wrap:balance}
.lead{color:var(--muted);font-size:.88rem;margin:.6em 0 0;max-width:46em}
.lead b{color:var(--ink)}
h2{font-family:var(--serif);font-weight:600;font-size:1.5rem;letter-spacing:.05em;
  margin:44px 0 4px;padding-top:16px;border-top:1px solid var(--line);display:flex;
  align-items:baseline;flex-wrap:wrap;gap:10px}
.rbadge{font-family:var(--sans);font-size:.7rem;letter-spacing:.1em;color:#fff;
  border-radius:999px;padding:3px 11px;font-weight:700;align-self:center}
.rb-ok{background:var(--accent)}.rb-ref{background:var(--warn)}.rb-unk{background:var(--muted)}
/* ---- reference-group divider ---- */
.refhead{margin-top:20px}
.refh2{font-family:var(--serif);font-weight:600;font-size:1.16rem;letter-spacing:.04em;
  margin:40px 0 4px;padding:14px 0 0;border-top:2px dashed var(--line);color:var(--warn)}
/* ---- date banner ---- */
.datebar{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 12px;margin:20px 0 4px;
  padding:13px 18px;border:1px solid var(--line);border-radius:10px;background:var(--panel)}
.dcal{font-family:var(--serif);font-size:1.55rem;font-weight:600;letter-spacing:.04em}
.dnote{font-size:.9rem;color:var(--muted)}
.genstamp{font-size:.7rem;color:var(--muted);border:1px solid var(--line);border-radius:999px;
  padding:2px 10px;margin-left:auto;font-variant-numeric:tabular-nums}
.asof{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 10px;margin:6px 0 12px}
.cal{font-family:var(--serif);font-size:1.08rem;font-weight:600}
.fresh{font-size:.7rem;border-radius:999px;padding:2px 10px;font-weight:700}
.fresh-ok{background:color-mix(in srgb,var(--accent) 15%,transparent);color:var(--accent)}
.fresh-warn{background:color-mix(in srgb,var(--warn) 16%,transparent);color:var(--warn)}
/* ---- legend ---- */
.legend{display:flex;flex-wrap:wrap;gap:8px;margin:14px 0 4px;font-size:.76rem}
.legend span{border:1px solid var(--line);border-radius:999px;padding:3px 11px;background:var(--panel);
  display:inline-flex;align-items:center;gap:6px}
.dot{width:8px;height:8px;border-radius:50%;display:inline-block;flex:0 0 8px}
.dot-go{background:var(--accent)}.dot-caution{background:var(--warn)}.dot-nogo{background:var(--bad)}
.dot-temp{background:var(--gold)}
/* ---- hero (背景色=水色そのもの、という意味構造) ---- */
.hero{border-radius:14px;padding:20px 24px;color:#fff;display:flex;align-items:center;gap:18px;
  box-shadow:0 10px 26px rgba(20,40,32,.18);margin-top:12px}
.water-clear{background:linear-gradient(135deg,#3f9fc4,#1c6b7a)}
.water-sasa{background:linear-gradient(135deg,#2f9e86,#175b4b)}
.water-doro{background:linear-gradient(135deg,#9d8570,#5c4636)}
.water-unknown{background:linear-gradient(135deg,#7d918f,#42544f)}
.fish{width:150px;height:90px;flex:0 0 150px;filter:drop-shadow(0 3px 6px rgba(0,0,0,.18))}
.hero-body{flex:1;min-width:0}
.hero-top{display:flex;flex-wrap:wrap;align-items:center;gap:8px}
.badge{display:inline-block;padding:5px 16px;border-radius:999px;font-weight:800;font-size:1.02rem;
  letter-spacing:.08em;box-shadow:inset 0 0 0 1px rgba(255,255,255,.35)}
.badge-GO{background:#2f7d6b}.badge-CAUTION{background:#c07a2b}.badge-NO_GO{background:#b5472f}
.chip{font-size:.72rem;padding:3px 10px;border-radius:999px;background:rgba(255,255,255,.22);
  font-variant-numeric:tabular-nums}
.hchip{font-size:.7rem;padding:3px 10px;border-radius:999px;font-weight:700;
  background:rgba(255,255,255,.2)}
.hchip.rb-ok{background:rgba(255,255,255,.32)}
.hchip.rb-ref{background:rgba(0,0,0,.22)}
.one{font-size:.98rem;font-weight:700;margin:10px 0 2px;opacity:.96}
.headline{font-size:1.22rem;font-weight:800;margin:2px 0 4px}
.sub{opacity:.92;font-size:.84rem;font-variant-numeric:tabular-nums}
.method{font-size:.78rem;opacity:.92;margin-top:8px;font-weight:600;letter-spacing:.02em}
/* 適性メーター(TSI): 55(好適)/68(絶好)の閾値目盛りを刻む */
.meter{height:14px;border-radius:7px;background:rgba(255,255,255,.24);margin-top:12px;position:relative}
.meter-fill{height:100%;border-radius:7px;background:linear-gradient(90deg,#b9d98a,var(--gold));
  box-shadow:0 1px 3px rgba(0,0,0,.2)}
.meter-tick{position:absolute;top:-3px;bottom:-3px;width:2px;background:rgba(255,255,255,.55)}
/* ---- panels: 見出しは明朝+桃色の菱マーカー ---- */
.panel{border:1px solid var(--line);border-radius:10px;padding:15px 18px;margin-top:14px;background:var(--panel)}
.ph{display:block;font-family:var(--serif);font-weight:600;font-size:1.06rem;letter-spacing:.03em;margin-bottom:7px}
.ph::before{content:"";display:inline-block;width:7px;height:7px;background:var(--gold);
  transform:rotate(45deg);margin-right:10px;vertical-align:2px}
.src{font-size:.76rem;color:var(--muted);line-height:1.6}
/* ---- 次に行くなら ---- */
.nextgood{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 10px;
  background:color-mix(in srgb,var(--accent) 6%,var(--panel))}
.ng-label{font-family:var(--serif);font-weight:600;font-size:.92rem;letter-spacing:.1em;color:var(--muted)}
.go-day{font-family:var(--serif);font-size:1.28rem;font-weight:700;color:var(--accent);letter-spacing:.03em}
.go-day.muted-day{color:var(--muted);font-size:1.02rem;font-weight:600}
.go-day.tentative-day{color:var(--muted);font-weight:600;text-decoration:underline dotted}
.nextgood .src{flex-basis:100%}
/* ---- 水温 & C&R パネル（魚種特化の目玉） ---- */
.wt{background:color-mix(in srgb,var(--accent) 5%,var(--panel))}
.wt-grid{display:flex;flex-wrap:wrap;align-items:center;gap:14px 22px;margin:8px 0}
.wt-fig{display:flex;flex-direction:column;align-items:center;min-width:110px}
.wt-val{font-family:var(--serif);font-size:2.1rem;font-weight:700;line-height:1;
  font-variant-numeric:tabular-nums}
.wt-cap{font-size:.72rem;color:var(--muted);margin-top:3px;letter-spacing:.06em}
.wt-box{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.wt-tag{font-size:.74rem;border:1px solid var(--line);border-radius:999px;padding:3px 11px;
  color:var(--muted);background:var(--bg)}
.wt-tag.wt-obs{color:var(--accent);border-color:var(--accent)}
.cr-chip{font-size:.82rem;font-weight:800;border-radius:999px;padding:4px 13px;color:#fff}
.cr-safe{background:var(--accent)}.cr-caution{background:var(--warn)}
.cr-strong{background:#c0603b}.cr-nogo{background:var(--bad)}.cr-unk{background:var(--muted)}
.wt-msg{margin-top:6px;padding:10px 13px;border-radius:9px;font-weight:700;font-size:.9rem;
  border:1px solid var(--line)}
.wt-msg.cr-caution{background:color-mix(in srgb,var(--warn) 11%,transparent);border-color:var(--warn)}
.wt-msg.cr-strong{background:color-mix(in srgb,var(--bad) 9%,transparent);border-color:var(--bad)}
.wt-msg.cr-nogo{background:color-mix(in srgb,var(--bad) 12%,transparent);border-color:var(--bad)}
/* ---- GO条件ゲート ---- */
.gate-top{display:flex;align-items:center;gap:14px;margin:8px 0 2px}
.gate-count{font-family:var(--serif);font-size:1.7rem;font-weight:700;line-height:1;
  font-variant-numeric:tabular-nums;flex:0 0 auto}
.gate-of{font-size:.95rem;color:var(--muted);font-weight:600}
.gate-seg{display:flex;gap:5px;flex:1;min-width:120px}
.gate-seg span{height:7px;flex:1;border-radius:4px;background:var(--line)}
.gate-seg span.on{background:var(--accent)}
.ckhead{font-weight:700;font-size:.9rem;margin:6px 0 2px;color:var(--muted)}
.ckhead.allok{color:var(--accent)}
.gate-list{list-style:none;margin:8px 0 0;padding:0}
.gate-list li{display:flex;align-items:center;flex-wrap:wrap;gap:4px 12px;padding:9px 10px;border-top:1px solid var(--line)}
.gate-list li:first-child{border-top:none}
li.g-no{background:color-mix(in srgb,var(--bad) 7%,transparent);border-radius:8px;border-top-color:transparent}
li.g-no+li{border-top-color:transparent}
.gmark{width:19px;height:19px;border-radius:50%;position:relative;flex:0 0 19px}
.g-ok .gmark{background:var(--accent)}
.g-ok .gmark::after{content:"";position:absolute;left:6px;top:3px;width:5px;height:9px;
  border:solid #fff;border-width:0 2px 2px 0;transform:rotate(43deg)}
.g-unk .gmark{box-shadow:inset 0 0 0 2px var(--muted)}
.g-unk .gmark::after{content:"?";position:absolute;left:50%;top:50%;
  transform:translate(-50%,-54%);color:var(--muted);font-size:.78rem;font-weight:800}
.g-unk .glabel{color:var(--muted)}
.g-no .gmark{box-shadow:inset 0 0 0 2px var(--bad)}
.g-no .gmark::before,.g-no .gmark::after{content:"";position:absolute;left:50%;top:50%;
  width:9px;height:2px;background:var(--bad)}
.g-no .gmark::before{transform:translate(-50%,-50%) rotate(45deg)}
.g-no .gmark::after{transform:translate(-50%,-50%) rotate(-45deg)}
.glabel{font-weight:700;font-size:.94rem}
.g-no .glabel{color:var(--bad)}
.gdetail{margin-left:auto;color:var(--muted);font-size:.82rem;text-align:right;max-width:60%}
/* ---- 変化 / 照合 ---- */
.chg{margin:4px 0 0;padding-left:2px;list-style:none}
.chg li{font-size:.92rem;margin:6px 0;padding-left:16px;position:relative}
.chg li::before{content:"";position:absolute;left:2px;top:.55em;width:6px;height:6px;
  background:var(--gold);transform:rotate(45deg)}
/* ---- 本文パネル群 ---- */
.tips ul{margin:8px 0 0;padding-left:20px}.tips li{margin:6px 0;line-height:1.6}
.why{font-size:.92rem;margin:4px 0 10px;max-width:46em}
.stagegrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:9px}
.mstage{border:1px solid var(--line);border-radius:10px;overflow:hidden;padding-bottom:9px;background:var(--bg)}
.mstage.on{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent),0 5px 16px rgba(47,143,122,.2)}
.mbar{height:6px}.mhd{padding:8px 10px 4px;font-weight:800;font-size:.9rem}
.mnow{display:inline-block;font-size:.62rem;background:var(--accent);color:#fff;border-radius:7px;
  padding:1px 7px;margin-left:5px;vertical-align:middle;letter-spacing:.06em}
.mrow{font-size:.72rem;line-height:1.45;padding:2px 10px}.mrow b{opacity:.55;font-weight:700}
.mcatch{font-size:.76rem;font-weight:700;padding:6px 10px 0;line-height:1.5}
.ostat{font-size:.92rem;margin-bottom:6px}
/* 見通しの定義リスト（キー: 値 を行で揃える） */
.ostats{margin:4px 0 8px;display:flex;flex-direction:column;gap:2px}
.okv{display:flex;gap:12px;align-items:baseline;padding:5px 0;border-top:1px solid var(--line);
  font-size:.92rem}
.okv:first-child{border-top:none}
.ok-k{flex:0 0 11em;color:var(--muted);font-size:.8rem;letter-spacing:.04em}
.ok-v{font-variant-numeric:tabular-nums}
.flow{display:grid;grid-template-columns:repeat(auto-fit,minmax(215px,1fr));gap:10px;margin-top:8px}
.fi{border:1px solid var(--line);border-radius:9px;padding:10px 12px;background:var(--bg);
  display:flex;flex-direction:column;gap:2px}
.fi-k{font-size:.76rem;color:var(--muted);letter-spacing:.03em}
.fi-v{font-size:.98rem;font-weight:700;font-variant-numeric:tabular-nums;line-height:1.4}
.fieldtrace{margin-top:10px;padding:9px 13px;border-left:3px solid var(--gold);background:var(--bg);
  border-radius:0 8px 8px 0;font-size:.85rem}
.alert{background:color-mix(in srgb,var(--bad) 11%,transparent);border:1px solid var(--bad);
  border-radius:9px;padding:10px 13px;font-weight:700}
.ok{background:color-mix(in srgb,var(--accent) 10%,transparent);border:1px solid var(--accent);
  border-radius:9px;padding:10px 13px}
.warnbar{background:color-mix(in srgb,var(--warn) 11%,transparent);border:1px solid var(--warn);
  border-radius:9px;padding:10px 13px;margin-top:10px;font-size:.9rem}
details.panel summary{cursor:pointer;font-size:1rem;list-style:none}
details.panel summary::-webkit-details-marker{display:none}
details.panel summary .ph{margin-bottom:0;display:inline-block}
details.panel summary::after{content:"開く";font-size:.7rem;color:var(--muted);float:right;
  border:1px solid var(--line);border-radius:999px;padding:2px 10px;margin-top:2px}
details[open].panel summary::after{content:"閉じる"}
details[open].panel summary{margin-bottom:6px}
.tablewrap{overflow-x:auto;margin-top:8px}
table.ind{border-collapse:collapse;width:100%;font-size:.82rem;min-width:560px;
  font-variant-numeric:tabular-nums}
table.ind th,table.ind td{border:1px solid var(--line);padding:7px 10px;text-align:left;vertical-align:top}
table.ind th{background:var(--bg);font-size:.76rem;letter-spacing:.06em}
.ik{font-weight:800;white-space:nowrap}
.cav li{font-size:.85rem;margin:4px 0}
table.recon{min-width:420px}
/* ---- 推移チャート ---- */
.spark-grid{stroke:var(--line);stroke-width:1}
.spark-area{fill:var(--accent);opacity:.13}
.spark-act{stroke:var(--accent);fill:none;stroke-width:2.5}
.spark-fc{stroke:var(--gold);fill:none;stroke-width:2.5;stroke-dasharray:5 4}
.spark-today{stroke:var(--muted);stroke-width:1;stroke-dasharray:3 3}
.spark-dot{fill:var(--accent)}
.foot{color:var(--muted);font-size:.78rem;margin-top:40px;padding-top:16px;
  border-top:1px solid var(--line);text-align:center;line-height:1.8}
@media(max-width:720px){
  .gdetail{flex-basis:100%;margin-left:31px;text-align:left;max-width:none}
  .ok-k{flex-basis:8.5em}
  .genstamp{margin-left:0}}
@media(prefers-reduced-motion:no-preference){details.panel summary{transition:opacity .15s}}
@media print{
  :root{--bg:#fff;--panel:#fff;--ink:#111;--muted:#444;--line:#bbb}
  .wrap{padding:0}.hero{box-shadow:none}
  .panel,.hero,.mstage,.fi{break-inside:avoid}
  details:not([open])>*{display:block!important}
}
</style>"""


def build_html(conn, run_date=None, full_document=False) -> str:
    """ページ生成。full_document=True で <!doctype html> 完全文書（GitHub Pages 等の
    直接配信用）、False で素のフラグメント（claude.ai Artifact が骨格を被せる用）。"""
    generated = dt.datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    # 呼び出し側(daily_update)が JST の run_date を渡す — CI(UTC) では date.today() が
    # JST より1日過去になり、鮮度窓・台帳・描画の日付が食い違うため必ず同一値で通す。
    run_date = run_date or dt.datetime.now(JST).date().isoformat()
    season = guide.season_note(run_date)
    season_bar = ""
    if season is not None:
        cls = "warnbar" if season["level"] == "warn" else "panel"
        season_bar = f'<div class="{cls}">{season["msg"]}</div>'

    verified, refs, as_ofs = [], [], []
    for reach_id in config.UI_REACHES:
        reach = config.REACHES[reach_id]
        # 1区間の失敗が全区間の公開HP生成を巻き込み日次更新を止めないよう、区間単位で隔離する。
        try:
            v = decision.reach_report(conn, reach_id, today=run_date)
            if v.as_of:
                as_ofs.append(v.as_of)
            # 前回比: 台帳の「run_date より前の最新」と今回を比較（今日の再実行は前回に数えない）。
            prev = calibration.latest_snapshot_before(conn, reach_id, run_date)
            delta = calibration.compute_delta(
                prev, calibration.snapshot_from_verdict(v, run_date))
            # 予実照合: 釣況源(semantic_source)のある区間のみ（源の無い区間は蓄積不能）。
            recon = (calibration.reconcile(conn, reach_id)
                     if reach.get("semantic_source") else None)
            html = render_reach(v, reach, run_date, delta=delta, recon=recon,
                                ref=reach.get("source_confidence") != "verified")
        except Exception as exc:  # noqa: BLE001 — 1区間の失敗を隔離（他区間と全体生成は続行）
            logger.warning("reach %s のパネル生成に失敗（スキップ）: %s", reach_id, exc)
            continue
        (verified if reach.get("source_confidence") == "verified" else refs).append(html)

    sections = list(verified)
    if refs:
        sections.append(
            '<div class="refhead"><h2 class="refh2">参考区間（物理データ + 注意書きに留めます）</h2>'
            '<p class="src">以下は公式データ源の実在確認が『参考』レベルの区間です。'
            '物理データ（気象・水位・水温・ダム放流）と季節の注意書きは正直に出しますが、'
            '確信を持った『行くべし』は出しません（システムが自動で様子見へ格下げ済み）。'
            '正確な期間・区間・遊漁ルールは各漁協にご確認ください。</p></div>')
        sections.extend(refs)

    page_asof = max(as_ofs) if as_ofs else None
    # Static snapshot: show the DATA date and the PAGE-GENERATION time separately so a
    # frozen page can't masquerade as "現在". No decaying "本日反映" badge here.
    page_fresh = guide.freshness(page_asof, run_date)
    datebar = (f'<div class="datebar"><span class="dcal">{guide.jp_date(page_asof, with_year=True)}</span>'
               '<span class="dnote">のデータ（毎日更新のスナップショット）</span>'
               f'<span class="fresh fresh-{page_fresh["level"]}">{page_fresh["label"]}</span>'
               f'<span class="genstamp">ページ生成 {generated}</span></div>')
    legend = ('<div class="legend">'
              '<span><i class="dot dot-go"></i>行くべし＝好条件</span>'
              '<span><i class="dot dot-caution"></i>様子見＝決め手なし</span>'
              '<span><i class="dot dot-nogo"></i>見送り＝増水/濁り/高水温</span>'
              '<span><i class="dot dot-temp"></i>水温＝判定の主軸（適水温＋C&R安全）</span></div>')
    head = ('<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta name="generated" content="{generated}+09:00">'
            '<meta name="description" content="群馬のニジマス釣り（神流川・利根川・吾妻川・渡良瀬川）の'
            '〈行くべし/様子見/見送り〉を、気象・水位・水温プロキシ・ダム放流・釣況の公開データだけで'
            '区間ごとに毎日推定する非公式レーダー。">'
            '<title>群馬 ニジマスレーダー</title>')
    content = ('<div class="wrap"><div class="inner">'
               '<header><div class="eyebrow">神流川・利根川・吾妻川・渡良瀬川 — 区間で読む冬鱒</div>'
               '<h1>群馬 ニジマスレーダー</h1>'
               '<p class="lead">群馬のニジマス（冬期キャッチ&リリース釣場・本流放流区間）の'
               '〈行くべし/様子見/見送り〉を、気象・水位・水温（気温からの未較正プロキシ）・'
               'ダム放流・釣況などの公開データだけから、<b>区間ごと</b>に毎日推定する非公式サイトです。'
               '同じ川でも自然流量の上流区間とダム下流区間は挙動が真逆のため、河川ではなく区間で判定します。'
               '判定の根拠も限界も、すべてそのまま公開します。</p>'
               '<p class="lead">精度と網羅の両立方針: 公式データ源を確認できた区間だけ確信を持った'
               '『行くべし』を出し、それ以外の<b>参考区間</b>は物理データと注意書きに留めます。'
               '水温はニジマス判定の主軸ですが、ライブ計測源が無いため気温からの'
               '<b>未較正プロキシ</b>です（現場報告があればそちらを優先）。</p></header>'
               + datebar + legend + season_bar
               + "".join(sections)
               + '<div class="foot">本サイトは個人が運営する非公式ページです。判定は公開データからの'
               '推定で、釣果や安全を保証するものではありません。水温は実測ではなく気温からの未較正プロキシです。'
               '増水・危険の確認は必ず公式の防災情報を、遊漁券・解禁期間・C&Rルールは各漁協の案内に'
               '従ってください。<br>'
               '出所: 気象庁AMeDAS/週間予報 ・ Yahoo!天気・災害（川の水位） ・ 釣況ブログ→Gemini ・ '
               '国土交通省 利根川ダム統合管理 ・ 各漁協の公式情報<br>'
               f'ページ生成: {generated}（JST）。データ基準日よりページ生成が大きく古い場合、'
               '自動更新が止まっている可能性があります。</div>'
               '</div></div>')
    if full_document:
        return ('<!doctype html><html lang="ja"><head>' + head + CSS + '</head><body>'
                + content + '</body></html>')
    return head + CSS + content


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else db.DB_PATH.parent / "niji_radar.html"
    db.init_db()
    conn = db.connect()
    try:
        out.write_text(build_html(conn), encoding="utf-8")
    finally:
        conn.close()
    logger.info("wrote static HTML: %s (%d bytes)", out, out.stat().st_size)


if __name__ == "__main__":
    main()
