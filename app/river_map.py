"""Schematic river map: a river's gauges (上流→下流) coloured by flood stage.

Deliberately a schematic, NOT a geographic map: weather/水温 come from a SINGLE AMeDAS
station so they are shared across the reach — only the per-gauge water stage differs,
which is exactly what this shows. The reach's own gauge is highlighted (mark=True).
Pure string builder, no Streamlit dep.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List

_STAGE_COLOR = {0: "#43a047", 1: "#fbc02d", 2: "#fb8c00", 3: "#e53935", 4: "#e53935", 5: "#b71c1c"}
_TREND_ARROW = {"上昇": "↑", "下降": "↓", "変化なし": "→"}


def _stage_color(sev: int, has_status: bool) -> str:
    return _STAGE_COLOR.get(sev, "#9e9e9e") if has_status else "#9e9e9e"


def river_map_svg(points: List[Dict[str, Any]], *, river: str = "河川",
                  upstream: str = "上流", downstream: str = "下流") -> str:
    """`points`: ordered up→downstream, each {name, status, trend, sev, mark, zone_label}."""
    w, h = 900, 300
    x0, x1 = 80, 820
    n = max(1, len(points))
    xs = [x0 + (x1 - x0) * (i / (n - 1) if n > 1 else 0.0) for i in range(n)]
    ys = [155 + 24 * math.sin(i * 1.05) for i in range(n)]

    band = " ".join(f"{x:.0f},{y:.0f}" for x, y in zip(xs, ys))
    river_band = (f'<polyline points="{xs[0] - 40:.0f},{ys[0]:.0f} {band} '
                  f'{xs[-1] + 40:.0f},{ys[-1]:.0f}" fill="none" stroke="#4fc3f7" '
                  f'stroke-width="16" stroke-linecap="round" stroke-linejoin="round" opacity="0.75"/>')

    marks = []
    for pt, x, y in zip(points, xs, ys):
        has = pt.get("status") is not None
        col = _stage_color(pt.get("sev", 0), has)
        status = pt.get("status") or "取得失敗"
        arrow = _TREND_ARROW.get(pt.get("trend") or "", "")
        ayu = "🎣 " if pt.get("mark") else ""
        zlabel = pt.get("zone_label") or ""
        zone_text = (f'<text x="{x:.0f}" y="{y + 48:.0f}" text-anchor="middle" '
                     f'font-size="13" font-weight="700" fill="{col}">{zlabel}</text>') if zlabel else ""
        marks.append(
            f'<circle cx="{x:.0f}" cy="{y:.0f}" r="13" fill="{col}" stroke="#fff" stroke-width="3"/>'
            f'<text x="{x:.0f}" y="{y - 24:.0f}" text-anchor="middle" font-size="14" '
            f'font-weight="700" fill="currentColor">{ayu}{pt["name"]}</text>'
            f'<text x="{x:.0f}" y="{y + 30:.0f}" text-anchor="middle" font-size="13" '
            f'fill="{col}">{status}{arrow}</text>'
            + zone_text
        )

    legend_items = [("平常", "#43a047"), ("注意", "#fb8c00"), ("危険", "#e53935"), ("取得失敗", "#9e9e9e")]
    legend = "".join(
        f'<circle cx="{100 + i * 150}" cy="278" r="7" fill="{c}"/>'
        f'<text x="{112 + i * 150}" y="283" font-size="12" fill="currentColor">{lbl}</text>'
        for i, (lbl, c) in enumerate(legend_items))

    return f"""
<svg viewBox="0 0 {w} {h}" width="100%" xmlns="http://www.w3.org/2000/svg" aria-label="{river}マップ">
  <text x="40" y="40" font-size="13" fill="currentColor" opacity="0.7">▲ {upstream}</text>
  <text x="{w - 40}" y="40" text-anchor="end" font-size="13" fill="currentColor" opacity="0.7">{downstream}▼</text>
  {river_band}
  {''.join(marks)}
  {legend}
</svg>
"""
