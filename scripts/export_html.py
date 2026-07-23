"""現在のDB状態を、サーバ不要の自己完結HTML（無料公開用）に書き出す。

Streamlitライブ版と違い、これは「その時点のスナップショット」。GitHub Pages 等に置くか、
GitHub Action で日次再生成すれば無料の公開HPになる（APIキーはAction内でのみ使用、出力の
静的HTMLには残らない）。初心者が一目で〈行く/様子見/見送り〉と〈なぜ・どう釣るか〉を
掴めることを最優先に構成する。静的ファイルなので「データ基準日」と「ページ生成時刻」を
別々に明示し、鮮度を誤認させない。

判定単位は「区間(reach)」。同じ河川でも自然流量区間とダム下流区間は挙動が真逆のため、
河川ではなく区間で描く。source_confidence が verified の区間を先頭・強調し、参考区間は
「参考」バッジ付きで後段に並べる（物理データと注意書きは出すが確信 GO は出さない）。湖(止水)は
増水/濁り/ダムでなく表層水温(標高補正)×季節×深度戦略で描き分ける。

デザイン: 「深水×オーロラ」(dark-first glassmorphism)。被写体に根ざした深水ブルーグリーン基調 +
ニジマスの桃色側線を唯一の差し色に。フォントはシステムスタック、画像は使わずSVG/CSSのみで
自己完結（GitHub Pages・フォントCDN不使用）。ダーク/ライト両対応・prefers-reduced-motion 尊重。

  python -m scripts.export_html [出力パス]
"""
from __future__ import annotations

import datetime as dt
import logging
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.river_map import river_map_svg  # noqa: E402
from app.trout_art import trout_svg  # noqa: E402
from src import calibration, config, db, decision, guide  # noqa: E402

logger = logging.getLogger(__name__)

JST = dt.timezone(dt.timedelta(hours=9))
TAU = math.tau

LEVEL_JP = {"GO": "行くべし", "CAUTION": "様子見", "NO_GO": "見送り"}
WATER_WORD = {0: "クリア", 1: "笹濁り", 2: "泥濁り", None: "情報なし"}
# source_confidence → (表示語, CSSクラス)
SRC_CONF = {
    "verified": ("確度 高", "sc--ok"),
    "参考": ("参考", "sc--ref"),
    "未確認": ("未確認", "sc--unk"),
}
# C&Rリスク帯 → (表示語, CSSクラス)
CR_WORD = {
    "safe": ("安全", "cr-safe"),
    "caution": ("注意", "cr-caution"),
    "strong": ("強い注意", "cr-strong"),
    "nogo": ("見送り推奨", "cr-nogo"),
    "unknown": ("不明", "cr-unk"),
}
# 判定 → (ラベル, アイコン, CSSクラス)。色単独に頼らずアイコン+ラベル併記。
PILL = {"GO": ("行くべし", "●", "go"), "CAUTION": ("様子見", "◐", "warn"),
        "NO_GO": ("見送り", "×", "bad")}
# waterbody → (語, 絵文字)
WB = {"river": ("河川", "🎣"), "lake": ("湖", "🏞")}


# --------------------------------------------------------------------------- #
# 検証済みプリミティブ（proto_components.py 由来: dasharray 数式・glow・トークン）
# --------------------------------------------------------------------------- #
def _ring(tsi, level, uid, size=208, stroke=16):
    """発光TSIリングゲージ(0-100)。円周から arc 長を算出、中心に hero 数字。

    uid は SVG filter の一意 id に使う（1ページに多数のリングが載るため衝突回避）。
    """
    val = tsi or 0.0
    r = (size - stroke) / 2 - 6
    cx = cy = size / 2
    circ = TAU * r
    frac = max(0.0, min(1.0, val / 100.0))
    dash = circ * frac
    gap = circ - dash
    color = {"GO": "var(--go)", "CAUTION": "var(--warn)",
             "NO_GO": "var(--bad)"}.get(level, "var(--aqua)")
    ticks = ""
    for tv in (55, 68):                         # zone 目盛: 55好適 / 68絶好
        a = -TAU / 4 + TAU * (tv / 100.0)       # 12時起点・時計回り
        x1, y1 = cx + (r - stroke / 2) * math.cos(a), cy + (r - stroke / 2) * math.sin(a)
        x2, y2 = cx + (r + stroke / 2) * math.cos(a), cy + (r + stroke / 2) * math.sin(a)
        ticks += (f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                  f'stroke="var(--faint)" stroke-width="1.5"/>')
    numsz = size * 0.28
    lblsz = max(8.5, size * 0.068)
    return (
        f'<svg class="ring" viewBox="0 0 {size} {size}" width="{size}" height="{size}" '
        f'role="img" aria-label="適性メーター TSI {val:.0f}/100">'
        f'<defs><filter id="rg-{uid}" x="-45%" y="-45%" width="190%" height="190%">'
        f'<feGaussianBlur stdDeviation="{size * 0.02:.1f}" result="b"/>'
        f'<feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs>'
        f'<circle cx="{cx}" cy="{cy}" r="{r:.1f}" fill="none" stroke="var(--glass-brd)" '
        f'stroke-width="{stroke}"/>'
        f'<circle cx="{cx}" cy="{cy}" r="{r:.1f}" fill="none" stroke="{color}" '
        f'stroke-width="{stroke}" stroke-linecap="round" stroke-dasharray="{dash:.1f} {gap:.1f}" '
        f'transform="rotate(-90 {cx} {cy})" filter="url(#rg-{uid})" class="ring__arc"/>{ticks}'
        f'<text x="{cx}" y="{cy + numsz * 0.34:.1f}" text-anchor="middle" class="ring__num" '
        f'style="font-size:{numsz:.1f}px">{val:.0f}</text>'
        f'<text x="{cx}" y="{cy + size * 0.30:.1f}" text-anchor="middle" class="ring__lbl" '
        f'style="font-size:{lblsz:.1f}px">TSI / 100</text></svg>')


def _pill(level):
    label, icon, cls = PILL.get(level, ("—", "·", "muted"))
    return (f'<span class="pill pill--{cls}"><span class="pill__i" aria-hidden="true">{icon}</span>'
            f'{label}</span>')


def _wb_badge(waterbody):
    word, emoji = WB.get(waterbody, ("河川", "🎣"))
    return (f'<span class="wb wb--{waterbody}"><span aria-hidden="true">{emoji}</span>{word}</span>')


AURORA = ('<div class="aurora" aria-hidden="true"><span class="aurora__l aurora__l1"></span>'
          '<span class="aurora__l aurora__l2"></span><span class="aurora__l aurora__l3"></span></div>')


# --------------------------------------------------------------------------- #
# small formatters + source links (河川/湖の差分を吸収)
# --------------------------------------------------------------------------- #
def _fmt(x, unit=""):
    return "—" if x is None else (f"{x:.1f}{unit}" if isinstance(x, float) else f"{x}{unit}")


def _temp_txt(t):
    return "—" if t is None else f"{t:.0f}℃"


def _source_urls(v):
    """この区間/湖の一次情報リンク。河川は水位/ダム、湖は公式/釣況+気象のみ。"""
    reach = config.REACHES[v.reach_id]
    location = reach["location"]
    code = config.JMA_STATIONS[location]["code"]
    urls = {
        "気象・日照(AMeDAS)": config.JMA_AMEDAS_PAGE.format(code=code),
        "週間予報": config.JMA_FORECAST_PAGE,
        "漁協・公式情報": reach.get("official_url"),
        "釣況・釣果(外部)": reach.get("catch_ref_url"),
    }
    if v.waterbody == "river":
        river = reach["river"]
        water = config.RIVER_WATER_LEVEL.get(river, {})
        urls["水位(Yahoo川)"] = water.get("yahoo_url")
        has_dam_id = bool(config.reach_dams(v.reach_id))
        urls["ダム放流"] = ("https://www.ktr.mlit.go.jp/tonedamu/"
                         if (has_dam_id and river == "利根川") else None)
    return urls


# --------------------------------------------------------------------------- #
# charts: sparkline (実線=実績+面塗り / 破線=予報 / 終点=stripe glow) ・水温スケール
# --------------------------------------------------------------------------- #
def _poly(seg, cls):
    if len(seg) <= 1:
        return ""
    pstr = " ".join(f"{x:.1f},{y:.1f}" for x, y in seg)
    return f'<polyline points="{pstr}" fill="none" class="{cls}"/>'


def _sparkline(series, as_of):
    """TSI 推移。実績=aqua実線+面塗り、予報=aqua破線、終点=stripe glow。today分割線。"""
    if not series:
        return ""
    pts = series[-21:]
    w, h = 340, 88
    n = len(pts)
    xs = [w * i / (n - 1) if n > 1 else 0 for i in range(n)]
    ys = [h - 12 - (s.tsi / 100.0) * (h - 24) for s in pts]
    act = [(x, y) for x, y, s in zip(xs, ys, pts) if as_of is None or s.date <= as_of]
    fc = [(x, y) for x, y, s in zip(xs, ys, pts) if as_of is not None and s.date >= as_of]
    today_x = act[-1][0] if act else 0.0
    grid = "".join(
        f'<line class="spk-grid" x1="0" y1="{h - 12 - vv / 100.0 * (h - 24):.1f}" '
        f'x2="{w}" y2="{h - 12 - vv / 100.0 * (h - 24):.1f}"/>' for vv in (0, 50, 100))
    area = ""
    if len(act) > 1:
        pstr = " ".join(f"{x:.1f},{y:.1f}" for x, y in act)
        area = (f'<polygon class="spk-area" points="{act[0][0]:.1f},{h - 12} {pstr} '
                f'{act[-1][0]:.1f},{h - 12}"/>')
    end = fc[-1] if fc else (act[-1] if act else None)
    dot = (f'<circle class="spk-dot" cx="{end[0]:.1f}" cy="{end[1]:.1f}" r="3.6"/>'
           if end else "")
    return (f'<svg class="spark" viewBox="0 0 {w} {h}" width="100%" '
            f'aria-label="適性メーター(TSI)の推移">{grid}{area}'
            f'{_poly(act, "spk-act")}{_poly(fc, "spk-fc")}'
            f'<line class="spk-today" x1="{today_x:.1f}" y1="8" x2="{today_x:.1f}" '
            f'y2="{h - 8}"/>{dot}</svg>')


def _temp_scale(temp):
    """水温スケール 3→24℃ に C&R ゾーン帯 + 現在水温 glow マーカー。"""
    lo, hi = 3.0, 24.0
    safe = (16 - lo) / (hi - lo) * 100
    cau = (20 - lo) / (hi - lo) * 100
    if temp is None:
        marker = ""
    else:
        pos = max(0.0, min(1.0, (temp - lo) / (hi - lo))) * 100
        marker = (f'<span class="tscale__mk" style="left:{pos:.1f}%">'
                  f'<span class="tscale__dot"></span>'
                  f'<span class="tscale__val">{temp:.0f}℃</span></span>')
    return (
        '<div class="tscale" role="img" aria-label="水温スケール 3〜24℃・現在'
        + (f"{temp:.0f}℃" if temp is not None else "不明") + '">'
        '<div class="tscale__bar">'
        f'<span class="tscale__zone tz-safe" style="left:0;width:{safe:.1f}%"></span>'
        f'<span class="tscale__zone tz-cau" style="left:{safe:.1f}%;width:{cau - safe:.1f}%"></span>'
        f'<span class="tscale__zone tz-bad" style="left:{cau:.1f}%;width:{100 - cau:.1f}%"></span>'
        f'{marker}</div>'
        '<div class="tscale__lbls"><span>3℃</span><span>16℃〜配慮</span>'
        '<span>20℃〜危険</span><span>24℃</span></div></div>')


def _depth_layers(surf):
    """湖の深度レイヤー図。表層水温から狙う層を stripe で示す（推定・断定しない）。"""
    if surf is None:
        target = None
    elif surf <= 18:
        target = 0
    elif surf <= 20:
        target = 1
    else:
        target = 2
    bands = [("表層（0〜3m）", "浅場・ショアから", "#3f9fc4"),
             ("中層（躍層付近）", "レンジを刻む", "#2f9e86"),
             ("冷水層（深場）", "ボート/ディープ", "#1f5b6b")]
    bh = 46
    rows = []
    for i, (name, how, col) in enumerate(bands):
        y = 8 + i * (bh + 6)
        on = (i == target)
        stroke = ('stroke="var(--stripe)" stroke-width="3"' if on
                  else 'stroke="var(--glass-brd)" stroke-width="1"')
        tgt = (f'<text x="284" y="{y + 27}" text-anchor="end" class="dl-tgt">◀ 狙う層</text>'
               if on else "")
        rows.append(
            f'<g><rect x="8" y="{y}" width="284" height="{bh}" rx="9" fill="{col}" '
            f'fill-opacity="0.5" {stroke}/>'
            f'<text x="20" y="{y + 20}" class="dl-name">{name}</text>'
            f'<text x="20" y="{y + 37}" class="dl-how">{how}</text>{tgt}</g>')
    total = 8 + 3 * (bh + 6)
    return (f'<svg class="depth" viewBox="0 0 300 {total}" width="100%" role="img" '
            f'aria-label="湖の深度レイヤーと狙う層">{"".join(rows)}</svg>')


# --------------------------------------------------------------------------- #
# panels（データ取得と正直さは現行を維持。見た目のみ刷新）
# --------------------------------------------------------------------------- #
def _asof_line(v, run_date):
    fresh = guide.freshness(v.as_of, run_date)
    obs_times = [s.get("date_time") for s in (v.stations or {}).values() if s.get("date_time")]
    latest_obs = max(obs_times) if obs_times else None
    upd = ""
    if latest_obs and len(latest_obs) >= 16:
        if latest_obs[:10] != v.as_of:
            upd = f'・水位更新 {latest_obs[5:10].replace("-", "/")} {latest_obs[11:16]}'
        else:
            upd = f'・水位更新 {latest_obs[11:16]}'
    return (f'<div class="asof"><span class="asof__cal">{guide.jp_date(v.as_of, with_year=True)} の'
            f'データ</span><span class="fresh fresh--{fresh["level"]}">{fresh["label"]}</span>'
            f'<span class="src">{upd}</span></div>')


def _season_panel(v, run_date):
    s = guide.lake_season_note(run_date) if v.waterbody == "lake" else guide.season_note(run_date)
    if not s:
        return ""
    lvl = "warn" if s["level"] == "warn" else "info"
    return (f'<div class="season season--{lvl}"><span class="season__k">季節メモ</span>'
            f'<span>{s["msg"]}</span></div>')


def _watertemp_panel(v):
    """水温 & C&R パネル（魚種特化の目玉）。水温がニジマス判定の主軸であることを明示。"""
    obs = v.observed_water_temp
    temp = obs if obs is not None else v.water_temp_proxy
    if obs is not None:
        cf = (f"（抽出確信度 {int(v.observed_confidence * 100)}%）"
              if isinstance(v.observed_confidence, (int, float)) else "")
        temp_tag = '<span class="wt-tag wt-tag--obs">現場報告（ブログ記載）</span>'
        src_note = ("釣況ブログの記載からGeminiが読み取った水温です" + cf +
                    "。ブログ主の計測値か体感かは区別できないため、参考としてご覧ください")
    else:
        temp_tag = '<span class="wt-tag wt-tag--proxy">気温からの換算・未較正プロキシ</span>'
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
    return ('<div class="panel wt"><b class="panel__h">水温 と キャッチ&リリース — この判定の主軸</b>'
            '<div class="wt__grid">'
            f'<div class="wt__fig"><span class="wt__val">{_temp_txt(temp)}</span>'
            '<span class="wt__cap">推定水温</span></div>'
            f'<div class="wt__box">{temp_tag}{cr_chip}</div></div>'
            f'{_temp_scale(temp)}{msg_html}'
            '<div class="src">水温はニジマス（冷水魚）の活性・摂餌・C&R安全性をほぼ決める主軸です。'
            f'摂餌スイートスポットは10〜16℃、20℃超で摂餌停止・C&R危険。{src_note}。{tail}</div></div>')


def _checklist_panel(v):
    """GO ゲートをセグメントメーター + ✅/❌/？で可視化。build_verdict のゲートと SSOT。"""
    rows = decision.go_checklist(v)
    n_ok = sum(1 for r in rows if r["ok"])
    n_unk = sum(1 for r in rows if not r["ok"] and r.get("unknown"))
    n_fail = len(rows) - n_ok - n_unk
    segs = "".join('<span class="on"></span>' if i < n_ok else "<span></span>"
                   for i in range(len(rows)))
    lis = "".join(
        f'<li class="{"g-ok" if r["ok"] else "g-unk" if r.get("unknown") else "g-no"}">'
        f'<span class="gmark"></span><span class="glabel">{r["label"]}</span>'
        f'<span class="gdetail">{r["detail"]}</span></li>' for r in rows)
    if v.level == "GO":
        head = '<div class="gate__head allok">GO条件をすべて充足 — 『行くべし』</div>'
    else:
        parts = []
        if n_fail:
            parts.append(f"未達 {n_fail}件")
        if n_unk:
            parts.append(f"情報待ち {n_unk}件（？）")
        head = (f'<div class="gate__head">『行くべし』に届かない条件: {"・".join(parts)}'
                '。情報が揃わない項目がある間は GO を出しません</div>')
    return (f'<div class="panel"><b class="panel__h">なぜこの判定か — GO条件</b>'
            f'<div class="gate__top"><span class="gate__count">{n_ok}'
            f'<span class="gate__of">/{len(rows)}</span></span>'
            f'<div class="gate__seg">{segs}</div></div>{head}'
            f'<ul class="gate__list">{lis}</ul></div>')


def _tips_panel(v):
    if v.waterbody == "lake":
        note = guide.lake_depth_note(v.water_temp_proxy)
        return ('<div class="panel"><b class="panel__h">今日の狙い方（深度戦略）</b>'
                f'{_depth_layers(v.water_temp_proxy)}'
                f'<p class="tipline">{note}</p>'
                '<div class="src">湖は水深で水温・酸素が変わります。表層水温からの深度の目安で、'
                '断定ではありません。当日は表層→中層→深場と刻んで探ってください。</div></div>')
    wt = v.observed_water_temp if v.observed_water_temp is not None else v.water_temp_proxy
    tips = guide.fishing_tips(v.level, v.effective_quality, v.turbidity, v.water_trend,
                             v.methods, v.days_since_stock, water_temp=wt)
    lis = "".join(f"<li>{t}</li>" for t in tips)
    return ('<div class="panel"><b class="panel__h">今日の狙い方</b>'
            '<div class="src">条件から導いたルアー/フライ/エサの一般的な定石です。'
            '立ち位置と安全は、現地でご自身の目でご判断ください。</div>'
            f'<ul class="tips">{lis}</ul></div>')


def _why_panel(v):
    if v.waterbody == "lake":
        return ('<div class="panel"><b class="panel__h">なぜ深度で釣果が決まるのか（湖の因果）</b>'
                f'<p class="why">{guide.WHY_LAKE}</p></div>')
    cur = v.effective_quality
    cards = []
    for s in guide.TROUT_STAGES:
        on = " on" if s["state"] == cur else ""
        now = '<span class="stage__now">今ここ</span>' if s["state"] == cur else ""
        cards.append(
            f'<div class="stage{on}"><div class="stage__bar" style="background:{s["color"]}"></div>'
            f'<div class="stage__hd">{s["emoji"]} {s["state"]}{now}</div>'
            f'<div class="stage__row"><b>水温:</b> {s["temp"]}</div>'
            f'<div class="stage__row"><b>魚:</b> {s["fish"]}</div>'
            f'<div class="stage__how">{s["how"]}</div></div>')
    if cur in guide.STATE_SHORT:
        foot = f"いまは強調枠の『{cur}』（{guide.STATE_SHORT[cur]}）が今の期待値です。"
    else:
        foot = "いまの状態は情報が薄く未確定です。現地で水温・水色をご確認ください。"
    return ('<div class="panel"><b class="panel__h">水温とニジマスの関係 — なぜこれで釣果が決まるのか</b>'
            f'<p class="why">{guide.WHY_TROUT}</p>'
            f'<div class="stages">{"".join(cards)}</div>'
            f'<div class="src">{foot}</div></div>')


def _outlook_panel(v):
    o = v.outlook
    if not o:
        return ""
    ng = o.get("next_good")
    tref = "（参考区間・予報上の目安）" if v.source_confidence != "verified" else ""
    if ng:
        rel = f"・予報信頼度{ng['reliability']}" if ng.get("reliability") else ""
        head = (f'<div class="outlook__stat"><b>次に行くなら {guide.jp_date(ng["date"])} 頃</b>'
                f' — TSI{ng["tsi"]:.0f}・{ng["quality"]}{rel}{tref}</div>')
    else:
        head = ('<div class="outlook__stat">今後1週間は、予報上『行くべし』級の日が'
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
    return ('<div class="panel"><b class="panel__h">今後1週間の見通し（気象庁 週間予報ベース・'
            '予報は不確実）</b>'
            f'{head}<div class="outlook__stats">{rows}</div>{_sparkline(v.series, v.as_of)}'
            '<div class="src">実線＝実績 ／ 破線＝予報投影 ／ 終点の光点＝予報末端 ／ '
            '縦線＝今日（基準日）。予報信頼度は気象庁の A＞B＞C です。TSI は水温を主軸とした'
            '適性推定で、日ごとに独立に算出しています（蓄積状態は持ちません）。</div></div>')


def _delta_panel(delta):
    if delta is None:
        return ""
    if delta["changes"]:
        body = '<ul class="chg">' + "".join(f"<li>{c}</li>" for c in delta["changes"]) + "</ul>"
    else:
        body = ('<div class="src">大きな変化はありません'
                '（判定・適性メーター・水位・濁りとも前回と同等です）</div>')
    return (f'<div class="panel"><b class="panel__h">前回更新（{guide.jp_date(delta["prev_date"])}）'
            f'からの変化</b>{body}</div>')


def _recon_panel(recon):
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
    return ('<details class="panel subpanel"><summary><b class="panel__h">予実照合 — '
            'モデルはどれだけ当たっているか</b></summary>'
            '<p class="why">TSI（適性）は未較正の推定です。釣り人のブログ報告（体感コンディション）との'
            '一致率を毎日蓄積し、これを較正の根拠にします'
            '（運営者の釣果ではなく、公開の現場報告で検証する方針です）。</p>'
            + body +
            '<div class="src">⚠️ 泥濁り・増水報告はリセットとしてモデル入力（水位ステージ）にも'
            '使われるため、増水直後の「リセット一致」は独立の検証ではありません。体感コンディション'
            '観測そのものはモデルに入らないため、それ以外の照合は独立です。また、ブログの体感語彙は'
            '4段階で、モデルの「高水温危険」「増水リセット」を表現できないため、その日の照合は'
            '構造的に控えめに出ます。</div></details>')


def _flow_panel(v, urls):
    tsi = v.tsi or 0
    if v.waterbody == "lake":
        items = [
            ("日照", _fmt(v.sunshine_h, "h/日"), "気象庁AMeDAS", urls.get("気象・日照(AMeDAS)")),
            ("気温(観測点)", _fmt(v.air_temp, "℃"), "気象庁AMeDAS", urls.get("気象・日照(AMeDAS)")),
            ("表層水温(推定)", _temp_txt(v.water_temp_proxy), "気温+標高補正(未較正)", None),
            ("適性メーター(TSI)", f"{tsi:.0f}/100 ({v.model_quality or '—'})",
             "表層水温×日照から推定", None),
        ]
    else:
        sun_src = "気象庁AMeDAS(推計)" if v.sunshine_estimated else "気象庁AMeDAS(観測)"
        temp = v.observed_water_temp if v.observed_water_temp is not None else v.water_temp_proxy
        temp_src = "現場報告(ブログ)" if v.observed_water_temp is not None else "気温から換算(未較正)"
        obs_age = ""
        if v.observed_quality is not None or v.observed_catch is not None:
            obs_age = (f"（{v.staleness_days}日前）" if v.staleness_days is not None
                       else "（投稿日不明）")
        items = [
            ("日照", _fmt(v.sunshine_h, "h/日"), sun_src, urls.get("気象・日照(AMeDAS)")),
            ("気温", _fmt(v.air_temp, "℃"), "気象庁AMeDAS", urls.get("気象・日照(AMeDAS)")),
            ("水位", f"{v.water_status or '—'} {v.water_trend or ''}", "Yahoo川ミラー",
             urls.get("水位(Yahoo川)")),
            ("現場報告",
             f"{WATER_WORD.get(v.turbidity)}／{v.observed_quality or 'コンディション報告なし'}"
             f"／釣果{_fmt(v.observed_catch)}{obs_age}", "釣況/漁協",
             urls.get("釣況・釣果(外部)") or urls.get("漁協・公式情報")),
            ("水温", _temp_txt(temp), temp_src, None),
            ("適性メーター(TSI)", f"{tsi:.0f}/100 ({v.model_quality or '—'})",
             "水温×濁り×日照から推定", None),
        ]
    flow = "".join(
        f'<div class="fi"><span class="fi-k">{t}</span><span class="fi-v">{val}</span>'
        '<span class="src">元: '
        + (f'<a href="{u}" target="_blank" rel="noopener">{s} ↗</a>' if u else s) + "</span></div>"
        for t, val, s, u in items)
    trace = ""
    if v.observed_excerpt:
        conf = (f"／抽出確信度(LLM自己申告) {int(v.observed_confidence * 100)}%"
                if isinstance(v.observed_confidence, (int, float)) else "")
        trace = ('<div class="trace">現場報告の根拠（ブログ引用）：'
                 f'「{v.observed_excerpt}」{conf}</div>')
    return ('<div class="panel"><b class="panel__h">この判定の作り方'
            '（何を元に何を出したか・出所はクリックで開く）</b>'
            f'<div class="flow">{flow}</div>{trace}'
            '<div class="src">公開データ（生）→ 導出（水温/TSI）→ 判定、の順で組み立てます。'
            '優先順位は 危険（増水・泥濁り・ダム）→ 魚の生存（C&R水温）→ 現場報告 → TSI の順です。'
            '</div></div>')


def _map_panel(v):
    reach = config.REACHES[v.reach_id]
    river = reach["river"]
    cfg = config.RIVER_WATER_LEVEL.get(river, {})
    stations = cfg.get("stations", [])
    points = []
    for name in stations:
        stt = v.stations.get(name, {})
        status = stt.get("water_level_status")
        is_reach = name == reach["water_station"]
        points.append({"name": name, "status": status, "trend": stt.get("water_trend"),
                       "sev": config.LEVEL_SEVERITY.get(status or "", 0), "mark": is_reach,
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
    return ('<div class="panel"><b class="panel__h">川マップ（各観測点の増水状況）</b>'
            f'<div class="mapwrap">{rmap}</div>{note}'
            '<div class="src">⚠️ 気象・水温は観測点が1つのため区間内で共通です。'
            '観測点ごとに違うのは水位のみで、🎣（本区間）が判定の代表観測点です。</div></div>')


def _dam_panel(v):
    dr = v.dam_risk
    if dr is None:
        return ('<div class="panel"><b class="panel__h">上流ダム放流（濁りの前兆）</b>'
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
    return ('<div class="panel"><b class="panel__h">上流ダム放流（濁りの前兆）</b>' + body
            + '<div class="src">ダム下流の区間では、上流ダムの放流増を濁り水の前兆として監視します。'
            '到達時間は各ダムの距離と流速の仮定から出した粗い目安です（実測ではありません。水位の波の'
            '到達時間で、濁り本体はさらに遅れて届きます）。</div></div>')


def _indicator_panel():
    rows = "".join(
        f'<tr><td class="ik">{g["key"]}</td><td>{g["what"]}</td>'
        f'<td>{g["high_low"]}</td><td class="src">{g["source"]}</td></tr>'
        for g in guide.INDICATOR_GUIDE)
    return ('<details class="panel subpanel"><summary><b class="panel__h">指標の読み方'
            '（高い/低いとどうなる）</b></summary>'
            '<div class="tablewrap"><table class="ind"><thead><tr><th>指標</th><th>何の値？</th>'
            f'<th>高い/低いとどうなる</th><th>何を元に</th></tr></thead><tbody>{rows}</tbody></table>'
            '</div></details>')


def _sources_panel(v, urls):
    src_links = " ・ ".join(f'<a href="{u}" target="_blank" rel="noopener">{k} ↗</a>'
                           for k, u in urls.items() if u)
    caveats = "".join(f"<li>{c}</li>" for c in v.caveats)
    return ('<details class="panel subpanel"><summary><b class="panel__h">情報源と'
            '『信じすぎない』注意書き</b></summary>'
            f'<p class="src2"><b>情報源:</b> {src_links}</p>'
            '<p class="src2"><b>この判定を過信しないための注意点:</b></p>'
            f'<ul class="cav">{caveats}</ul></details>')


# --------------------------------------------------------------------------- #
# card / hero assembly
# --------------------------------------------------------------------------- #
def _detail_panels(v, delta, recon, urls, run_date):
    """河川/湖で描き分ける展開パネル群。河川=川マップ/濁り/ダム、湖=深度戦略/湖因果/湖季節。"""
    parts = [
        _asof_line(v, run_date),
        _season_panel(v, run_date),
        _delta_panel(delta),
        _watertemp_panel(v),
        _checklist_panel(v),
        _tips_panel(v),
        _why_panel(v),
        _outlook_panel(v),
    ]
    if v.waterbody == "river":
        parts += [_dam_panel(v), _map_panel(v), _indicator_panel()]
    parts += [_flow_panel(v, urls), _recon_panel(recon), _sources_panel(v, urls)]
    return "".join(p for p in parts if p)


def _next_good_line(v):
    """最も行動につながる出力（次に狙える日）。参考区間は『予報上の目安』と弱める。"""
    o = v.outlook
    if not o:
        return ""
    ng = o.get("next_good")
    tentative = v.source_confidence != "verified"
    if ng:
        rel = f"・予報信頼度{ng['reliability']}" if ng.get("reliability") else ""
        note = "（参考・予報上の目安）" if tentative else ""
        cls = "nextgood nextgood--tentative" if tentative else "nextgood"
        return (f'<div class="{cls}"><span class="nextgood__k">次に行くなら</span>'
                f'<span class="nextgood__d">{guide.jp_date(ng["date"])} 頃</span>'
                f'<span class="src">TSI{ng["tsi"]:.0f}・{ng["quality"]}{rel}{note}</span></div>')
    return ('<div class="nextgood nextgood--none"><span class="nextgood__k">次に行くなら</span>'
            '<span class="nextgood__d nextgood__d--none">今週は好機が見当たりません</span></div>')


def _facts(v):
    temp = v.observed_water_temp if v.observed_water_temp is not None else v.water_temp_proxy
    cr_word, cr_cls = CR_WORD.get(v.cr_risk, ("不明", "cr-unk"))
    cr_label = "C&R" if v.catch_release else "水温"
    return (f'<span class="fact"><span class="fact__k">推定水温</span>'
            f'<span class="fact__v">{_temp_txt(temp)}</span></span>'
            f'<span class="fact"><span class="fact__k">{cr_label}</span>'
            f'<span class="cr-chip {cr_cls}">{cr_word}</span></span>')


def _hero_stats(v):
    temp = v.observed_water_temp if v.observed_water_temp is not None else v.water_temp_proxy
    cr_word, cr_cls = CR_WORD.get(v.cr_risk, ("不明", "cr-unk"))
    cr_label = "C&Rリスク" if v.catch_release else "水温の目安"
    ng = (v.outlook or {}).get("next_good") if v.outlook else None
    ng_val = guide.jp_date(ng["date"]) if ng else "見当たらず"
    ng_sub = "予報上の目安" if v.source_confidence != "verified" else "週間予報ベース"
    sc_word = SRC_CONF.get(v.source_confidence, (v.source_confidence, ""))[0]
    tiles = [
        ("推定水温", _temp_txt(temp), "判定の主軸"),
        (cr_label, f'<span class="cr-chip {cr_cls}">{cr_word}</span>',
         "水温連動の保全ゲート" if v.catch_release else "活性の目安"),
        ("次に行くなら", ng_val, ng_sub),
        ("データ源", sc_word, f"信頼度 {int(v.confidence * 100)}%"),
    ]
    return "".join(f'<div class="stat"><span class="stat__k">{k}</span>'
                   f'<span class="stat__v">{val}</span>'
                   f'<span class="stat__s">{s}</span></div>' for k, val, s in tiles)


def _ref_note(waterbody):
    kind = "湖" if waterbody == "lake" else "区間"
    return ('<div class="warnbar">この' + kind + 'は公式データ源の実在確認が『参考』レベルです。'
            '物理データ（気象・水位・水温・季節）と注意書きは出しますが、確信を持った『行くべし』は'
            '出していません（自動で様子見へ格下げ済み）。正確な期間・区間・ルールは各漁協・現地で'
            'ご確認ください。</div>')


def _hero(item, run_date):
    v, reach = item["v"], item["reach"]
    if v.as_of is None:
        return ""
    urls = _source_urls(v)
    sc_word, sc_cls = SRC_CONF.get(v.source_confidence, (v.source_confidence, "sc--unk"))
    ref_note = "" if v.source_confidence == "verified" else _ref_note(v.waterbody)
    panels = _detail_panels(v, item["delta"], item["recon"], urls, run_date)
    return (
        '<section class="herowrap"><details class="hero" open><summary class="hero__head">'
        '<div class="hero__grid">'
        f'<div class="hero__art">{trout_svg(v.mood)}</div>'
        f'<div class="hero__ring">{_ring(v.tsi, v.level, v.reach_id + "-h", size=208, stroke=16)}</div>'
        '<div class="hero__main">'
        f'<div class="hero__badges">{_wb_badge(v.waterbody)}<span class="sc {sc_cls}">{sc_word}</span>'
        f'<span class="hero__conf">信頼度 {int(v.confidence * 100)}%</span></div>'
        f'<h2 class="hero__label">{reach["label"]}</h2>'
        f'<div class="hero__pill">{_pill(v.level)}</div>'
        f'<p class="hero__one">{guide.VERDICT_ONELINER[v.level]}</p>'
        f'<p class="hero__headline">{v.headline}</p>'
        f'<div class="hero__stats">{_hero_stats(v)}</div>'
        '</div></div><span class="card__toggle hero__toggle"></span></summary>'
        f'<div class="card__panels">{ref_note}{panels}</div></details></section>')


def _card(item, run_date):
    v, reach = item["v"], item["reach"]
    if v.as_of is None:
        return ('<details class="card"><summary class="card__head"><div class="card__row">'
                f'<div class="card__ident">{_wb_badge(v.waterbody)}'
                f'<h3 class="card__title">{reach["label"]}</h3></div>'
                '<div class="warnbar">この区間はまだ実データがありません（収集待ち）。'
                '判定は表示しません。</div></div></summary></details>')
    urls = _source_urls(v)
    verified = v.source_confidence == "verified"
    cls = "card card--verified" if verified else "card card--ref"
    sc_word, sc_cls = SRC_CONF.get(v.source_confidence, (v.source_confidence, "sc--unk"))
    ref_note = "" if verified else _ref_note(v.waterbody)
    panels = _detail_panels(v, item["delta"], item["recon"], urls, run_date)
    return (
        f'<details class="{cls}"><summary class="card__head"><div class="card__row">'
        f'<div class="card__ident">{_wb_badge(v.waterbody)}'
        f'<span class="sc {sc_cls}">{sc_word}</span>'
        f'<h3 class="card__title">{reach["label"]}</h3></div>'
        '<div class="card__sum">'
        f'<div class="card__ring">{_ring(v.tsi, v.level, v.reach_id + "-m", size=118, stroke=11)}</div>'
        f'<div class="card__meta">{_pill(v.level)}'
        f'<p class="card__hl">{v.headline}</p>'
        f'<div class="card__facts">{_facts(v)}</div>'
        f'<div class="card__method">{guide.method_label(v.methods, v.catch_release)}</div>'
        '</div></div>'
        f'{_next_good_line(v)}'
        '<span class="card__toggle"></span></div></summary>'
        f'<div class="card__panels">{ref_note}{panels}</div></details>')


# --------------------------------------------------------------------------- #
# CSS — 「深水×オーロラ」dark-first glassmorphism（自己完結・ダーク/ライト両対応）
# --------------------------------------------------------------------------- #
CSS = """<style>
/* ---- color tokens: 深水ブルーグリーン + ニジマス桃色側線の一点豪華 ---- */
:root{
 --abyss:#081018;--deep:#0c1a26;--deep2:#102331;
 --glass:rgba(18,38,52,.55);--glass-brd:rgba(120,200,210,.14);
 --ink:#e6f2f0;--muted:#8fb0b2;--faint:rgba(143,176,178,.5);
 --aqua:#35d0c0;--aqua-deep:#1f8f88;--stripe:#ff8fa3;
 --go:#33d69f;--warn:#f5b942;--bad:#ff6b6b;
 --aur1:#1f8f88;--aur2:#2ec5a8;--aur3:#ff8fa3;--aur-op:.5;
 --mono:ui-monospace,"SF Mono","Menlo",monospace;
 --sans:"SF Pro Display","Hiragino Sans","Yu Gothic","Noto Sans JP",system-ui,sans-serif}
@media(prefers-color-scheme:light){:root{
 --abyss:#eef6f4;--deep:#ffffff;--deep2:#f2f9f7;
 --glass:rgba(255,255,255,.72);--glass-brd:rgba(14,143,130,.18);
 --ink:#0c2430;--muted:#4a6b6d;--faint:rgba(74,107,109,.42);
 --aqua:#0e8f82;--aqua-deep:#0b6f65;--stripe:#e85d78;
 --go:#0f9d6c;--warn:#c07a1e;--bad:#d94436;--aur-op:.24}}
:root[data-theme="dark"]{
 --abyss:#081018;--deep:#0c1a26;--deep2:#102331;
 --glass:rgba(18,38,52,.55);--glass-brd:rgba(120,200,210,.14);
 --ink:#e6f2f0;--muted:#8fb0b2;--faint:rgba(143,176,178,.5);
 --aqua:#35d0c0;--aqua-deep:#1f8f88;--stripe:#ff8fa3;
 --go:#33d69f;--warn:#f5b942;--bad:#ff6b6b;--aur-op:.5}
:root[data-theme="light"]{
 --abyss:#eef6f4;--deep:#ffffff;--deep2:#f2f9f7;
 --glass:rgba(255,255,255,.72);--glass-brd:rgba(14,143,130,.18);
 --ink:#0c2430;--muted:#4a6b6d;--faint:rgba(74,107,109,.42);
 --aqua:#0e8f82;--aqua-deep:#0b6f65;--stripe:#e85d78;
 --go:#0f9d6c;--warn:#c07a1e;--bad:#d94436;--aur-op:.24}
/* ---- base ---- */
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--abyss);color:var(--ink);font-family:var(--sans);
 line-height:1.62;-webkit-font-smoothing:antialiased;font-feature-settings:"palt";
 overflow-x:hidden}
a{color:var(--aqua);text-underline-offset:3px;text-decoration-thickness:1px;
 text-decoration-color:color-mix(in srgb,var(--aqua) 55%,transparent);transition:color .15s}
a:hover{text-decoration-color:var(--aqua)}
:focus-visible{outline:2px solid var(--aqua);outline-offset:2px;border-radius:8px}
b{font-weight:700}
/* ---- ambient aurora ---- */
.aurora{position:fixed;inset:0;overflow:hidden;pointer-events:none;z-index:0}
.aurora__l{position:absolute;border-radius:50%;filter:blur(72px);opacity:var(--aur-op);
 mix-blend-mode:screen}
.aurora__l1{width:62vw;height:62vw;left:-12vw;top:-16vw;
 background:radial-gradient(circle,var(--aur1),transparent 60%);animation:drift1 34s ease-in-out infinite}
.aurora__l2{width:52vw;height:52vw;right:-10vw;top:8vh;
 background:radial-gradient(circle,var(--aur2),transparent 60%);animation:drift2 40s ease-in-out infinite}
.aurora__l3{width:40vw;height:40vw;left:28vw;bottom:-14vw;opacity:calc(var(--aur-op) * .56);
 background:radial-gradient(circle,var(--aur3),transparent 62%);animation:drift3 46s ease-in-out infinite}
@keyframes drift1{50%{transform:translate(8vw,6vh) scale(1.1)}}
@keyframes drift2{50%{transform:translate(-6vw,-4vh) scale(1.08)}}
@keyframes drift3{50%{transform:translate(4vw,-6vh) scale(1.12)}}
/* ---- layout ---- */
.wrap{position:relative;z-index:1;max-width:1120px;margin:0 auto;padding:30px 18px 90px}
.masthead{margin-bottom:6px}
.eyebrow{font-family:var(--mono);font-size:.72rem;letter-spacing:.22em;text-transform:uppercase;
 color:var(--muted);margin-bottom:10px}
.masthead h1{font-size:clamp(1.9rem,5vw,2.7rem);font-weight:800;letter-spacing:-.02em;margin:0;
 text-wrap:balance;background:linear-gradient(120deg,var(--ink),var(--aqua));
 -webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent}
.lead{color:var(--muted);font-size:.9rem;margin:.7em 0 0;max-width:52em}
.lead b{color:var(--ink)}
/* ---- date bar / freshness / genstamp ---- */
.datebar{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 14px;margin:22px 0 6px;
 padding:14px 20px;border:1px solid var(--glass-brd);border-radius:16px;background:var(--glass);
 -webkit-backdrop-filter:blur(14px);backdrop-filter:blur(14px)}
.datebar__cal{font-size:1.4rem;font-weight:800;letter-spacing:-.01em;font-variant-numeric:tabular-nums}
.datebar__note{font-size:.86rem;color:var(--muted)}
.genstamp{margin-left:auto;font-family:var(--mono);font-size:.68rem;color:var(--muted);
 border:1px solid var(--glass-brd);border-radius:999px;padding:3px 11px;font-variant-numeric:tabular-nums}
.fresh{font-family:var(--mono);font-size:.66rem;letter-spacing:.04em;border-radius:999px;
 padding:3px 10px;font-weight:700}
.fresh--ok{background:color-mix(in srgb,var(--go) 18%,transparent);color:var(--go)}
.fresh--warn{background:color-mix(in srgb,var(--warn) 20%,transparent);color:var(--warn)}
/* ---- legend ---- */
.legend{display:flex;flex-wrap:wrap;gap:8px;margin:14px 0 4px;font-size:.76rem}
.legend span{border:1px solid var(--glass-brd);border-radius:999px;padding:4px 12px;
 background:var(--glass);display:inline-flex;align-items:center;gap:7px}
.dot{width:9px;height:9px;border-radius:50%;display:inline-block;flex:0 0 9px}
.dot--go{background:var(--go)}.dot--warn{background:var(--warn)}.dot--bad{background:var(--bad)}
.dot--temp{background:var(--stripe)}
/* ---- glass surface + reusable badges ---- */
.hero,.card,.panel,.datebar{background:var(--glass);
 -webkit-backdrop-filter:blur(16px);backdrop-filter:blur(16px)}
.pill{display:inline-flex;align-items:center;gap:6px;padding:6px 14px;border-radius:999px;
 font-weight:800;font-size:.92rem;letter-spacing:.02em}
.pill__i{font-size:.82em}
.pill--go{background:color-mix(in srgb,var(--go) 20%,transparent);color:var(--go);
 box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--go) 40%,transparent)}
.pill--warn{background:color-mix(in srgb,var(--warn) 20%,transparent);color:var(--warn);
 box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--warn) 40%,transparent)}
.pill--bad{background:color-mix(in srgb,var(--bad) 20%,transparent);color:var(--bad);
 box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--bad) 40%,transparent)}
.pill--muted{background:color-mix(in srgb,var(--muted) 18%,transparent);color:var(--muted)}
.wb{display:inline-flex;align-items:center;gap:5px;font-size:.72rem;font-weight:700;
 border-radius:999px;padding:3px 10px;border:1px solid var(--glass-brd)}
.wb--river{color:var(--aqua)}.wb--lake{color:var(--aur2,#2ec5a8)}
.sc{font-family:var(--mono);font-size:.64rem;letter-spacing:.06em;font-weight:700;
 border-radius:999px;padding:3px 9px}
.sc--ok{background:color-mix(in srgb,var(--aqua) 22%,transparent);color:var(--aqua)}
.sc--ref{background:color-mix(in srgb,var(--warn) 20%,transparent);color:var(--warn)}
.sc--unk{background:color-mix(in srgb,var(--muted) 20%,transparent);color:var(--muted)}
/* ---- TSI ring text ---- */
.ring{display:block}
.ring__num{font-family:var(--mono);font-weight:800;fill:var(--ink);font-variant-numeric:tabular-nums}
.ring__lbl{font-family:var(--mono);font-weight:600;fill:var(--muted);letter-spacing:.12em;
 text-transform:uppercase}
.ring__arc{transition:stroke-dasharray 1s cubic-bezier(.2,.8,.2,1)}
/* ---- hero ---- */
.herowrap{margin-top:20px}
.hero{border:1px solid var(--glass-brd);border-radius:24px;overflow:hidden;
 box-shadow:0 20px 60px rgba(0,0,0,.28)}
.hero__head{cursor:pointer;list-style:none;display:block;padding:26px 26px 22px;position:relative}
.hero__head::-webkit-details-marker{display:none}
.hero__grid{display:grid;grid-template-columns:170px auto 1fr;align-items:center;gap:20px 26px}
.hero__art{width:150px;height:92px;filter:drop-shadow(0 6px 14px rgba(0,0,0,.3))}
.hero__ring{justify-self:center}
.hero__main{min-width:0}
.hero__badges{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin-bottom:6px}
.hero__conf{font-family:var(--mono);font-size:.66rem;color:var(--muted);
 border:1px solid var(--glass-brd);border-radius:999px;padding:3px 10px}
.hero__label{font-size:clamp(1.3rem,3vw,1.7rem);font-weight:800;letter-spacing:-.01em;
 margin:.1em 0 .5em;text-wrap:balance}
.hero__pill{margin-bottom:8px}
.hero__one{font-weight:700;font-size:1rem;margin:.2em 0}
.hero__headline{color:var(--muted);font-size:.92rem;margin:.2em 0 0;max-width:44em}
.hero__stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(128px,1fr));gap:10px;
 margin-top:16px}
.stat{border:1px solid var(--glass-brd);border-radius:13px;padding:11px 13px;
 background:color-mix(in srgb,var(--deep2) 40%,transparent)}
.stat__k{display:block;font-family:var(--mono);font-size:.64rem;letter-spacing:.08em;
 text-transform:uppercase;color:var(--muted)}
.stat__v{display:block;font-size:1.16rem;font-weight:800;font-variant-numeric:tabular-nums;
 margin:3px 0 1px;line-height:1.15}
.stat__s{display:block;font-size:.68rem;color:var(--muted)}
.hero__toggle,.card__toggle{display:block;text-align:right;margin-top:14px}
/* ---- reach grid ---- */
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,342px),1fr));
 gap:16px;margin-top:24px;align-items:start}
.refhead{grid-column:1/-1;margin-top:14px;padding-top:12px}
.refhead h2{font-size:1.15rem;font-weight:800;letter-spacing:-.01em;margin:0 0 6px;
 color:var(--warn);border-top:2px dashed var(--glass-brd);padding-top:18px}
.card{border:1px solid var(--glass-brd);border-radius:20px;overflow:hidden;
 transition:transform .2s,box-shadow .2s,border-color .2s}
.card--verified{border-color:color-mix(in srgb,var(--aqua) 42%,var(--glass-brd));
 box-shadow:0 0 0 1px color-mix(in srgb,var(--aqua) 20%,transparent)}
.card:hover{transform:translateY(-3px);
 box-shadow:0 14px 44px rgba(0,0,0,.34),0 0 0 1px color-mix(in srgb,var(--aqua) 30%,transparent)}
.card__head{cursor:pointer;list-style:none;display:block;padding:18px 18px 16px;position:relative}
.card__head::-webkit-details-marker{display:none}
.card__row{display:block}
.card__ident{display:flex;flex-wrap:wrap;align-items:center;gap:7px;margin-bottom:12px}
.card__title{font-size:1.02rem;font-weight:800;letter-spacing:-.01em;margin:0;flex-basis:100%}
.card__sum{display:flex;align-items:center;gap:16px}
.card__ring{flex:0 0 auto}
.card__meta{min-width:0;flex:1}
.card__hl{color:var(--muted);font-size:.84rem;margin:8px 0 10px;line-height:1.5}
.card__facts{display:flex;flex-wrap:wrap;gap:8px 14px;align-items:center}
.fact{display:inline-flex;align-items:baseline;gap:6px}
.fact__k{font-family:var(--mono);font-size:.62rem;letter-spacing:.06em;text-transform:uppercase;
 color:var(--muted)}
.fact__v{font-size:1.02rem;font-weight:800;font-variant-numeric:tabular-nums}
.card__method{font-size:.74rem;color:var(--muted);margin-top:10px;letter-spacing:.01em}
.card__toggle{padding-bottom:0}
.card__toggle::after,.hero__toggle::after{content:"詳細を開く ▾";font-family:var(--mono);
 font-size:.66rem;letter-spacing:.06em;color:var(--aqua);
 border:1px solid color-mix(in srgb,var(--aqua) 40%,transparent);border-radius:999px;padding:3px 11px}
details[open]>.card__head .card__toggle::after,
details[open]>.hero__head .hero__toggle::after{content:"閉じる ▴";color:var(--muted);
 border-color:var(--glass-brd)}
.card__next{margin-top:12px}
.card__panels{padding:0 18px 18px}
.hero>.card__panels{padding:0 26px 24px}
/* ---- next good ---- */
.nextgood{display:flex;flex-wrap:wrap;align-items:baseline;gap:5px 10px;margin-top:14px;
 padding:11px 14px;border-radius:12px;
 background:color-mix(in srgb,var(--aqua) 8%,transparent);border:1px solid var(--glass-brd)}
.nextgood__k{font-family:var(--mono);font-size:.64rem;letter-spacing:.1em;text-transform:uppercase;
 color:var(--muted)}
.nextgood__d{font-size:1.12rem;font-weight:800;color:var(--aqua)}
.nextgood--tentative .nextgood__d{color:var(--muted);text-decoration:underline dotted;font-weight:700}
.nextgood--none .nextgood__d,.nextgood__d--none{color:var(--muted);font-size:.94rem;font-weight:600}
.nextgood .src{flex-basis:100%}
/* ---- panels ---- */
.panel{border:1px solid var(--glass-brd);border-radius:15px;padding:16px 18px;margin-top:14px}
.panel__h{display:block;font-weight:800;font-size:1rem;letter-spacing:-.005em;margin-bottom:9px}
.panel__h::before{content:"";display:inline-block;width:7px;height:7px;background:var(--stripe);
 transform:rotate(45deg);margin-right:9px;vertical-align:2px;
 box-shadow:0 0 8px color-mix(in srgb,var(--stripe) 70%,transparent)}
.src{font-size:.76rem;color:var(--muted);line-height:1.62}
.src2{font-size:.85rem;margin:6px 0}
.why{font-size:.9rem;margin:4px 0 12px;max-width:52em}
.tipline{font-size:.92rem;margin:10px 0 4px}
/* ---- season ---- */
.season{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 12px;margin-top:14px;
 padding:12px 16px;border-radius:13px;border:1px solid var(--glass-brd);font-size:.88rem}
.season__k{font-family:var(--mono);font-size:.64rem;letter-spacing:.1em;text-transform:uppercase;
 color:var(--muted);flex:0 0 auto}
.season--info{background:color-mix(in srgb,var(--aqua) 8%,transparent)}
.season--warn{background:color-mix(in srgb,var(--warn) 12%,transparent);border-color:var(--warn)}
/* ---- asof ---- */
.asof{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 10px;margin-top:14px}
.asof__cal{font-weight:700;font-size:1rem}
/* ---- water temp + C&R ---- */
.wt{background:color-mix(in srgb,var(--aqua) 6%,var(--glass))}
.wt__grid{display:flex;flex-wrap:wrap;align-items:center;gap:14px 24px;margin:6px 0 12px}
.wt__fig{display:flex;flex-direction:column;align-items:center;min-width:104px}
.wt__val{font-size:2.1rem;font-weight:800;line-height:1;font-variant-numeric:tabular-nums}
.wt__cap{font-family:var(--mono);font-size:.64rem;letter-spacing:.08em;text-transform:uppercase;
 color:var(--muted);margin-top:4px}
.wt__box{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.wt-tag{font-size:.72rem;border:1px solid var(--glass-brd);border-radius:999px;padding:4px 11px;
 color:var(--muted)}
.wt-tag--obs{color:var(--aqua);border-color:color-mix(in srgb,var(--aqua) 45%,transparent)}
.cr-chip{font-size:.78rem;font-weight:800;border-radius:999px;padding:4px 12px;color:#04120f}
.cr-safe{background:var(--go)}.cr-caution{background:var(--warn)}
.cr-strong{background:#e88a4a;color:#1a0d04}.cr-nogo{background:var(--bad);color:#180404}
.cr-unk{background:var(--muted)}
.wt-msg{margin-top:8px;padding:11px 14px;border-radius:11px;font-weight:700;font-size:.9rem;
 border:1px solid var(--glass-brd)}
.wt-msg.cr-caution{background:color-mix(in srgb,var(--warn) 14%,transparent);border-color:var(--warn);
 color:var(--ink)}
.wt-msg.cr-strong{background:color-mix(in srgb,var(--bad) 10%,transparent);color:var(--ink)}
.wt-msg.cr-nogo{background:color-mix(in srgb,var(--bad) 16%,transparent);border-color:var(--bad);
 color:var(--ink)}
/* temp scale */
.tscale{margin:10px 0 4px}
.tscale__bar{position:relative;height:14px;border-radius:8px;overflow:visible;
 background:var(--glass-brd)}
.tscale__zone{position:absolute;top:0;bottom:0;opacity:.9}
.tscale__zone:first-of-type{border-radius:8px 0 0 8px}
.tscale__zone:last-of-type{border-radius:0 8px 8px 0}
.tz-safe{background:color-mix(in srgb,var(--go) 55%,transparent)}
.tz-cau{background:color-mix(in srgb,var(--warn) 55%,transparent)}
.tz-bad{background:color-mix(in srgb,var(--bad) 55%,transparent)}
.tscale__mk{position:absolute;top:50%;transform:translate(-50%,-50%);display:flex;
 flex-direction:column;align-items:center}
.tscale__dot{width:14px;height:14px;border-radius:50%;background:var(--stripe);
 box-shadow:0 0 0 3px var(--abyss),0 0 12px var(--stripe)}
.tscale__val{font-family:var(--mono);font-size:.66rem;font-weight:800;margin-top:16px;
 white-space:nowrap;color:var(--ink)}
.tscale__lbls{display:flex;justify-content:space-between;margin-top:22px;
 font-family:var(--mono);font-size:.6rem;color:var(--muted)}
/* ---- GO gate ---- */
.gate__top{display:flex;align-items:center;gap:14px;margin:6px 0 4px}
.gate__count{font-size:1.7rem;font-weight:800;line-height:1;font-variant-numeric:tabular-nums;
 flex:0 0 auto}
.gate__of{font-size:.92rem;color:var(--muted);font-weight:600}
.gate__seg{display:flex;gap:5px;flex:1;min-width:110px}
.gate__seg span{height:8px;flex:1;border-radius:4px;background:var(--glass-brd)}
.gate__seg span.on{background:var(--go);box-shadow:0 0 8px color-mix(in srgb,var(--go) 55%,transparent)}
.gate__head{font-weight:700;font-size:.88rem;margin:6px 0 2px;color:var(--muted)}
.gate__head.allok{color:var(--go)}
.gate__list{list-style:none;margin:8px 0 0;padding:0}
.gate__list li{display:flex;align-items:center;flex-wrap:wrap;gap:4px 12px;padding:10px 8px;
 border-top:1px solid var(--glass-brd)}
.gate__list li:first-child{border-top:none}
li.g-no{background:color-mix(in srgb,var(--bad) 8%,transparent);border-radius:9px;
 border-top-color:transparent}
li.g-no+li{border-top-color:transparent}
.gmark{width:19px;height:19px;border-radius:50%;position:relative;flex:0 0 19px}
.g-ok .gmark{background:var(--go)}
.g-ok .gmark::after{content:"";position:absolute;left:6px;top:3px;width:5px;height:9px;
 border:solid #04120f;border-width:0 2px 2px 0;transform:rotate(43deg)}
.g-unk .gmark{box-shadow:inset 0 0 0 2px var(--muted)}
.g-unk .gmark::after{content:"?";position:absolute;left:50%;top:50%;
 transform:translate(-50%,-54%);color:var(--muted);font-size:.78rem;font-weight:800}
.g-unk .glabel{color:var(--muted)}
.g-no .gmark{box-shadow:inset 0 0 0 2px var(--bad)}
.g-no .gmark::before,.g-no .gmark::after{content:"";position:absolute;left:50%;top:50%;
 width:9px;height:2px;background:var(--bad)}
.g-no .gmark::before{transform:translate(-50%,-50%) rotate(45deg)}
.g-no .gmark::after{transform:translate(-50%,-50%) rotate(-45deg)}
.glabel{font-weight:700;font-size:.92rem}
.g-no .glabel{color:var(--bad)}
.gdetail{margin-left:auto;color:var(--muted);font-size:.8rem;text-align:right;max-width:62%}
/* ---- tips / stages ---- */
.tips{margin:8px 0 0;padding-left:20px}.tips li{margin:7px 0;line-height:1.6}
.stages{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:9px}
.stage{border:1px solid var(--glass-brd);border-radius:12px;overflow:hidden;padding-bottom:9px;
 background:color-mix(in srgb,var(--deep2) 35%,transparent)}
.stage.on{border-color:var(--stripe);
 box-shadow:0 0 0 1px var(--stripe),0 6px 20px color-mix(in srgb,var(--stripe) 24%,transparent)}
.stage__bar{height:6px}
.stage__hd{padding:8px 10px 4px;font-weight:800;font-size:.88rem}
.stage__now{display:inline-block;font-size:.6rem;background:var(--stripe);color:#180207;
 border-radius:7px;padding:1px 7px;margin-left:5px;vertical-align:middle;letter-spacing:.05em;
 font-weight:800}
.stage__row{font-size:.72rem;line-height:1.45;padding:2px 10px}
.stage__row b{color:var(--muted);font-weight:700}
.stage__how{font-size:.76rem;font-weight:700;padding:6px 10px 0;line-height:1.5}
/* ---- outlook ---- */
.outlook__stat{font-size:.9rem;margin-bottom:6px}
.outlook__stats{margin:4px 0 10px;display:flex;flex-direction:column;gap:2px}
.okv{display:flex;gap:12px;align-items:baseline;padding:6px 0;border-top:1px solid var(--glass-brd);
 font-size:.9rem}
.okv:first-child{border-top:none}
.ok-k{flex:0 0 10em;color:var(--muted);font-family:var(--mono);font-size:.7rem;letter-spacing:.04em}
.ok-v{font-variant-numeric:tabular-nums}
.spark{display:block;margin:2px 0}
.spk-grid{stroke:var(--glass-brd);stroke-width:1}
.spk-area{fill:var(--aqua);opacity:.14}
.spk-act{stroke:var(--aqua);fill:none;stroke-width:2.4;stroke-linejoin:round;stroke-linecap:round}
.spk-fc{stroke:var(--aqua);fill:none;stroke-width:2.4;stroke-dasharray:5 4;opacity:.85;
 stroke-linecap:round}
.spk-today{stroke:var(--muted);stroke-width:1;stroke-dasharray:3 3}
.spk-dot{fill:var(--stripe);filter:drop-shadow(0 0 5px var(--stripe))}
/* ---- depth layers (lake) ---- */
.depth{margin:8px 0 4px;max-width:420px}
.dl-name{fill:var(--ink);font-weight:700;font-size:13px}
.dl-how{fill:var(--ink);opacity:.82;font-size:11px}
.dl-tgt{fill:var(--stripe);font-weight:800;font-size:12px}
/* ---- flow / trace ---- */
.flow{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px;margin-top:8px}
.fi{border:1px solid var(--glass-brd);border-radius:11px;padding:10px 12px;
 background:color-mix(in srgb,var(--deep2) 35%,transparent);display:flex;flex-direction:column;gap:2px}
.fi-k{font-family:var(--mono);font-size:.66rem;letter-spacing:.04em;color:var(--muted)}
.fi-v{font-size:.98rem;font-weight:700;font-variant-numeric:tabular-nums;line-height:1.4}
.trace{margin-top:11px;padding:10px 14px;border-left:3px solid var(--stripe);
 background:color-mix(in srgb,var(--stripe) 8%,transparent);border-radius:0 10px 10px 0;font-size:.85rem}
/* ---- alerts ---- */
.alert{background:color-mix(in srgb,var(--bad) 14%,transparent);border:1px solid var(--bad);
 border-radius:11px;padding:11px 14px;font-weight:700}
.ok{background:color-mix(in srgb,var(--go) 12%,transparent);border:1px solid var(--go);
 border-radius:11px;padding:11px 14px}
.warnbar{background:color-mix(in srgb,var(--warn) 13%,transparent);border:1px solid var(--warn);
 border-radius:11px;padding:11px 14px;margin-top:12px;font-size:.88rem}
/* ---- map ---- */
.mapwrap{overflow-x:auto;color:var(--ink)}
.mapwrap svg{min-width:520px}
/* ---- tables ---- */
.tablewrap{overflow-x:auto;margin-top:8px}
table.ind{border-collapse:collapse;width:100%;font-size:.82rem;min-width:560px;
 font-variant-numeric:tabular-nums}
table.ind th,table.ind td{border:1px solid var(--glass-brd);padding:8px 10px;text-align:left;
 vertical-align:top}
table.ind th{background:color-mix(in srgb,var(--deep2) 45%,transparent);
 font-family:var(--mono);font-size:.72rem;letter-spacing:.04em}
.ik{font-weight:800;white-space:nowrap}
table.recon{min-width:420px}
.cav li{font-size:.85rem;margin:5px 0}
/* ---- changes ---- */
.chg{margin:4px 0 0;padding-left:2px;list-style:none}
.chg li{font-size:.9rem;margin:7px 0;padding-left:17px;position:relative}
.chg li::before{content:"";position:absolute;left:2px;top:.55em;width:6px;height:6px;
 background:var(--stripe);transform:rotate(45deg)}
/* ---- subpanel (nested details) ---- */
.subpanel>summary{cursor:pointer;list-style:none}
.subpanel>summary::-webkit-details-marker{display:none}
.subpanel>summary .panel__h{margin-bottom:0;display:inline-block}
.subpanel>summary::after{content:"開く";font-family:var(--mono);font-size:.66rem;color:var(--muted);
 float:right;border:1px solid var(--glass-brd);border-radius:999px;padding:3px 11px}
details[open].subpanel>summary::after{content:"閉じる"}
details[open].subpanel>summary{margin-bottom:8px}
/* ---- footer ---- */
.foot{color:var(--muted);font-size:.78rem;margin-top:44px;padding-top:18px;
 border-top:1px solid var(--glass-brd);text-align:center;line-height:1.85;
 max-width:60em;margin-left:auto;margin-right:auto}
/* ---- motion ---- */
@keyframes fadeup{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:none}}
@keyframes ringin{from{opacity:0;transform:scale(.92)}to{opacity:1;transform:scale(1)}}
.hero,.card{animation:fadeup .5s ease both}
.ring{animation:ringin .6s ease both}
/* ---- responsive ---- */
@media(max-width:820px){
 .hero__grid{grid-template-columns:auto 1fr;justify-items:start}
 .hero__art{grid-row:1;width:130px;height:80px}
 .hero__ring{grid-column:1;grid-row:2}
 .hero__main{grid-column:2;grid-row:1/3;align-self:center}}
@media(max-width:560px){
 .hero__grid{grid-template-columns:1fr;justify-items:center;text-align:center}
 .hero__art{width:130px;height:80px}
 .hero__main{grid-column:1;grid-row:auto;text-align:left;width:100%}
 .hero__badges,.hero__pill{justify-content:flex-start}
 .gdetail{flex-basis:100%;margin-left:31px;text-align:left;max-width:none}
 .ok-k{flex-basis:8.5em}
 .genstamp{margin-left:0}}
@media(prefers-reduced-motion:reduce){
 *{animation:none!important;transition:none!important;scroll-behavior:auto!important}}
@media print{
 :root{--abyss:#fff;--glass:#fff;--ink:#111;--muted:#444;--glass-brd:#bbb;--aur-op:0}
 .aurora{display:none}.wrap{padding:0}
 .hero,.card,.panel,.stage,.fi{break-inside:avoid;box-shadow:none}
 details:not([open])>.card__panels{display:block!important}}
</style>"""


# --------------------------------------------------------------------------- #
# page build
# --------------------------------------------------------------------------- #
def build_html(conn, run_date=None, full_document=False) -> str:
    """ページ生成。full_document=True で <!doctype html> 完全文書（GitHub Pages 等の
    直接配信用）、False で素のフラグメント（claude.ai Artifact が骨格を被せる用）。"""
    generated = dt.datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    # 呼び出し側(daily_update)が JST の run_date を渡す — CI(UTC) では date.today() が
    # JST より1日過去になり、鮮度窓・台帳・描画の日付が食い違うため必ず同一値で通す。
    run_date = run_date or dt.datetime.now(JST).date().isoformat()

    items, as_ofs = [], []
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
            items.append({"v": v, "reach": reach, "delta": delta, "recon": recon})
        except Exception as exc:  # noqa: BLE001 — 1区間の失敗を隔離（他区間と全体生成は続行）
            logger.warning("reach %s のパネル生成に失敗（スキップ）: %s", reach_id, exc)
            continue

    # ヒーローは「実データのある verified 区間の先頭」。データ欠落の区間をヒーローに選ぶと
    # 空表示になったうえグリッドからも外れて消えるため、as_of を持つ区間から選ぶ。
    dated = [it for it in items if it["v"].as_of]
    verified = [it for it in dated if it["v"].source_confidence == "verified"]
    hero_item = verified[0] if verified else (dated[0] if dated else None)
    grid_items = [it for it in items if it is not hero_item]
    grid_verified = [it for it in grid_items if it["v"].source_confidence == "verified"]
    grid_refs = [it for it in grid_items if it["v"].source_confidence != "verified"]

    hero_html = _hero(hero_item, run_date) if hero_item else ""
    grid = '<section class="grid">' + "".join(_card(it, run_date) for it in grid_verified)
    if grid_refs:
        grid += ('<div class="refhead"><h2>参考区間・参考湖（物理データ + 注意書きに留めます）</h2>'
                 '<p class="src">以下は公式データ源の実在確認が『参考』レベルの区間・湖です。'
                 '物理データ（気象・水位・水温・ダム放流）と季節の注意書きは正直に出しますが、'
                 '確信を持った『行くべし』は出しません（システムが自動で様子見へ格下げ済み）。'
                 '正確な期間・区間・遊漁ルールは各漁協・現地にご確認ください。</p></div>')
        grid += "".join(_card(it, run_date) for it in grid_refs)
    grid += '</section>'

    page_asof = max(as_ofs) if as_ofs else None
    # Static snapshot: show the DATA date and the PAGE-GENERATION time separately so a
    # frozen page can't masquerade as "現在". No decaying "本日反映" badge here.
    page_fresh = guide.freshness(page_asof, run_date)
    datebar = (f'<div class="datebar"><span class="datebar__cal">'
               f'{guide.jp_date(page_asof, with_year=True)}</span>'
               '<span class="datebar__note">のデータ（毎日更新のスナップショット）</span>'
               f'<span class="fresh fresh--{page_fresh["level"]}">{page_fresh["label"]}</span>'
               f'<span class="genstamp">ページ生成 {generated}</span></div>')
    legend = ('<div class="legend">'
              '<span><i class="dot dot--go"></i>行くべし＝好条件</span>'
              '<span><i class="dot dot--warn"></i>様子見＝決め手なし</span>'
              '<span><i class="dot dot--bad"></i>見送り＝増水/濁り/高水温</span>'
              '<span><i class="dot dot--temp"></i>水温＝判定の主軸（適水温＋C&R安全）</span></div>')
    masthead = ('<header class="masthead">'
                '<div class="eyebrow">神流川・利根川・吾妻川・渡良瀬川 ＋ 群馬の湖 — 区間で読む鱒</div>'
                '<h1>群馬 ニジマスレーダー</h1>'
                '<p class="lead">群馬のニジマス（冬期キャッチ&リリース釣場・本流放流区間・高原の湖）の'
                '〈行くべし/様子見/見送り〉を、気象・水位・水温（気温からの未較正プロキシ）・'
                'ダム放流・釣況などの公開データだけから、<b>区間ごと</b>に毎日推定する非公式サイトです。'
                '同じ川でも自然流量の上流区間とダム下流区間は挙動が真逆のため、河川ではなく区間で判定します。'
                '判定の根拠も限界も、すべてそのまま公開します。</p>'
                '<p class="lead">精度と網羅の両立方針: 公式データ源を確認できた区間だけ確信を持った'
                '『行くべし』を出し、それ以外の<b>参考区間</b>は物理データと注意書きに留めます。'
                '水温はニジマス判定の主軸ですが、ライブ計測源が無いため気温からの'
                '<b>未較正プロキシ</b>です（現場報告があればそちらを優先）。</p></header>')
    footer = ('<footer class="foot">本サイトは個人が運営する非公式ページです。判定は公開データからの'
              '推定で、釣果や安全を保証するものではありません。水温は実測ではなく気温からの未較正'
              'プロキシです（湖は標高補正付きの推定）。増水・危険の確認は必ず公式の防災情報を、'
              '遊漁券・解禁期間・C&Rルールは各漁協・各釣場の案内に従ってください。<br>'
              '出所: 気象庁AMeDAS/週間予報 ・ Yahoo!天気・災害（川の水位） ・ 釣況ブログ→Gemini ・ '
              '国土交通省 利根川ダム統合管理 ・ 各漁協/各湖の公式情報<br>'
              f'ページ生成: {generated}（JST）。データ基準日よりページ生成が大きく古い場合、'
              '自動更新が止まっている可能性があります。</footer>')
    favicon = ('<link rel="icon" href="data:image/svg+xml,'
               '<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22>'
               '<text y=%22.9em%22 font-size=%2290%22>🎣</text></svg>">')
    head = ('<meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta name="generated" content="{generated}+09:00">'
            '<meta name="description" content="群馬のニジマス釣り（神流川・利根川・吾妻川・渡良瀬川と'
            '群馬の湖）の〈行くべし/様子見/見送り〉を、気象・水位・水温プロキシ・ダム放流・釣況の'
            '公開データだけで区間ごとに毎日推定する非公式レーダー。">'
            f'{favicon}<title>群馬 ニジマスレーダー</title>')
    content = ('<div class="wrap">' + masthead + datebar + legend
               + hero_html + grid + footer + '</div>')
    if full_document:
        return ('<!doctype html><html lang="ja"><head>' + head + CSS + '</head><body>'
                + AURORA + content + '</body></html>')
    return head + CSS + AURORA + content


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
