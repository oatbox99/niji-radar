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

デザイン: 「Thermal Cartography(水温地図)」。唯一の色体系は水温ランプ(0–24℃)で、それ以外は
モノトーンの計器盤。各区間はその温度フィールド上の一点として置かれる。TSI/推定水温は水温ランプ上に
「ニジマスの側線(桃色)」としてプロットする。フォントはシステムスタック、画像は使わずSVG/CSSのみで
自己完結（GitHub Pages・フォントCDN不使用）。ダーク/ライト両対応・prefers-reduced-motion 尊重。

  python -m scripts.export_html [出力パス]
"""
from __future__ import annotations

import datetime as dt
import html as html_mod
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.river_map import river_map_svg  # noqa: E402
from src import calibration, config, db, decision, guide  # noqa: E402

logger = logging.getLogger(__name__)

JST = dt.timezone(dt.timedelta(hours=9))

_WD_JP = ["月", "火", "水", "木", "金", "土", "日"]
WATER_WORD = {0: "クリア", 1: "笹濁り", 2: "泥濁り", None: "情報なし"}
# C&Rリスク帯 → (表示語, CSSクラス)
CR_WORD = {
    "safe": ("安全", "cr-safe"),
    "caution": ("注意", "cr-caution"),
    "strong": ("強い注意", "cr-strong"),
    "nogo": ("見送り推奨", "cr-nogo"),
    "unknown": ("不明", "cr-unk"),
}
# 判定 → (CSSクラス, 形アイコン=CSS描画, ラベル)。色単独に頼らず 形+ラベル併記。
BADGE = {
    "GO": ("go", "▲行くべし GO"),
    "CAUTION": ("caution", "●様子見 CAUTION"),
    "NO_GO": ("nogo", "■見送り NO-GO"),
}
# waterbody → (語, 絵文字)
WB = {"river": ("河川", "🎣"), "lake": ("湖", "🏞")}


# --------------------------------------------------------------------------- #
# small numeric helpers (thermal-ramp plotting)
# --------------------------------------------------------------------------- #
def _clamp_pct(x) -> float:
    return max(0.0, min(100.0, float(x)))


def _temp_pin_left(temp) -> float:
    """推定水温を水温ランプ 0–24℃ 上の位置(%)へ。"""
    return _clamp_pct(temp / 24.0 * 100.0)


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
# new visual primitives (Thermal Cartography — 見た目のみ、データは Verdict から)
# --------------------------------------------------------------------------- #
def _badge(level, big=False):
    cls, label = BADGE.get(level, ("caution", "●様子見"))
    sz = " lg" if big else ""
    return (f'<span class="badge {cls}{sz}"><span class="ic" aria-hidden="true"></span>'
            f'{label}</span>')


def _conf_tag(src):
    """source_confidence → ◆高信頼 / ◇参考(格下げ)。記号は CSS ::before で付与。"""
    if src == "verified":
        return '<span class="conf verified">高信頼</span>'
    return '<span class="conf ref">参考</span>'


def _wb_tag(waterbody, cls="wb"):
    word, emoji = WB.get(waterbody, ("河川", "🎣"))
    en = "LAKE" if waterbody == "lake" else "RIVER"
    return (f'<span class="{cls}"><span aria-hidden="true">{emoji}</span>{word} {en}</span>')


def _ramp_meter(v, card=False):
    """TSI(0–100)を水温ランプ上にプロットするメーター。tsi None は pin無し・数字「—」。"""
    tsi = v.tsi
    if tsi is None:
        valnum, pin, aria = "—", "", "TSI 不明"
    else:
        pct = _clamp_pct(tsi)
        valnum = f"{tsi:.0f}"
        pin = (f'<div class="fillmask" style="left:{pct:.1f}%"></div>'
               f'<div class="pin" style="left:{pct:.1f}%"></div>')
        aria = f"TSI {valnum} / 100"
    cls = "tsi card-tsi" if card else "tsi"
    return (f'<div class="{cls}">'
            '<div class="lab"><span>TSI 適性 · 側線メーター</span><span>0 – 100</span></div>'
            f'<div class="val">{valnum}<small> / 100</small></div>'
            f'<div class="meter" role="img" aria-label="{aria}">{pin}</div>'
            '<div class="meter-scale"><span>0</span><span>50</span><span>100</span></div></div>')


def _temp_bits(v):
    """(表示ラベル, 値HTML, 注記, 水温ランプpin) を返す。observed優先・実測とは偽らない。"""
    obs = v.observed_water_temp
    temp = obs if obs is not None else v.water_temp_proxy
    if temp is None:
        return ("推定水温", "—", "推定不可", "")
    val = f"{temp:.0f}<small> ℃</small>"
    pin = (f'<div class="tempramp"><span class="pin" style="left:{_temp_pin_left(temp):.1f}%">'
           '</span></div>')
    if obs is not None:
        return ("現場報告 水温", val, "（ブログ記載）", pin)
    return ("推定水温", val, "（気温プロキシ）", pin)


def _card_metrics(v):
    """カード用: 推定水温(水温ランプpin付き) / C&R / 信頼度。※水温以外に pin は付けない。"""
    tlabel, tval, tnote, tpin = _temp_bits(v)
    cr_word = CR_WORD.get(v.cr_risk, ("不明", "cr-unk"))[0]
    cr_label = "C&amp;R" if v.catch_release else "水温目安"
    conf = round(v.confidence * 100)
    return ('<div class="metrics">'
            f'<div class="mt"><div class="k">{tlabel}</div><div class="v temp">{tval}</div>'
            f'{tpin}<div class="mnote">{tnote}</div></div>'
            f'<div class="mt"><div class="k">{cr_label}</div><div class="v">{cr_word}</div></div>'
            f'<div class="mt"><div class="k">信頼度</div><div class="v">{conf}<small> %</small>'
            '</div></div></div>')


def _hero_reads(v):
    """ヒーロー4読み: 推定水温(pin付) / C&R生存リスク / 信頼度 / データ源。※水温以外に pin無し。"""
    tlabel, tval, tnote, tpin = _temp_bits(v)
    cr_word = CR_WORD.get(v.cr_risk, ("不明", "cr-unk"))[0]
    cr_label = "C&amp;R 生存リスク" if v.catch_release else "水温の目安"
    conf = round(v.confidence * 100)
    sc = ("verified 高信頼" if v.source_confidence == "verified"
          else f"{v.source_confidence}（格下げ）")
    return ('<div class="reads">'
            f'<div class="read"><div class="k">{tlabel}</div><div class="v temp">{tval}</div>'
            f'{tpin}<div class="rnote">{tnote}</div></div>'
            f'<div class="read"><div class="k">{cr_label}</div><div class="v">{cr_word}</div></div>'
            f'<div class="read"><div class="k">信頼度 CONFIDENCE</div>'
            f'<div class="v num">{conf}<small> %</small></div></div>'
            f'<div class="read"><div class="k">データ源 SOURCE</div>'
            f'<div class="v" style="font-size:15px;line-height:1.3">{sc}</div></div></div>')


def _why_list(v):
    if not v.reasons:
        return ""
    lis = "".join(f"<li>{r}</li>" for r in v.reasons)
    return f'<div class="why"><div class="h">なぜこの判定か</div><ul>{lis}</ul></div>'


def _caveats_list(v):
    if not v.caveats:
        return ""
    lis = "".join(f"<li>{c}</li>" for c in v.caveats)
    return f'<div class="caveats"><div class="h">正直な弱点</div><ul>{lis}</ul></div>'


def _day_glyph(s, p, open_ok=True, is_ref=False):
    """その日の TState を glyph へ粗マップ。4段ゲートと同順で営業/合法が釣果より上位。

    open_ok=False(営業/解禁期間外・定休日)は水温が良くても■ — バッジは「今日」だが strip は
    未来7日を示す唯一のシグナルのため、ここが季節ゲートを見ないと閉鎖区間への釣行を誘導する。
    参考区間(is_ref)は確信▲を出さない(source_confidence 格下げの glyph 版)。
    """
    if not open_ok:
        return "no"
    if s.is_scour:
        return "no"
    t = s.water_temp_c
    if t is not None and t >= p.t_stress:      # 20℃ 摂餌停止域
        return "no"
    if s.tsi >= p.good_min:                     # 好適以上
        return "cau" if is_ref else "go"
    return "cau"


def _outlook_strip(v):
    """7日アウトルック(温度着色バー)。series の未来日から [曜, round(℃), glyph] をサーバ生成。

    温度欠測日も枠を出す(— 表示) — 無言で脱落させると「7日」ラベルの左詰め6本になり
    先頭バーを明日と誤読させるため。着色は JS の tempColor()。
    """
    p = decision.TroutParams()
    reach = config.REACHES[v.reach_id]
    is_ref = v.source_confidence != "verified"
    future = [s for s in (v.series or []) if v.as_of is None or s.date > v.as_of]
    rows, closed_any, missing_any = [], False, False
    for s in future[:7]:
        try:
            wd = _WD_JP[dt.date.fromisoformat(s.date).weekday()]
        except ValueError:
            continue
        open_ok = decision.reach_open(reach, s.date)["open"]
        closed_any = closed_any or not open_ok
        t = s.water_temp_c
        if t is None:
            missing_any = True
            rows.append([wd, None, "na"])
            continue
        rows.append([wd, round(t), _day_glyph(s, p, open_ok, is_ref)])
    if not rows or all(r[2] == "na" for r in rows):
        return ""
    data = json.dumps(rows, ensure_ascii=False)
    unit = "推定表層水温" if v.waterbody == "lake" else "推定水温"
    notes = ""
    if closed_any:
        notes += " · ■は営業/解禁期間外の日を含みます"
    if missing_any:
        notes += " · —=予報欠測日"
    return ('<div class="outlook"><div class="h">7日アウトルック</div>'
            f"<div class=\"ol-strip\" data-outlook='{data}'></div>"
            f'<div class="ol-legend">棒の色 = {unit}（推定） · 数字 = ℃ · '
            f'▲行くべし / ●様子見 / ■見送り{notes}</div></div>')


def _lake_box(v):
    """湖の深度戦略ブロック。NO_GO は「どう釣るか」を出さず見送りに差替(既存挙動を維持)。"""
    if v.waterbody != "lake":
        return ""
    if v.level == "NO_GO":
        return ('<div class="lakebox"><div class="h">◈ 深度戦略 / 成層の注意</div>'
                '<p>本日は見送り推奨です（営業期間外、または表層が高水温でリリースした魚に危険）。'
                '深度戦略は次の好機日にご覧ください。</p></div>')
    shore = config.REACHES[v.reach_id].get("shore_only", False)
    note = guide.lake_depth_note(v.water_temp_proxy, shore_only=shore)
    causal = ('表層が温むと魚は<b>水温躍層の直上</b> —— 冷たく溶存酸素の残る層へ落ちます。'
              '晴天無風の午後は表層を嫌って中層へ、朝夕は表層に浮きます。カウントダウンで'
              '層を刻むのが定石です。')
    return ('<div class="lakebox"><div class="h">◈ 深度戦略 / 成層の因果</div>'
            f'<p>{note}</p><p>{causal}</p>'
            '<p><span class="est">⚠ 躍層・溶存酸素・深度別水温は実測源が無く、季節推定</span>'
            'です（実測ではありません）。当日の風と日射で成層は日々動きます。</p></div>')


def _cartouche(v):
    """カード頭の意匠SVG: 河川=flow-lines(川マップ相当) / 湖=等深線(bathymetric)。装飾。"""
    lvlcls = BADGE.get(v.level, ("caution",))[0]
    color = f"var(--{lvlcls if lvlcls != 'caution' else 'caution'})"
    if v.waterbody == "lake":
        return ('<svg class="cartuche" viewBox="0 0 400 96" preserveAspectRatio="none" '
                'aria-hidden="true">'
                f'<g fill="none" stroke="{color}" stroke-opacity=".26" stroke-width="1.3">'
                '<ellipse cx="200" cy="52" rx="220" ry="70"/>'
                '<ellipse cx="200" cy="52" rx="168" ry="52"/>'
                '<ellipse cx="200" cy="52" rx="116" ry="35"/>'
                '<ellipse cx="200" cy="52" rx="66" ry="20"/>'
                '<ellipse cx="200" cy="52" rx="26" ry="8"/></g></svg>')
    return ('<svg class="cartuche" viewBox="0 0 400 96" preserveAspectRatio="none" '
            'aria-hidden="true">'
            f'<g fill="none" stroke="{color}" stroke-opacity=".26" stroke-width="1.4">'
            '<path d="M-10,20 C120,40 260,4 410,26"/>'
            '<path d="M-10,40 C120,62 260,26 410,48"/>'
            '<path d="M-10,60 C120,82 260,46 410,68"/>'
            '<path d="M-10,80 C120,100 260,66 410,86"/></g></svg>')


def _hero_stripe():
    """ヒーロー背景の側線SVG(ニジマスの lateral-line を水温グラデで描く一手)。装飾・aria-hidden。"""
    return (
        '<svg class="stripe" viewBox="0 0 1200 460" preserveAspectRatio="none" aria-hidden="true">'
        '<defs><linearGradient id="lat" x1="0" y1="0" x2="1" y2="0">'
        '<stop offset="0" stop-color="#0f2f63" stop-opacity="0"/>'
        '<stop offset=".28" stop-color="#146290" stop-opacity=".14"/>'
        '<stop offset=".52" stop-color="#159e8b" stop-opacity=".22"/>'
        '<stop offset=".78" stop-color="#e35d8a" stop-opacity=".20"/>'
        '<stop offset="1" stop-color="#dca032" stop-opacity="0"/></linearGradient>'
        '<linearGradient id="latcore" x1="0" y1="0" x2="1" y2="0">'
        '<stop offset="0" stop-color="#e35d8a" stop-opacity="0"/>'
        '<stop offset=".5" stop-color="#e35d8a" stop-opacity=".5"/>'
        '<stop offset="1" stop-color="#e35d8a" stop-opacity="0"/></linearGradient></defs>'
        '<path d="M0,300 C300,250 520,340 760,250 C960,175 1080,215 1200,150 '
        'L1200,460 L0,460 Z" fill="url(#lat)"/>'
        '<path d="M0,266 C300,214 540,306 780,214 C980,138 1090,182 1200,120" '
        'fill="none" stroke="url(#latcore)" stroke-width="3"/></svg>')


def _rampkey():
    """マストヘッドの水温カラースケール宣言(唯一の色体系)。純CSS/HTML・静的。"""
    return (
        '<div class="rampkey" aria-label="水温カラースケール 0から24度">'
        '<div class="cap"><span>WATER TEMPERATURE · 唯一の色体系</span><span>℃</span></div>'
        '<div class="rampbar"><span class="zone" style="left:41.7%"></span>'
        '<span class="zone" style="left:66.7%"></span></div>'
        '<div class="rampticks">'
        '<span style="left:0%">0<br>凍結圏</span>'
        '<span style="left:20.8%">5</span>'
        '<span class="opt" style="left:41.7%">10<br>適水温</span>'
        '<span class="opt" style="left:66.7%">16<br>適水温</span>'
        '<span style="left:83.3%">20<br>摂餌停止</span>'
        '<span style="left:100%">24<br>致死</span></div></div>')


def _more(ref_note, panels):
    """根拠・データ源・限界を折り畳みに(正直さ設計=詳細パネル群を丸ごと保持)。"""
    inner = ref_note + panels
    if not inner:
        return ""
    return ('<details class="more"><summary>根拠・データ源・限界を開く</summary>'
            f'<div class="more-body">{inner}</div></details>')


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
def _static_fresh_label(fresh):
    """静的ページ用の鮮度ラベル: days==0 の「本日反映」断定を避け、生成時点基準の非減衰表現へ。
    閲覧時の実日付を知り得ない静的HTMLでも古さを隠さない(実際の古さは genstamp で判断)。"""
    return "生成時点で最新" if fresh["days"] == 0 else fresh["label"]


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
            f'データ</span><span class="fresh fresh--{fresh["level"]}">'
            f'{_static_fresh_label(fresh)}</span>'
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
    elif n_fail == 0 and n_unk == 0:
        # 全ゲート充足だが GO でない = チェックリスト外の理由での格下げ(参考データ源/午後高水温等)。
        why = ("参考データ源のため確信GOを保留しています（現地確認前提の様子見）"
               if v.source_confidence != "verified"
               else "追加の注意（高水温予報など）で様子見にしています")
        head = f'<div class="gate__head">GO条件は満たしていますが、{why}</div>'
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
        # NO_GO(営業期間外/表層高温でC&R危険)のときは「どう釣るか」を出さない — 見送り判定と矛盾させない。
        if v.level == "NO_GO":
            return ('<div class="panel"><b class="panel__h">今日の狙い方</b>'
                    '<p class="tipline">本日は見送り推奨です（営業期間外、または表層が高水温で'
                    'リリースした魚に危険）。深度戦略は次の好機日にご覧ください。</p></div>')
        shore = config.REACHES[v.reach_id].get("shore_only", False)
        note = guide.lake_depth_note(v.water_temp_proxy, shore_only=shore)
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
                f'<p class="prose">{guide.WHY_LAKE}</p></div>')
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
            f'<p class="prose">{guide.WHY_TROUT}</p>'
            f'<div class="stages">{"".join(cards)}</div>'
            f'<div class="src">{foot}</div></div>')


def _outlook_panel(v):
    o = v.outlook
    if not o:
        return ""
    ng = o.get("next_good")
    tref = "（参考区間・予報上の目安）" if v.source_confidence != "verified" else ""
    if ng and ng.get("closed"):
        # 合法/営業ゲートは釣果より上位 — 期間外の日を「次に行くなら」と推奨しない。
        head = ('<div class="outlook__stat">予報上は水温が好適になる日がありますが、'
                'その日は<b>営業/解禁期間外（または定休日）の可能性</b>があるため'
                '『次に行くなら』候補には出しません。営業期間の確認が先です。</div>')
    elif ng:
        rel = f"・予報信頼度{ng['reliability']}" if ng.get("reliability") else ""
        head = (f'<div class="outlook__stat"><b>次に行くなら {guide.jp_date(ng["date"])} 頃</b>'
                f' — TSI{ng["tsi"]:.0f}・{ng["quality"]}{rel}{tref}</div>')
    else:
        head = ('<div class="outlook__stat">今後1週間は、予報上『行くべし』級の日が'
                '見当たりません（水温が適域から外れる、または増水の懸念があります）。</div>')
    best = o["best"]
    best_rel = f"・信頼度{best['reliability']}" if best.get("reliability") else ""
    best_closed = "・※営業/解禁期間外の可能性" if best.get("closed") else ""
    wk = "　".join(f'{w["date"][5:]}({w["wd"]}) TSI{w["tsi"]:.0f}'
                   + ("※期間外" if w.get("closed") else "") for w in o["weekend"])
    okv = [("傾向", o["trend"]),
           ("ピーク", f'TSI{best["tsi"]:.0f}（{guide.jp_date(best["date"])}頃・'
                     f'{best["quality"]}{best_rel}{best_closed}）')]
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
            '<p class="prose">TSI（適性）は未較正の推定です。釣り人のブログ報告（体感コンディション）との'
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
        # ブログ由来テキストの唯一の生流入点 — CSP無しのGitHub Pagesで全訪問者に効く
        # stored XSS 面のため必ずエスケープする(Gemini経由でも元はブログの自由文)。
        trace = ('<div class="trace">現場報告の根拠（ブログ引用）：'
                 f'「{html_mod.escape(str(v.observed_excerpt))}」{conf}</div>')
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


def _ref_note(waterbody):
    kind = "湖" if waterbody == "lake" else "区間"
    return ('<div class="warnbar">この' + kind + 'は公式データ源の実在確認が『参考』レベルです。'
            '物理データ（気象・水位・水温・季節）と注意書きは出しますが、確信を持った『行くべし』は'
            '出していません（自動で様子見へ格下げ済み）。正確な期間・区間・ルールは各漁協・現地で'
            'ご確認ください。</div>')


def _empty_card(v, reach):
    return ('<article class="card empty"><div class="card-body"><div class="card-top">'
            f'{_wb_tag(v.waterbody)}</div>'
            f'<h3>{reach["label"]}</h3>'
            '<div class="warnbar">この区間はまだ実データがありません（収集待ち）。'
            '判定は表示しません。</div></div></article>')


def _card(item, run_date):
    v, reach = item["v"], item["reach"]
    if v.as_of is None:
        return _empty_card(v, reach)
    urls = _source_urls(v)
    verified = v.source_confidence == "verified"
    lvlcls = BADGE.get(v.level, ("caution",))[0]
    refcls = "" if verified else " ref"
    ref_note = "" if verified else _ref_note(v.waterbody)
    panels = _detail_panels(v, item["delta"], item["recon"], urls, run_date)
    sub = guide.method_label(v.methods, v.catch_release)
    return (
        f'<article class="card {lvlcls}{refcls}">'
        + _cartouche(v)
        + '<div class="card-body">'
        '<div class="card-top">'
        f'{_wb_tag(v.waterbody)}{_conf_tag(v.source_confidence)}</div>'
        f'<h3>{reach["label"]}</h3>'
        f'<p class="creach">{sub}</p>'
        f'{_badge(v.level)}'
        f'{_ramp_meter(v, card=True)}'
        f'{_card_metrics(v)}'
        f'{_why_list(v)}'
        f'{_lake_box(v)}'
        f'{_caveats_list(v)}'
        f'{_outlook_strip(v)}'
        f'{_more(ref_note, panels)}'
        '</div></article>')


def _hero(item, run_date):
    v, reach = item["v"], item["reach"]
    if v.as_of is None:
        return ""
    urls = _source_urls(v)
    verified = v.source_confidence == "verified"
    ref_note = "" if verified else _ref_note(v.waterbody)
    panels = _detail_panels(v, item["delta"], item["recon"], urls, run_date)
    flag = "◆ FLAGSHIP · VERIFIED" if verified else "◇ FLAGSHIP · 参考"
    sub = guide.method_label(v.methods, v.catch_release)
    return (
        '<section class="hero" aria-labelledby="heroName">'
        f'<span class="flag">{flag}</span>'
        + _hero_stripe()
        + '<div class="hero-grid"><div class="hero-main">'
        f'<p class="wb-tag">{_wb_tag(v.waterbody, cls="wb-inner")} · 冬期/管理区間ほか</p>'
        f'<h2 id="heroName">{reach["label"]}</h2>'
        f'<p class="subreach">{sub}</p>'
        f'{_badge(v.level, big=True)}'
        f'<p class="hero-headline">{v.headline}</p>'
        f'{_hero_reads(v)}'
        '</div><div class="hero-side">'
        f'{_ramp_meter(v)}'
        f'{_why_list(v)}'
        f'{_lake_box(v)}'
        f'{_caveats_list(v)}'
        '</div></div>'
        f'<div class="hero-panels">{_more(ref_note, panels)}</div>'
        '</section>')


# --------------------------------------------------------------------------- #
# CSS — 「Thermal Cartography(水温地図)」自己完結・ダーク/ライト両対応
# --------------------------------------------------------------------------- #
CSS = """<style>
/* ============ TOKENS ============
   唯一の色体系 = 水温ランプ(0–24℃)。それ以外はモノトーンの計器盤。
   --t10/--go はライト時に一段濃く(パネル上テキストの可読性=修正B)、ダークは明るいまま。 */
:root{
  --t00:#0f2f63; --t05:#146290; --t16:#57b56b; --t20:#dca032; --t24:#a81f1c;
  --trout:#e35d8a;
  --t10:#0e7d6e;                 /* light: darkened optimal teal (fix B) */
  --ramp:linear-gradient(90deg,var(--t00) 0%,var(--t05) 21%,var(--t10) 42%,
     var(--t16) 67%,var(--t20) 83%,var(--t24) 100%);
  --go:#0e7d6e; --caution:#9a6a12; --nogo:#a81f1c;
  --ink:#14181d; --ink-2:#4a5561; --ink-3:#7c8894;
  --paper:#eceef0; --panel:#f7f8f9; --panel-edge:#d3d8dd; --hair:#c3cad1;
  --field-veil:rgba(236,238,240,.62);
  --mono:ui-monospace,"SFMono-Regular",Menlo,"DejaVu Sans Mono",monospace;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Hiragino Kaku Gothic ProN","Noto Sans JP",sans-serif;
  --edge:clamp(16px,4vw,44px); --maxw:1180px;
  /* alias layer (late-bound → follows theme) for the ported honest-detail panels */
  --muted:var(--ink-3); --ink2:var(--ink-2); --faint:var(--hair);
  --glass:var(--panel); --glass-brd:var(--panel-edge); --deep2:var(--panel);
  --abyss:var(--paper); --stripe:var(--trout); --aqua:var(--t10); --aqua-deep:var(--t05);
  --warn:var(--caution); --bad:var(--nogo);
}
@media (prefers-color-scheme:dark){:root{
  --t10:#159e8b; --go:#159e8b; --caution:#dca032; --nogo:#e5563f;
  --ink:#e9edf1; --ink-2:#a3adb8; --ink-3:#69747f;
  --paper:#080a0d; --panel:#0f1319; --panel-edge:#232b34; --hair:#2b333d;
  --field-veil:rgba(8,10,13,.60);
}}
:root[data-theme="light"]{
  --t10:#0e7d6e; --go:#0e7d6e; --caution:#9a6a12; --nogo:#a81f1c;
  --ink:#14181d; --ink-2:#4a5561; --ink-3:#7c8894;
  --paper:#eceef0; --panel:#f7f8f9; --panel-edge:#d3d8dd; --hair:#c3cad1;
  --field-veil:rgba(236,238,240,.62);
}
:root[data-theme="dark"]{
  --t10:#159e8b; --go:#159e8b; --caution:#dca032; --nogo:#e5563f;
  --ink:#e9edf1; --ink-2:#a3adb8; --ink-3:#69747f;
  --paper:#080a0d; --panel:#0f1319; --panel-edge:#232b34; --hair:#2b333d;
  --field-veil:rgba(8,10,13,.60);
}

/* ============ BASE ============ */
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--paper);color:var(--ink);font-family:var(--sans);line-height:1.6;
  font-variant-numeric:tabular-nums;-webkit-font-smoothing:antialiased;overflow-x:hidden}
b{font-weight:700}
.num,.mono{font-family:var(--mono);font-variant-numeric:tabular-nums lining-nums}
#field{position:fixed;inset:0;width:100%;height:100%;z-index:-2;display:block}
#veil{position:fixed;inset:0;z-index:-1;background:var(--field-veil);backdrop-filter:saturate(105%)}
@media (prefers-reduced-motion:reduce){#field{opacity:.9}}
a{color:var(--t05)}
:focus-visible{outline:2px solid var(--trout);outline-offset:3px;border-radius:2px}
.wrap{max-width:var(--maxw);margin:0 auto;padding:0 var(--edge)}

/* ============ MASTHEAD ============ */
.masthead{padding:clamp(30px,6vw,68px) 0 26px}
.brandrow{display:flex;justify-content:space-between;align-items:flex-start;gap:20px;flex-wrap:wrap}
.eyebrow{font-family:var(--mono);font-size:11px;letter-spacing:.34em;text-transform:uppercase;
  color:var(--ink-3);margin:0 0 14px}
h1.title{font-size:clamp(34px,8.4vw,86px);line-height:.98;margin:0;font-weight:760;
  letter-spacing:-.022em;
  background:linear-gradient(96deg,var(--trout) 0%,var(--t20) 26%,var(--t16) 58%,
     var(--t10) 82%,var(--t05) 100%);
  -webkit-background-clip:text;background-clip:text;color:transparent}
.title .en{display:block;font-family:var(--mono);font-weight:500;font-size:clamp(11px,2.4vw,15px);
  letter-spacing:.18em;margin-top:14px;-webkit-text-fill-color:var(--ink-3);color:var(--ink-3);background:none}
.mh-tools{display:flex;gap:12px;align-items:stretch}
.seal{writing-mode:vertical-rl;text-orientation:upright;font-family:var(--mono);font-size:11px;
  letter-spacing:.2em;color:var(--trout);border:1px solid var(--trout);border-radius:2px;
  padding:8px 4px;opacity:.85}
.lede{max-width:60ch;margin:22px 0 0;color:var(--ink-2);font-size:clamp(14px,1.9vw,17px);line-height:1.62}
.lede b{color:var(--ink);font-weight:640}
.toggle{font-family:var(--mono);font-size:11px;letter-spacing:.12em;background:var(--panel);
  color:var(--ink-2);border:1px solid var(--panel-edge);padding:9px 13px;border-radius:2px;
  cursor:pointer;white-space:nowrap;text-transform:uppercase;transition:color .15s,border-color .15s}
.toggle:hover{color:var(--ink);border-color:var(--hair)}
.metabar{display:flex;flex-wrap:wrap;gap:14px 26px;align-items:center;margin-top:30px;padding:16px 0;
  border-top:1px solid var(--hair);border-bottom:1px solid var(--hair)}
.legend{display:flex;gap:18px;flex-wrap:wrap;align-items:center}
.lg{display:inline-flex;gap:8px;align-items:center;font-size:12.5px;color:var(--ink-2)}
.lg .gl{width:0;height:0;border-left:6px solid transparent;border-right:6px solid transparent}
.gl.go{border-bottom:11px solid var(--go)}
.dot.cau{width:12px;height:12px;background:var(--caution);border-radius:50%}
.sq.no{width:11px;height:11px;background:var(--nogo)}
.legend .note{color:var(--ink-3);font-size:12px}
.dates{margin-left:auto;display:flex;gap:20px;flex-wrap:wrap;font-family:var(--mono);
  font-size:11.5px;color:var(--ink-3);align-items:center}
.dates b{color:var(--ink);font-weight:600}
.fresh{font-family:var(--mono);font-size:10px;letter-spacing:.04em;border-radius:999px;
  padding:2px 8px;border:1px solid var(--hair)}
.fresh--ok{color:var(--go);border-color:color-mix(in srgb,var(--go) 45%,transparent)}
.fresh--warn{color:var(--caution);border-color:color-mix(in srgb,var(--caution) 45%,transparent)}

/* ============ THERMAL RAMP KEY ============ */
.rampkey{margin:34px 0 6px}
.rampkey .cap{display:flex;justify-content:space-between;align-items:baseline;font-family:var(--mono);
  font-size:11px;letter-spacing:.18em;text-transform:uppercase;color:var(--ink-3);margin-bottom:9px}
.rampbar{position:relative;height:16px;border-radius:2px;background:var(--ramp);border:1px solid var(--panel-edge)}
.rampbar .zone{position:absolute;top:-1px;bottom:-1px;border-left:1px dashed rgba(255,255,255,.5)}
.rampticks{position:relative;height:34px;margin-top:2px}
.rampticks span{position:absolute;transform:translateX(-50%);font-family:var(--mono);font-size:10.5px;
  color:var(--ink-3);white-space:nowrap;top:8px}
.rampticks span::before{content:"";position:absolute;left:50%;top:-8px;width:1px;height:6px;background:var(--hair)}
.rampticks .opt{color:var(--go)}
.rampticks .opt::before{background:var(--go)}

/* ============ SECTION HEADER / DIVIDER ============ */
.section-h{font-family:var(--mono);font-size:12px;letter-spacing:.24em;text-transform:uppercase;
  color:var(--ink-3);margin:52px 0 20px;padding-bottom:12px;border-bottom:1px solid var(--hair)}
.divider{display:flex;align-items:center;gap:18px;margin:56px 0 8px}
.divider .lab{font-family:var(--mono);font-size:12px;letter-spacing:.24em;text-transform:uppercase;
  color:var(--ink-2);white-space:nowrap}
.divider .lab small{display:block;letter-spacing:.04em;text-transform:none;color:var(--ink-3);
  font-size:11px;margin-top:3px}
.divider .rule{flex:1;height:1px;background:var(--hair)}
.ref-intro{color:var(--ink-3);font-size:12.5px;line-height:1.6;max-width:66ch;margin:10px 0 18px}

/* ============ HERO ============ */
.hero{margin:46px 0 20px;border:1px solid var(--panel-edge);background:var(--panel);position:relative;
  overflow:hidden;border-radius:3px}
.hero .flag{position:absolute;top:0;left:0;font-family:var(--mono);font-size:10px;letter-spacing:.24em;
  text-transform:uppercase;color:var(--paper);background:var(--ink);padding:6px 14px;border-radius:0 0 3px 0;z-index:3}
.hero .stripe{position:absolute;inset:0;z-index:0;opacity:.9}
.hero-grid{position:relative;z-index:2;display:grid;grid-template-columns:1.35fr 1fr;gap:0}
@media (max-width:760px){.hero-grid{grid-template-columns:1fr}}
.hero-main{padding:clamp(26px,4vw,44px)}
.hero-side{padding:clamp(26px,4vw,44px);border-left:1px solid var(--panel-edge);
  background:linear-gradient(180deg,transparent,rgba(0,0,0,.02))}
@media (max-width:760px){.hero-side{border-left:0;border-top:1px solid var(--panel-edge)}}
.wb-tag{font-family:var(--mono);font-size:11px;letter-spacing:.16em;color:var(--ink-3);
  text-transform:uppercase;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.hero h2{font-size:clamp(23px,3.6vw,38px);line-height:1.06;margin:16px 0 4px;font-weight:720;letter-spacing:-.018em}
.subreach{color:var(--ink-2);font-size:14px;margin:0 0 22px}
.hero-headline{font-size:clamp(15px,2.1vw,19px);line-height:1.5;color:var(--ink);margin:22px 0 0;max-width:44ch}
.hero-panels{position:relative;z-index:2;padding:0 clamp(26px,4vw,44px) clamp(20px,3vw,30px)}

/* ============ VERDICT BADGE (shape+label, not color-only) ============ */
.badge{display:inline-flex;align-items:center;gap:9px;font-family:var(--mono);font-size:12px;
  letter-spacing:.08em;font-weight:600;padding:8px 13px;border:1.5px solid;border-radius:2px;white-space:nowrap}
.badge .ic{width:0;height:0;flex:0 0 auto}
.badge.go{color:var(--go);border-color:var(--go)}
.badge.go .ic{border-left:6px solid transparent;border-right:6px solid transparent;border-bottom:11px solid var(--go)}
.badge.caution{color:var(--caution);border-color:var(--caution)}
.badge.caution .ic{width:11px;height:11px;border-radius:50%;background:var(--caution)}
.badge.nogo{color:var(--nogo);border-color:var(--nogo)}
.badge.nogo .ic{width:10px;height:10px;background:var(--nogo)}
.badge.lg{font-size:13.5px;padding:11px 17px}

/* ============ READOUTS (hero) ============ */
.reads{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:var(--panel-edge);
  border:1px solid var(--panel-edge);margin-top:26px;border-radius:2px;overflow:hidden}
.read{background:var(--panel);padding:15px 16px}
.read .k{font-family:var(--mono);font-size:10px;letter-spacing:.14em;text-transform:uppercase;
  color:var(--ink-3);margin-bottom:7px}
.read .v{font-size:24px;font-weight:700;line-height:1.05}
.read .v small{font-size:13px;color:var(--ink-2);font-weight:500}
.read .v.temp{font-family:var(--mono)}
.read .rnote{font-family:var(--mono);font-size:10px;color:var(--ink-3);margin-top:6px}

/* ============ TSI METER on thermal ramp (pin = lateral line) ============ */
.tsi{margin-top:4px}
.tsi .lab{display:flex;justify-content:space-between;align-items:baseline;font-family:var(--mono);
  font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--ink-3);margin-bottom:10px}
.tsi .val{font-size:34px;font-weight:760;color:var(--ink);letter-spacing:-.02em}
.tsi .val small{font-size:13px;color:var(--ink-3);font-weight:500;letter-spacing:.1em}
.card-tsi{margin-top:18px}
.card-tsi .val{font-size:26px}
.meter{position:relative;height:12px;border-radius:2px;background:var(--ramp);border:1px solid var(--panel-edge)}
.meter .fillmask{position:absolute;top:-1px;bottom:-1px;right:-1px;
  background:repeating-linear-gradient(135deg,var(--paper),var(--paper) 3px,transparent 3px,transparent 6px);
  opacity:.55;border-radius:0 2px 2px 0}
.meter .pin{position:absolute;top:-5px;width:3px;height:22px;background:var(--trout);
  transform:translateX(-50%);box-shadow:0 0 6px 1px color-mix(in srgb,var(--trout) 55%,transparent),0 0 0 2px var(--panel)}
.meter .pin::after{content:"";position:absolute;top:-6px;left:50%;transform:translateX(-50%);
  border-left:5px solid transparent;border-right:5px solid transparent;border-top:7px solid var(--trout)}
.meter-scale{display:flex;justify-content:space-between;font-family:var(--mono);font-size:10px;
  color:var(--ink-3);margin-top:7px}
.tempramp{position:relative;height:8px;border-radius:2px;background:var(--ramp);margin-top:10px;
  border:1px solid var(--panel-edge)}
.tempramp .pin{position:absolute;top:-4px;width:2px;height:16px;background:var(--trout);
  transform:translateX(-50%);box-shadow:0 0 5px 1px color-mix(in srgb,var(--trout) 55%,transparent),0 0 0 1.5px var(--panel)}

/* ============ REACH CARDS ============ */
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:18px}
.card{border:1px solid var(--panel-edge);background:var(--panel);border-radius:3px;position:relative;
  overflow:hidden;display:flex;flex-direction:column}
.card.ref{opacity:.97}
.card .cartuche{position:absolute;inset:0 0 auto 0;height:96px;z-index:0;opacity:.85}
.card-body{position:relative;z-index:1;padding:20px 20px 22px;display:flex;flex-direction:column;flex:1}
.card-top{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}
.card .wb{font-family:var(--mono);font-size:10.5px;letter-spacing:.14em;text-transform:uppercase;
  color:var(--ink-3);display:flex;gap:8px;align-items:center}
.wb-inner{font-family:var(--mono);letter-spacing:.14em;text-transform:uppercase;display:inline-flex;gap:8px;align-items:center}
.conf{font-family:var(--mono);font-size:10px;letter-spacing:.08em;padding:4px 8px;border-radius:2px;
  border:1px solid;text-transform:uppercase;white-space:nowrap}
.conf.verified{color:var(--go);border-color:var(--go)}
.conf.verified::before{content:"◆ "}
.conf.ref{color:var(--ink-3);border-color:var(--hair)}
.conf.ref::before{content:"◇ "}
.card h3{font-size:20px;line-height:1.12;margin:52px 0 3px;font-weight:700;letter-spacing:-.01em}
.card .creach{color:var(--ink-2);font-size:12.5px;margin:0 0 16px}
.card .badge{margin:2px 0 0}
.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(92px,1fr));gap:14px;margin-top:18px}
.metrics .mt .k{font-family:var(--mono);font-size:10px;letter-spacing:.1em;text-transform:uppercase;
  color:var(--ink-3);margin-bottom:5px}
.metrics .mt .v{font-size:17px;font-weight:640;line-height:1.1}
.metrics .mt .v.temp{font-family:var(--mono)}
.metrics .mt .v small{font-size:12px;color:var(--ink-2);font-weight:500}
.metrics .mt .mnote{font-family:var(--mono);font-size:9.5px;color:var(--ink-3);margin-top:5px}

/* reasons (why) + caveats + lake box + outlook (at-a-glance layer) */
.why .h,.caveats .h,.outlook .h,.lakebox .h{font-family:var(--mono);font-size:10px;letter-spacing:.16em;
  text-transform:uppercase;color:var(--ink-3);margin:0 0 10px}
.why{margin:20px 0 0;padding:16px 0 0;border-top:1px solid var(--hair)}
.why ul{margin:0;padding:0;list-style:none;display:flex;flex-direction:column;gap:9px}
.why li{position:relative;padding-left:20px;font-size:13.5px;line-height:1.44;color:var(--ink)}
.why li::before{content:"";position:absolute;left:0;top:7px;width:7px;height:7px;background:var(--t10)}
.card.caution .why li::before,.card.nogo .why li::before{background:var(--t20)}
.caveats{margin:18px 0 0;padding:14px 15px;background:transparent;border:1px dashed var(--hair);border-radius:2px}
.caveats ul{margin:0;padding:0;list-style:none;display:flex;flex-direction:column;gap:7px}
.caveats li{position:relative;padding-left:19px;font-size:12.5px;line-height:1.4;color:var(--ink-2)}
.caveats li::before{content:"△";position:absolute;left:0;top:0;color:var(--caution);font-size:11px}
.lakebox{margin:16px 0 0;padding:15px;border:1px solid var(--panel-edge);border-radius:2px;
  background:linear-gradient(180deg,transparent,rgba(20,49,92,.05))}
.lakebox .h{color:var(--t05)}
.lakebox p{margin:0 0 8px;font-size:12.8px;line-height:1.5;color:var(--ink-2)}
.lakebox p:last-child{margin:0}
.lakebox .est{color:var(--caution);font-weight:600}
.outlook{margin:20px 0 0;padding-top:16px;border-top:1px solid var(--hair)}
.ol-strip{display:grid;grid-template-columns:repeat(7,1fr);gap:5px}
.ol-day{display:flex;flex-direction:column;align-items:center;gap:6px}
.ol-day .d{font-family:var(--mono);font-size:9.5px;color:var(--ink-3);letter-spacing:.02em}
.ol-day .bar{width:100%;height:34px;border-radius:1px;border:1px solid var(--panel-edge)}
.ol-day .bar.na{border-style:dashed;background:transparent}
.ol-day .t{font-family:var(--mono);font-size:10px;color:var(--ink-2)}
.ol-legend{font-family:var(--mono);font-size:10px;color:var(--ink-3);margin-top:10px;line-height:1.5}

/* ============ MORE (folded honest detail panels) ============ */
.more{margin:20px 0 0;border-top:1px solid var(--hair);padding-top:14px}
.more>summary{cursor:pointer;list-style:none;font-family:var(--mono);font-size:11px;letter-spacing:.1em;
  text-transform:uppercase;color:var(--t05);display:inline-flex;align-items:center;gap:8px}
.more>summary::-webkit-details-marker{display:none}
.more>summary::after{content:"▾";color:var(--ink-3)}
details[open].more>summary::after{content:"▴"}
.more-body{margin-top:6px}

/* ============ PORTED HONEST-DETAIL PANELS (data verbatim, restyled tokens) ============ */
.panel{border:1px solid var(--hair);border-radius:3px;padding:15px 16px;margin-top:14px;background:var(--panel)}
.panel__h{display:block;font-weight:800;font-size:1rem;letter-spacing:-.005em;margin-bottom:9px}
.panel__h::before{content:"";display:inline-block;width:7px;height:7px;background:var(--stripe);
  transform:rotate(45deg);margin-right:9px;vertical-align:2px}
.src{font-size:.76rem;color:var(--muted);line-height:1.62}
.src2{font-size:.85rem;margin:6px 0}
.prose{font-size:.9rem;margin:4px 0 12px;max-width:52em;color:var(--ink-2)}
.tipline{font-size:.92rem;margin:10px 0 4px}
.season{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 12px;margin-top:14px;padding:12px 16px;
  border-radius:3px;border:1px solid var(--glass-brd);font-size:.88rem}
.season__k{font-family:var(--mono);font-size:.64rem;letter-spacing:.1em;text-transform:uppercase;
  color:var(--muted);flex:0 0 auto}
.season--info{background:color-mix(in srgb,var(--aqua) 8%,transparent)}
.season--warn{background:color-mix(in srgb,var(--warn) 12%,transparent);border-color:var(--warn)}
.asof{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 10px;margin-top:14px}
.asof__cal{font-weight:700;font-size:1rem}
.wt{background:color-mix(in srgb,var(--aqua) 6%,var(--glass))}
.wt__grid{display:flex;flex-wrap:wrap;align-items:center;gap:14px 24px;margin:6px 0 12px}
.wt__fig{display:flex;flex-direction:column;align-items:center;min-width:104px}
.wt__val{font-size:2.1rem;font-weight:800;line-height:1;font-variant-numeric:tabular-nums}
.wt__cap{font-family:var(--mono);font-size:.64rem;letter-spacing:.08em;text-transform:uppercase;
  color:var(--muted);margin-top:4px}
.wt__box{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.wt-tag{font-size:.72rem;border:1px solid var(--glass-brd);border-radius:999px;padding:4px 11px;color:var(--muted)}
.wt-tag--obs{color:var(--aqua);border-color:color-mix(in srgb,var(--aqua) 45%,transparent)}
.wt-tag--proxy{color:var(--caution);border-color:color-mix(in srgb,var(--caution) 45%,transparent)}
.cr-chip{font-size:.78rem;font-weight:800;border-radius:999px;padding:4px 12px;color:#04120f}
.cr-safe{background:var(--go);color:#fff}.cr-caution{background:var(--warn);color:#1a0d04}
.cr-strong{background:#e88a4a;color:#1a0d04}.cr-nogo{background:var(--bad);color:#fff}
.cr-unk{background:var(--muted);color:#fff}
.wt-msg{margin-top:8px;padding:11px 14px;border-radius:6px;font-weight:700;font-size:.9rem;border:1px solid var(--glass-brd)}
.wt-msg.cr-caution{background:color-mix(in srgb,var(--warn) 14%,transparent);border-color:var(--warn);color:var(--ink)}
.wt-msg.cr-strong{background:color-mix(in srgb,var(--bad) 10%,transparent);color:var(--ink)}
.wt-msg.cr-nogo{background:color-mix(in srgb,var(--bad) 16%,transparent);border-color:var(--bad);color:var(--ink)}
.tscale{margin:10px 0 4px}
.tscale__bar{position:relative;height:14px;border-radius:6px;overflow:visible;background:var(--glass-brd)}
.tscale__zone{position:absolute;top:0;bottom:0;opacity:.9}
.tscale__zone:first-of-type{border-radius:6px 0 0 6px}
.tscale__zone:last-of-type{border-radius:0 6px 6px 0}
.tz-safe{background:color-mix(in srgb,var(--go) 55%,transparent)}
.tz-cau{background:color-mix(in srgb,var(--warn) 55%,transparent)}
.tz-bad{background:color-mix(in srgb,var(--bad) 55%,transparent)}
.tscale__mk{position:absolute;top:50%;transform:translate(-50%,-50%);display:flex;flex-direction:column;align-items:center}
.tscale__dot{width:14px;height:14px;border-radius:50%;background:var(--stripe);
  box-shadow:0 0 0 3px var(--abyss),0 0 12px var(--stripe)}
.tscale__val{font-family:var(--mono);font-size:.66rem;font-weight:800;margin-top:16px;white-space:nowrap;color:var(--ink)}
.tscale__lbls{display:flex;justify-content:space-between;margin-top:22px;font-family:var(--mono);font-size:.6rem;color:var(--muted)}
.gate__top{display:flex;align-items:center;gap:14px;margin:6px 0 4px}
.gate__count{font-size:1.7rem;font-weight:800;line-height:1;font-variant-numeric:tabular-nums;flex:0 0 auto}
.gate__of{font-size:.92rem;color:var(--muted);font-weight:600}
.gate__seg{display:flex;gap:5px;flex:1;min-width:110px}
.gate__seg span{height:8px;flex:1;border-radius:4px;background:var(--glass-brd)}
.gate__seg span.on{background:var(--go)}
.gate__head{font-weight:700;font-size:.88rem;margin:6px 0 2px;color:var(--muted)}
.gate__head.allok{color:var(--go)}
.gate__list{list-style:none;margin:8px 0 0;padding:0}
.gate__list li{display:flex;align-items:center;flex-wrap:wrap;gap:4px 12px;padding:10px 8px;border-top:1px solid var(--glass-brd)}
.gate__list li:first-child{border-top:none}
li.g-no{background:color-mix(in srgb,var(--bad) 8%,transparent);border-radius:6px;border-top-color:transparent}
li.g-no+li{border-top-color:transparent}
.gmark{width:19px;height:19px;border-radius:50%;position:relative;flex:0 0 19px}
.g-ok .gmark{background:var(--go)}
.g-ok .gmark::after{content:"";position:absolute;left:6px;top:3px;width:5px;height:9px;
  border:solid #fff;border-width:0 2px 2px 0;transform:rotate(43deg)}
.g-unk .gmark{box-shadow:inset 0 0 0 2px var(--muted)}
.g-unk .gmark::after{content:"?";position:absolute;left:50%;top:50%;transform:translate(-50%,-54%);
  color:var(--muted);font-size:.78rem;font-weight:800}
.g-unk .glabel{color:var(--muted)}
.g-no .gmark{box-shadow:inset 0 0 0 2px var(--bad)}
.g-no .gmark::before,.g-no .gmark::after{content:"";position:absolute;left:50%;top:50%;width:9px;height:2px;background:var(--bad)}
.g-no .gmark::before{transform:translate(-50%,-50%) rotate(45deg)}
.g-no .gmark::after{transform:translate(-50%,-50%) rotate(-45deg)}
.glabel{font-weight:700;font-size:.92rem}
.g-no .glabel{color:var(--bad)}
.gdetail{margin-left:auto;color:var(--muted);font-size:.8rem;text-align:right;max-width:62%}
.tips{margin:8px 0 0;padding-left:20px}.tips li{margin:7px 0;line-height:1.6}
.stages{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:9px}
.stage{border:1px solid var(--glass-brd);border-radius:6px;overflow:hidden;padding-bottom:9px;
  background:color-mix(in srgb,var(--deep2) 35%,transparent)}
.stage.on{border-color:var(--stripe);box-shadow:0 0 0 1px var(--stripe)}
.stage__bar{height:6px}
.stage__hd{padding:8px 10px 4px;font-weight:800;font-size:.88rem}
.stage__now{display:inline-block;font-size:.6rem;background:var(--stripe);color:#180207;border-radius:4px;
  padding:1px 7px;margin-left:5px;vertical-align:middle;letter-spacing:.05em;font-weight:800}
.stage__row{font-size:.72rem;line-height:1.45;padding:2px 10px}
.stage__row b{color:var(--muted);font-weight:700}
.stage__how{font-size:.76rem;font-weight:700;padding:6px 10px 0;line-height:1.5}
.outlook__stat{font-size:.9rem;margin-bottom:6px}
.outlook__stats{margin:4px 0 10px;display:flex;flex-direction:column;gap:2px}
.okv{display:flex;gap:12px;align-items:baseline;padding:6px 0;border-top:1px solid var(--glass-brd);font-size:.9rem}
.okv:first-child{border-top:none}
.ok-k{flex:0 0 10em;color:var(--muted);font-family:var(--mono);font-size:.7rem;letter-spacing:.04em}
.ok-v{font-variant-numeric:tabular-nums}
.spark{display:block;margin:2px 0}
.spk-grid{stroke:var(--glass-brd);stroke-width:1}
.spk-area{fill:var(--aqua);opacity:.14}
.spk-act{stroke:var(--aqua);fill:none;stroke-width:2.4;stroke-linejoin:round;stroke-linecap:round}
.spk-fc{stroke:var(--aqua);fill:none;stroke-width:2.4;stroke-dasharray:5 4;opacity:.85;stroke-linecap:round}
.spk-today{stroke:var(--muted);stroke-width:1;stroke-dasharray:3 3}
.spk-dot{fill:var(--stripe)}
.depth{margin:8px 0 4px;max-width:420px}
.dl-name{fill:var(--ink);font-weight:700;font-size:13px}
.dl-how{fill:var(--ink);opacity:.82;font-size:11px}
.dl-tgt{fill:var(--stripe);font-weight:800;font-size:12px}
.flow{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px;margin-top:8px}
.fi{border:1px solid var(--glass-brd);border-radius:6px;padding:10px 12px;
  background:color-mix(in srgb,var(--deep2) 35%,transparent);display:flex;flex-direction:column;gap:2px}
.fi-k{font-family:var(--mono);font-size:.66rem;letter-spacing:.04em;color:var(--muted)}
.fi-v{font-size:.98rem;font-weight:700;font-variant-numeric:tabular-nums;line-height:1.4}
.trace{margin-top:11px;padding:10px 14px;border-left:3px solid var(--stripe);
  background:color-mix(in srgb,var(--stripe) 8%,transparent);border-radius:0 6px 6px 0;font-size:.85rem}
.alert{background:color-mix(in srgb,var(--bad) 14%,transparent);border:1px solid var(--bad);border-radius:6px;padding:11px 14px;font-weight:700}
.ok{background:color-mix(in srgb,var(--go) 12%,transparent);border:1px solid var(--go);border-radius:6px;padding:11px 14px}
.warnbar{background:color-mix(in srgb,var(--warn) 13%,transparent);border:1px solid var(--warn);
  border-radius:6px;padding:11px 14px;margin-top:12px;font-size:.88rem}
.mapwrap{overflow-x:auto;color:var(--ink)}
.mapwrap svg{min-width:520px}
.tablewrap{overflow-x:auto;margin-top:8px}
table.ind{border-collapse:collapse;width:100%;font-size:.82rem;min-width:560px;font-variant-numeric:tabular-nums}
table.ind th,table.ind td{border:1px solid var(--glass-brd);padding:8px 10px;text-align:left;vertical-align:top}
table.ind th{background:color-mix(in srgb,var(--deep2) 45%,transparent);font-family:var(--mono);
  font-size:.72rem;letter-spacing:.04em}
.ik{font-weight:800;white-space:nowrap}
table.recon{min-width:420px}
.cav li{font-size:.85rem;margin:5px 0}
.chg{margin:4px 0 0;padding-left:2px;list-style:none}
.chg li{font-size:.9rem;margin:7px 0;padding-left:17px;position:relative}
.chg li::before{content:"";position:absolute;left:2px;top:.55em;width:6px;height:6px;background:var(--stripe);transform:rotate(45deg)}
.subpanel>summary{cursor:pointer;list-style:none}
.subpanel>summary::-webkit-details-marker{display:none}
.subpanel>summary .panel__h{margin-bottom:0;display:inline-block}
.subpanel>summary::after{content:"開く";font-family:var(--mono);font-size:.66rem;color:var(--muted);
  float:right;border:1px solid var(--glass-brd);border-radius:999px;padding:3px 11px}
details[open].subpanel>summary::after{content:"閉じる"}
details[open].subpanel>summary{margin-bottom:8px}

/* ============ FOOTER ============ */
footer{margin:70px 0 60px;padding-top:26px;border-top:1px solid var(--hair);color:var(--ink-3);
  font-size:12.5px;line-height:1.65}
footer .fnote{max-width:66ch;margin:0 0 14px}
footer strong{color:var(--ink-2);font-weight:600}
footer .disc{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));margin:16px 0}
footer .disc>div{padding:14px 15px;border:1px solid var(--hair);border-radius:3px;background:var(--panel);line-height:1.55}
footer .disc h4{font-family:var(--mono);font-size:10.5px;letter-spacing:.14em;text-transform:uppercase;color:var(--go);margin:0 0 7px}
footer .disc .warn-accent{color:var(--caution);font-weight:600}
footer .fmeta{font-family:var(--mono);font-size:11px;letter-spacing:.06em;display:flex;gap:20px;flex-wrap:wrap;margin-top:16px}

/* ============ MOTION / RESPONSIVE ============ */
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important;scroll-behavior:auto!important}}
@media (max-width:560px){.dates{margin-left:0}.reads{grid-template-columns:1fr}}
@media print{
  :root{--paper:#fff;--panel:#fff;--ink:#111;--ink-2:#333;--ink-3:#555;--hair:#bbb;--panel-edge:#bbb}
  #field,#veil{display:none}
  .card,.hero,.panel{break-inside:avoid}
  details.more,details.subpanel{}
  details.more>.more-body{display:block!important}
}
</style>"""


# --------------------------------------------------------------------------- #
# JS — 側線メーターは静的(CSS)、JSは水温着色・アウトルック生成・テーマ切替・水温フィールド背景
# --------------------------------------------------------------------------- #
JS = """<script>
(function(){
  "use strict";
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion:reduce)").matches;
  var root=document.documentElement;

  var stops=[[0,15,47,99],[5,20,98,144],[10,21,158,139],[16,87,181,107],[20,220,160,50],[24,168,31,28]];
  function tempColor(t){
    if(t<=stops[0][0]) return "rgb("+stops[0][1]+","+stops[0][2]+","+stops[0][3]+")";
    if(t>=stops[stops.length-1][0]){var s=stops[stops.length-1];return "rgb("+s[1]+","+s[2]+","+s[3]+")";}
    for(var i=0;i<stops.length-1;i++){
      var a=stops[i],b=stops[i+1];
      if(t>=a[0]&&t<=b[0]){
        var f=(t-a[0])/(b[0]-a[0]);
        return "rgb("+Math.round(a[1]+(b[1]-a[1])*f)+","+Math.round(a[2]+(b[2]-a[2])*f)+","+Math.round(a[3]+(b[3]-a[3])*f)+")";
      }
    }
    return "rgb(120,130,140)";
  }

  var strips=document.querySelectorAll(".ol-strip");
  for(var si=0;si<strips.length;si++){
    (function(el){
      var data;
      try{ data=JSON.parse(el.getAttribute("data-outlook")); }catch(e){ return; }
      for(var j=0;j<data.length;j++){
        var row=data[j], day=row[0], temp=row[1], verd=row[2];
        var cell=document.createElement("div"); cell.className="ol-day";
        if(temp===null||verd==="na"){
          cell.innerHTML='<span class="d">'+day+'</span>'+
            '<span class="bar na" title="\\u4e88\\u5831\\u6b20\\u6e2c"></span>'+
            '<span class="t">\\u2014</span>'+
            '<span class="d" aria-hidden="true">\\u2014</span>';
          el.appendChild(cell); continue;
        }
        var col=tempColor(temp);
        var glyph = verd==="go" ? "\\u25b2" : (verd==="cau" ? "\\u25cf" : "\\u25a0");
        cell.innerHTML='<span class="d">'+day+'</span>'+
          '<span class="bar" style="background:'+col+'" title="'+temp+'\\u2103"></span>'+
          '<span class="t">'+temp+'</span>'+
          '<span class="d" aria-hidden="true">'+glyph+'</span>';
        el.appendChild(cell);
      }
    })(strips[si]);
  }

  var btn=document.getElementById("themeBtn");
  if(btn){ btn.addEventListener("click",function(){
    var cur=root.getAttribute("data-theme");
    var sysDark=window.matchMedia&&window.matchMedia("(prefers-color-scheme:dark)").matches;
    if(!cur) cur = sysDark ? "dark" : "light";
    root.setAttribute("data-theme", cur==="dark" ? "light" : "dark");
  }); }

  var cv=document.getElementById("field"), ctx=cv&&cv.getContext&&cv.getContext("2d");
  if(!ctx) return;
  var W,H,DPR;
  function isDark(){
    var a=root.getAttribute("data-theme");
    if(a) return a==="dark";
    return window.matchMedia&&window.matchMedia("(prefers-color-scheme:dark)").matches;
  }
  function resize(){
    DPR=Math.min(window.devicePixelRatio||1,2);
    W=cv.width=Math.floor(innerWidth*DPR); H=cv.height=Math.floor(innerHeight*DPR);
    cv.style.width=innerWidth+"px"; cv.style.height=innerHeight+"px";
  }
  var blobs=[
    {t:3 ,x:.14,y:.16,r:.52,px:0.011,py:0.007,a:0},
    {t:12,x:.72,y:.30,r:.60,px:-0.008,py:0.010,a:1.6},
    {t:16,x:.30,y:.66,r:.55,px:0.009,py:-0.008,a:3.1},
    {t:20,x:.84,y:.78,r:.48,px:-0.010,py:-0.006,a:4.4},
    {t:24,x:.55,y:.95,r:.42,px:0.006,py:0.009,a:5.7},
    {t:7 ,x:.92,y:.10,r:.40,px:-0.007,py:0.008,a:2.2}
  ];
  function draw(time){
    var dark=isDark();
    ctx.clearRect(0,0,W,H);
    ctx.globalCompositeOperation = dark ? "screen" : "multiply";
    for(var i=0;i<blobs.length;i++){
      var b=blobs[i];
      var tt = reduce ? 0 : time*0.00006;
      var cx=(b.x + Math.sin(tt*b.px*60+b.a)*0.06)*W;
      var cy=(b.y + Math.cos(tt*b.py*60+b.a)*0.06)*H;
      var rad=b.r*Math.max(W,H)*0.62;
      var col=tempColor(b.t);
      var g=ctx.createRadialGradient(cx,cy,0,cx,cy,rad);
      var alpha = dark ? 0.42 : 0.34;
      g.addColorStop(0, col.replace("rgb","rgba").replace(")",","+alpha+")"));
      g.addColorStop(1, col.replace("rgb","rgba").replace(")",",0)"));
      ctx.fillStyle=g; ctx.beginPath(); ctx.arc(cx,cy,rad,0,Math.PI*2); ctx.fill();
    }
    ctx.globalCompositeOperation="source-over";
  }
  resize(); window.addEventListener("resize",resize);
  if(reduce){ draw(0); }
  else{ (function loop(ts){ draw(ts); requestAnimationFrame(loop); })(0); }
})();
</script>"""

FIELD = ('<canvas id="field" aria-hidden="true"></canvas>'
         '<div id="veil" aria-hidden="true"></div>')


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

    # 描画段も区間単位で隔離する: 1区間のカード/ヒーロー描画例外が全10区間+ページ全体を
    # 落とさないよう、reach_report の隔離(上のループ)と対称に per-item で握る。
    def _safe_card(it):
        try:
            return _card(it, run_date)
        except Exception as exc:  # noqa: BLE001 — 1区間の描画失敗を隔離（他区間と全体は続行）
            logger.warning("reach %s のカード描画に失敗（スキップ）: %s", it["reach"]["label"], exc)
            return ""

    def _safe_hero(it):
        try:
            return _hero(it, run_date)
        except Exception as exc:  # noqa: BLE001 — ヒーロー描画失敗時はカードとして下段へ回す
            logger.warning("reach %s のヒーロー描画に失敗（カードへ降格）: %s",
                           it["reach"]["label"], exc)
            return None

    hero_html = ""
    if hero_item:
        hero_html = _safe_hero(hero_item) or ""
        if not hero_html:                       # ヒーロー描画失敗 → グリッド側で拾う
            grid_verified = ([hero_item] + grid_verified
                             if hero_item["v"].source_confidence == "verified"
                             else grid_verified)
            grid_refs = (grid_refs if hero_item["v"].source_confidence == "verified"
                         else [hero_item] + grid_refs)

    grid = ""
    if grid_verified:
        grid += '<div class="section-h">VERIFIED 区間 — 確信ある判定</div>'
        grid += ('<div class="grid">'
                 + "".join(_safe_card(it) for it in grid_verified) + '</div>')
    if grid_refs:
        grid += ('<div class="divider"><span class="lab">参考区間 REFERENCE'
                 '<small>source_confidence 格下げ — 確信ある GO は verified のみ</small></span>'
                 '<span class="rule"></span></div>'
                 '<p class="ref-intro">以下は公式データ源の実在確認が『参考』レベルの区間・湖です。'
                 '物理データ（気象・水位・水温・ダム放流）と季節の注意書きは正直に出しますが、'
                 '確信を持った『行くべし』は出しません（システムが自動で様子見へ格下げ済み）。'
                 '正確な期間・区間・遊漁ルールは各漁協・現地にご確認ください。</p>')
        grid += ('<div class="grid">'
                 + "".join(_safe_card(it) for it in grid_refs) + '</div>')

    page_asof = max(as_ofs) if as_ofs else None
    # Static snapshot: show the DATA date and the PAGE-GENERATION time separately so a
    # frozen page can't masquerade as "現在". No decaying "本日反映" badge here.
    page_fresh = guide.freshness(page_asof, run_date)
    fresh_label = _static_fresh_label(page_fresh)

    masthead = (
        '<header class="masthead">'
        '<div class="brandrow"><div>'
        '<p class="eyebrow">非公式 意思決定レーダー · 公開データのみ</p>'
        '<h1 class="title">群馬 ニジマスレーダー'
        '<span class="en">GUNMA RAINBOW-TROUT RADAR — THERMAL CARTOGRAPHY</span></h1></div>'
        '<div class="mh-tools"><span class="seal" aria-hidden="true">冷水判断</span>'
        '<button class="toggle" id="themeBtn" type="button" aria-label="表示テーマを切替">'
        '◐ THEME</button></div></div>'
        '<p class="lede">週末遠征の<b>無駄足を減らす</b>ための、区間ごとの釣行判断。'
        'このサイトは水温を判定の主軸に置きます —— ニジマスは<b>10–16℃で活発</b>、'
        '<b>20℃で摂餌停止</b>、<b>24℃で致死</b>。ページ全体が一枚の水温地図で、'
        '各区間はその温度フィールド上の一点として置かれます。水温は気温からの推定'
        '（実測ではない）で、確信ある『行くべし』は verified 区間のみです。</p>'
        '<div class="metabar"><div class="legend">'
        '<span class="lg"><span class="gl go"></span>行くべし GO</span>'
        '<span class="lg"><span class="dot cau"></span>様子見 CAUTION</span>'
        '<span class="lg"><span class="sq no"></span>見送り NO-GO</span>'
        '<span class="note">— 水温が判定の主軸。verified 区間のみ確信ある GO。</span></div>'
        f'<div class="dates"><span>DATA <b>{guide.jp_date(page_asof, with_year=True)}</b> '
        f'<span class="fresh fresh--{page_fresh["level"]}">{fresh_label}</span></span>'
        f'<span>ページ生成 <b>{generated}</b></span></div></div>'
        + _rampkey() + '</header>')

    footer = (
        '<footer>'
        '<p class="fnote"><strong>このサイトについて。</strong>'
        '群馬県のニジマス釣行判断を、公開データ（気象アメダス・漁協の放流告知・河川水位・ダム放流・'
        '標高）だけで組み立てた<strong>非公式ツール</strong>です。判定は断定ではなく確率の提示です。</p>'
        '<div class="disc">'
        '<div><h4>データ源</h4>気象庁AMeDAS／週間予報 · Yahoo!天気・災害（川の水位） · '
        '国土交通省 利根川ダム統合管理 · 各漁協/各湖の公式情報 · 釣況ブログ→Gemini。'
        '会員制/有償データは使いません。</div>'
        '<div><h4>水温は推定（実測ではない）</h4>表示の水温は気温からの'
        '<span class="warn-accent">未較正プロキシ</span>で、渓流・湖の実測ではありません。'
        '現場報告があればそちらを優先し「ブログ記載」と明示します。湖は標高補正付きの推定です。</div>'
        '<div><h4>信頼度と参考格下げの意味</h4><strong>◆ verified</strong> のみ確信を持った'
        '『行くべし』を出します。<strong>◇ 参考</strong> 区間は物理データと注意書きは出しますが、'
        '確信GOは自動で様子見へ格下げします。</div>'
        '<div><h4>キャッチ&amp;リリースの倫理</h4>水温20℃で摂餌停止、24℃で致死域。'
        '<span class="warn-accent">高水温期のやり取りは魚に致命的</span>です。'
        '迷ったら釣らない —— それも一つの正しい判定です。</div></div>'
        '<div class="fmeta"><span>THERMAL CARTOGRAPHY · 水温地図</span>'
        f'<span>データ基準日 {guide.jp_date(page_asof, with_year=True)}</span>'
        f'<span>ページ生成 {generated}（JST）</span>'
        '<span>唯一の色体系 = 水温 0–24℃</span>'
        '<span>© 群馬 ニジマスレーダー · 非公式</span></div>'
        '<p class="fnote">ページ生成がデータ基準日より大きく古い場合、自動更新が止まっている'
        '可能性があります。増水・危険の確認は必ず公式の防災情報を、遊漁券・解禁期間・C&amp;Rルールは'
        '各漁協・各釣場の案内に従ってください。</p></footer>')

    favicon = ('<link rel="icon" href="data:image/svg+xml,'
               '<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22>'
               '<text y=%22.9em%22 font-size=%2290%22>🎣</text></svg>">')
    head = ('<meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta name="generated" content="{generated}+09:00">'
            '<meta name="description" content="群馬のニジマス釣り（神流川・利根川・吾妻川・渡良瀬川と'
            '群馬の湖）の〈行くべし/様子見/見送り〉を、気象・水位・水温プロキシ・ダム放流・釣況の'
            '公開データだけで区間ごとに毎日推定する非公式レーダー。水温を主軸に置く水温地図。">'
            f'{favicon}<title>群馬 ニジマスレーダー — 水温地図</title>')
    content = '<div class="wrap">' + masthead + hero_html + grid + footer + '</div>'
    if full_document:
        return ('<!doctype html><html lang="ja"><head>' + head + CSS + '</head><body>'
                + FIELD + content + JS + '</body></html>')
    return head + CSS + FIELD + content + JS


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
