"""The mascot: a hand-drawn SVG rainbow trout (ニジマス) whose expression + colour react
to the verdict mood. Pure string builder — no Streamlit dependency, easy to preview.

Moods: ecstatic (GO 好条件) / neutral (CAUTION) / grumpy (NO_GO 増水・濁り・高水温) /
sleepy (stale / no data). Signature = the pink lateral stripe + fine black spots.
The trout faces right; its tail wiggles (SMIL) and the fish bobs via CSS class `fish-bob`.
"""
from __future__ import annotations

_MOODS = {
    "ecstatic": dict(back1="#5b8fb0", back2="#7fae86", belly="#eef4f0", fin="#6b8f74",
                     stripe="#e57373", glow=True, eye="happy", deco="sparkle"),
    "neutral": dict(back1="#6f8a9c", back2="#7f9182", belly="#e8ecea", fin="#7f9182",
                    stripe="#c98a8a", glow=False, eye="open", deco="none"),
    "grumpy": dict(back1="#7a6f5f", back2="#6d5f4f", belly="#dcd4c8", fin="#6d5f4f",
                   stripe="#a97b7b", glow=False, eye="angry", deco="anger"),
    "sleepy": dict(back1="#9aa8a2", back2="#93a39a", belly="#e9ede9", fin="#93a39a",
                   stripe="#c2a3a3", glow=False, eye="closed", deco="sleep"),
}


def _eye(kind: str) -> str:
    if kind == "happy":
        return ('<path d="M205,63 Q212,57 219,63" fill="none" stroke="#20303a" '
                'stroke-width="2.4" stroke-linecap="round"/>')
    if kind == "angry":
        return ('<line x1="204" y1="57" x2="219" y2="63" stroke="#20303a" stroke-width="2.6" '
                'stroke-linecap="round"/><circle cx="212" cy="65" r="3.4" fill="#20303a"/>')
    if kind == "closed":
        return ('<path d="M205,64 Q212,68 219,64" fill="none" stroke="#26343a" '
                'stroke-width="2.4" stroke-linecap="round"/>')
    return ('<circle cx="212" cy="64" r="5" fill="#fff"/>'
            '<circle cx="213" cy="64" r="2.7" fill="#222"/>')


def _deco(kind: str) -> str:
    if kind == "sparkle":
        return ('<text x="30" y="40" font-size="22" class="fish-spark">✨</text>'
                '<text x="210" y="30" font-size="18" class="fish-spark" style="animation-delay:.4s">✨</text>'
                '<text x="120" y="20" font-size="16" class="fish-spark" style="animation-delay:.8s">✨</text>')
    if kind == "anger":
        return '<text x="40" y="36" font-size="26">💧</text>'
    if kind == "sleep":
        return ('<text x="150" y="34" font-size="18" class="fish-spark">💤</text>'
                '<text x="176" y="20" font-size="13" class="fish-spark" style="animation-delay:.6s">z</text>')
    return ""


def _spots() -> str:
    """Fine black spots scattered on the upper body (rainbow-trout signature)."""
    pts = [(96, 62), (116, 58), (140, 60), (164, 64), (188, 66),
           (108, 72), (132, 70), (156, 72), (180, 74), (150, 88)]
    return "".join(f'<circle cx="{x}" cy="{y}" r="2.1" fill="#2b3a2f" opacity="0.55"/>'
                   for x, y in pts)


def trout_svg(mood: str = "neutral") -> str:
    m = _MOODS.get(mood, _MOODS["neutral"])
    glow = ('<filter id="stripeglow"><feGaussianBlur stdDeviation="2.2" result="b"/>'
            '<feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>')
    stripe_filter = ' filter="url(#stripeglow)"' if m["glow"] else ""
    return f"""
<svg viewBox="0 0 260 150" width="100%" height="100%" xmlns="http://www.w3.org/2000/svg" aria-label="trout-{mood}">
  <defs>
    <linearGradient id="troutBody" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="{m['back1']}"/>
      <stop offset="0.5" stop-color="{m['back2']}"/>
      <stop offset="1" stop-color="{m['belly']}"/>
    </linearGradient>
    {glow}
  </defs>
  {_deco(m['deco'])}
  <!-- tail (wiggles) -->
  <g>
    <animateTransform attributeName="transform" type="rotate"
       values="-7 60 78; 7 60 78; -7 60 78" dur="0.9s" repeatCount="indefinite"/>
    <path d="M62,78 L18,52 L34,78 L18,104 Z" fill="{m['fin']}"/>
  </g>
  <!-- fins -->
  <path d="M116,48 L150,32 L160,56 Z" fill="{m['fin']}"/>            <!-- dorsal -->
  <path d="M76,60 L92,55 L94,66 Z" fill="{m['fin']}"/>              <!-- adipose -->
  <path d="M150,96 L176,114 L188,96 Z" fill="{m['fin']}" opacity="0.92"/>  <!-- pectoral -->
  <!-- body -->
  <path d="M60,78 C92,38 180,34 224,64 C234,70 237,74 238,78
           C237,82 234,86 224,92 C180,122 92,118 60,78 Z" fill="url(#troutBody)"
        stroke="#2f3f34" stroke-width="1.2" stroke-opacity="0.35"/>
  {_spots()}
  <!-- pink lateral stripe (rainbow-trout signature) -->
  <path d="M66,80 C110,70 170,70 226,78" fill="none" stroke="{m['stripe']}"
        stroke-width="7" stroke-linecap="round" opacity="0.85"{stripe_filter}/>
  <!-- gill + mouth + eye -->
  <path d="M198,58 Q192,78 198,96" fill="none" stroke="#2f3f34" stroke-width="1.2" stroke-opacity="0.4"/>
  <path d="M234,84 Q238,84 239,82" fill="none" stroke="#2f3f34" stroke-width="1.6" stroke-linecap="round"/>
  {_eye(m['eye'])}
</svg>
"""
