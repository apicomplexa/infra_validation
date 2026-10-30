"""Tiny layout helper for the diagrams of docs/workflow.qmd.

Boxes sit at the coordinates a diagram gives them, text is measured so a line
that does not fit its box is reported, and arrows carry labels.  The output is
inline SVG in the style of docs/architecture.qmd (classes of
docs/architecture.css).  Line markup: "!" title, "`" mono, "~" against (after
« || »), "?" question text; otherwise sub text.

Text is measured with Pillow and Liberation Sans (metric-compatible with
Arial) when both are available; without them the diagrams are still written,
only unchecked.
"""
from html import escape

try:
    from PIL import ImageFont

    _F = "/usr/share/fonts/liberation-sans-fonts/LiberationSans-{}.ttf"
    FONTS = {
        "t": ImageFont.truetype(_F.format("Bold"), 13),
        "s": ImageFont.truetype(_F.format("Regular"), 11.5),
        "x": ImageFont.truetype(_F.format("Regular"), 11.5),
        "q": ImageFont.truetype(_F.format("Regular"), 12),
        "m": ImageFont.truetype("/usr/share/fonts/liberation-mono-fonts/LiberationMono-Regular.ttf", 11),
        "lbl": ImageFont.truetype(_F.format("Regular"), 11),
    }
except (ImportError, OSError):
    FONTS = None

LH = {"t": 19, "s": 16, "x": 16, "q": 16.5, "m": 15.5}
PAD = 12
KIND = {"data": "r-data", "check": "r-check", "lm": "r-lm", "cat": "r-cat", "q": "r-q",
        "ok": "r-ok", "no": "r-no", "note": "r-note", "ref": "r-ref", "plain": "r-plain"}
ERRORS = []


def width(text, cls):
    """Rendered width in px; 0 when no font is available (nothing is checked)."""
    if FONTS is None:
        return 0.0
    return FONTS[cls].getlength(text) * 1.06   # browsers' sans is a little wider


def parse(line):
    for p, c in (("!", "t"), ("`", "m"), ("~", "x"), ("?", "q")):
        if line.startswith(p):
            return c, line[1:]
    return "s", line


class Box:
    def __init__(self, x, y, w, h):
        self.x, self.y, self.w, self.h = x, y, w, h

    cx = property(lambda s: s.x + s.w / 2)
    cy = property(lambda s: s.y + s.h / 2)
    bottom = property(lambda s: s.y + s.h)
    right = property(lambda s: s.x + s.w)

    def r(self, dy=0): return (self.x + self.w, self.cy + dy)
    def l(self, dy=0): return (self.x, self.cy + dy)
    def t(self, dx=0): return (self.cx + dx, self.y)
    def b(self, dx=0): return (self.cx + dx, self.y + self.h)


class Diagram:
    def __init__(self, name, width, label):
        self.name, self.W, self.label = name, width, label
        self.parts, self.maxy = [], 0

    def _grow(self, y):
        self.maxy = max(self.maxy, y)

    def box(self, x, y, w, lines, kind="data", href=None, h=None, anchor="start", valign="middle"):
        lines = [lines] if isinstance(lines, str) else list(lines)
        if href:
            lines[0] = lines[0] + " ↗"
        hh = 6 + sum(LH[parse(l)[0]] for l in lines) + 10
        h = max(h or 0, hh)
        b = Box(x, y, w, h)
        rx = 3 if kind == "q" else 6
        out = [f'<rect class="box {KIND[kind]}" x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{rx}"/>']
        cur = y + 6 + ((h - hh) / 2 if valign == "middle" else 0)
        for line in lines:
            cls, text = parse(line)
            cur += LH[cls]
            need = width(text, cls) + 2 * PAD
            if need > w + 0.5:
                ERRORS.append(f"{self.name}: «{text}» needs {need:.0f} > {w}")
            tx = x + PAD if anchor == "start" else x + w / 2
            out.append(f'<text class="{cls}" x="{tx:.1f}" y="{cur - 4:.1f}"'
                       + (' text-anchor="middle"' if anchor != "start" else "") + f">{escape(text)}</text>")
        svg = "\n".join(out)
        if href:
            svg = f'<a href="#{href}">\n{svg}\n</a>'
        self.parts.append(svg)
        self._grow(y + h)
        return b

    def group(self, x, y, w, h, label, kind="check", stack=0):
        out = []
        for i in range(stack, 0, -1):
            out.append(f'<rect class="grp {KIND[kind]}" x="{x + 6 * i}" y="{y + 6 * i}" width="{w}" height="{h}" rx="10" opacity="{0.55 - 0.15 * i}"/>')
        out.append(f'<rect class="grp {KIND[kind]}" x="{x}" y="{y}" width="{w}" height="{h}" rx="10"/>')
        out.append(f'<text class="g" x="{x + 14}" y="{y + 20}">{escape(label)}</text>')
        if width(label, "lbl") + 28 > w:
            ERRORS.append(f"{self.name}: group label «{label}» too long")
        self.parts.insert(0, "\n".join(out))
        self._grow(y + h + 6 * stack)

    def text(self, x, y, s, anchor="start", cls="lbl"):
        a = "" if anchor == "start" else f' text-anchor="{anchor}"'
        self.parts.append(f'<text class="{cls}" x="{x:.1f}" y="{y:.1f}"{a}>{escape(s)}</text>')
        self._grow(y + 4)

    def arrow(self, pts, label=None, at=None, anchor=None, dashed=False, head=True):
        d = "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in pts)
        cls = "arr pol" if dashed else "arr"
        m = f' marker-end="url(#ah-{self.name})"' if head else ""
        self.parts.append(f'<path class="{cls}" d="{d}"{m}/>')
        if label:
            if at is None:
                (x1, y1), (x2, y2) = pts[0], pts[1]
                if abs(y1 - y2) < 1:            # horizontal: above the middle
                    at, anchor = ((x1 + x2) / 2, y1 - 5), anchor or "middle"
                else:                            # vertical: right of the middle
                    at, anchor = (x1 + 6, (y1 + y2) / 2 + 4), anchor or "start"
            self.text(at[0], at[1], label, anchor or "start")
        for _, y in pts:
            self._grow(y)

    def svg(self):
        h = self.maxy + 16
        mw = min(self.W, 760)
        return (f'<div class="frame">\n<svg viewBox="0 0 {self.W} {h:.0f}" role="img" '
                f'aria-label="{escape(self.label)}" style="max-width:{self.W}px;min-width:{mw}px">\n'
                f'<defs><marker id="ah-{self.name}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
                f'markerHeight="7" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="currentColor"/>'
                f'</marker></defs>\n' + "\n".join(self.parts) + "\n</svg>\n</div>")


def ladder(d, x, y, w, items, side_x, side_w, gap=24):
    """A vertical chain of boxes and questions; a question may branch to its right.

    items: ("box", lines, kind[, href]) or
           ("q", lines, side_lines|None, side_kind, side_label, down_label[, href]).
    The chain continues downward from each item; returns the boxes in order.
    """
    prev, prev_label, out = None, None, []
    for it in items:
        if it[0] == "box":
            b = d.box(x, y, w, it[1], it[2], href=it[3] if len(it) > 3 else None)
            side, down = None, None
        else:
            _, lines, sl, sk, slab, down, *rest = it
            b = d.box(x, y, w, lines, "q", href=rest[0] if rest else None)
            side = None
            if sl:
                # measure the side box's height first, then centre it on the question
                probe = Diagram("_", 10, "")
                hh = probe.box(0, 0, side_w, sl, sk).h
                side = d.box(side_x, b.cy - hh / 2, side_w, sl, sk)
                d.arrow([b.r(), (side.x, b.cy)], slab)
        if prev is not None:
            d.arrow([prev.b(), b.t()], prev_label)
        out.append(b)
        prev, prev_label = b, down
        y = max(b.bottom, side.bottom if side else 0) + gap
    return out
