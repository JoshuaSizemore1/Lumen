"""Body rendering for the reading pane: image handling, HTML normalization, and
plain-text formatting.

QTextBrowser never touches the network, so remote <img> tags would render as
broken boxes (todo-fixes #12). `inline_remote_images` fetches them off the GUI
thread and rewrites each src to a data: URI; `strip_remote_images` is the
no-network view behind the `mail.load_remote_images` setting. Loading images
does let the sender log the open (time, IP, repeat opens) — the user weighed
that and chose always-load as the default on 2026-07-19.

It also renders only a subset of HTML4/CSS2, which is why real mail "sometimes
looks okay, other times horrible" (todo-fixes #15): fixed pixel widths overflow
a pane whose horizontal scrollbar is off, and light-on-dark palettes go
invisible on the white paper card. `prepare_html` normalizes both.

Plain-text bodies get their own path (todo-fixes #16/#17). They must never
reach a rich-text widget raw: QLabel defaults to Qt::AutoText, whose
mightBeRichText() heuristic fires on ordinary prose — "a < b and c > d" renders
as "a d", silently eating the message. `plain_to_html` escapes first, then
rebuilds the layout a mail reader is expected to show."""

import base64
import html as _html
import re
import urllib.request

_IMG = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_SRC = re.compile(r"""\bsrc\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""", re.IGNORECASE)

_FETCH_TIMEOUT = 6.0
_MAX_BYTES = 3_000_000
_MAX_IMAGES = 80
_UA = "Mozilla/5.0 (Lumen local mail reader)"

# The paper card HtmlBody paints on. Text must stay legible against this.
CARD_BG = "#ffffff"
CARD_FG = "#1f1f1f"
QUOTE_FG = "#6b6b6b"
LINK_FG = "#1a5fb4"


def _src_of(tag: str) -> str:
    m = _SRC.search(tag)
    return (m.group(1) or m.group(2) or m.group(3) or "").strip() if m else ""


def _is_remote(src: str) -> bool:
    return src.lower().startswith(("http://", "https://", "//", "cid:"))


def remote_image_count(html: str) -> int:
    """How many images would need a network fetch to show — i.e. how many the
    default (stripped) view is hiding."""
    return sum(1 for t in _IMG.findall(html or "") if _is_remote(_src_of(t)))


def strip_remote_images(html: str) -> str:
    """Drop remote/cid <img> tags (would-be broken boxes); keep data: images."""
    return _IMG.sub(
        lambda m: "" if _is_remote(_src_of(m.group(0))) else m.group(0),
        html or "")


def _fetch_data_uri(url: str) -> str | None:
    if url.startswith("//"):
        url = "https:" + url
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:
            ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            data = resp.read(_MAX_BYTES + 1)
    except Exception:
        return None
    if not data or len(data) > _MAX_BYTES:
        return None
    if not ctype.startswith("image/"):
        if not re.search(r"\.(png|jpe?g|gif|webp|bmp|svg)(\?|$)", url, re.IGNORECASE):
            return None
        ctype = "image/png"          # server mislabelled a real image extension
    return f"data:{ctype};base64," + base64.b64encode(data).decode("ascii")


def inline_remote_images(html: str) -> str:
    """Fetch each remote image once and rewrite its src to a data: URI; drop the
    ones that fail or are cid: attachment parts (no broken boxes left behind).
    Blocking network — call it off the GUI thread."""
    cache: dict[str, str | None] = {}

    def repl(m: re.Match) -> str:
        tag, src = m.group(0), _src_of(m.group(0))
        if not _is_remote(src) or src.lower().startswith("cid:"):
            return "" if src.lower().startswith("cid:") else tag
        if src not in cache:
            cache[src] = _fetch_data_uri(src) if len(cache) < _MAX_IMAGES else None
        data_uri = cache[src]
        return "" if not data_uri else _SRC.sub(
            lambda _m: f'src="{data_uri}"', tag, count=1)

    return _IMG.sub(repl, html or "")


# ---- plain-text bodies (todo-fixes #16, #17) --------------------------------

_URL = re.compile(r"""(?<![\w@.])((?:https?://|www\.)[^\s<>"')\]]+)""", re.IGNORECASE)
_BARE_EMAIL = re.compile(r"(?<![\w.])([\w.+-]+@[\w-]+\.[\w.-]+)(?![\w.])")
_QUOTE = re.compile(r"^((?:>\s?)+)(.*)$")
_SIG = re.compile(r"^--\s*$")
_TRAILING_BLANKS = re.compile(r"\n{3,}")


def _linkify(escaped: str) -> str:
    """URLs and bare addresses -> anchors. Runs on ALREADY-ESCAPED text, so the
    replacement is the only markup that can exist in the output."""
    def url(m):
        raw = m.group(1)
        href = raw if raw.lower().startswith("http") else f"https://{raw}"
        return f'<a href="{href}">{raw}</a>'
    out = _URL.sub(url, escaped)
    return _BARE_EMAIL.sub(lambda m: f'<a href="mailto:{m.group(1)}">{m.group(1)}</a>',
                           out)


def plain_to_html(text: str) -> str:
    """A plain-text mail body as the safe HTML a reader is expected to show:
    escaped (never guessed at), hard line breaks kept, paragraph spacing between
    blocks, quoted (`>`) passages set apart the way every mail client does them,
    and the signature after `--` dimmed. Escaping happens FIRST — everything
    after it is markup this function chose."""
    body = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    body = _TRAILING_BLANKS.sub("\n\n", body.strip("\n"))
    if not body:
        return f'<div style="color:{QUOTE_FG};">(This message has no text.)</div>'

    lines, in_sig = [], False
    for raw in body.split("\n"):
        if _SIG.match(raw):
            in_sig = True
            lines.append(f'<div style="color:{QUOTE_FG};">--</div>')
            continue
        m = _QUOTE.match(raw)
        if m:
            depth = m.group(1).count(">")
            inner = _linkify(_html.escape(m.group(2)))
            lines.append(
                f'<div style="color:{QUOTE_FG}; '
                f'margin-left:{min(depth, 4) * 12}px;">'
                f'{inner or "&nbsp;"}</div>')
            continue
        inner = _linkify(_html.escape(raw))
        if not inner.strip():
            lines.append('<div style="height:8px;">&nbsp;</div>')
        elif in_sig:
            lines.append(f'<div style="color:{QUOTE_FG};">{inner}</div>')
        else:
            lines.append(f"<div>{inner}</div>")
    return (f'<div style="color:{CARD_FG}; white-space:normal;">'
            + "".join(lines) + "</div>")


# ---- HTML bodies (todo-fixes #15) -------------------------------------------

_SCRIPT = re.compile(r"<(script|style|head|meta|link)\b.*?</\1\s*>",
                     re.IGNORECASE | re.DOTALL)
_VOID_HEAD = re.compile(r"<(?:meta|link)\b[^>]*>", re.IGNORECASE)
_WIDTH_ATTR = re.compile(r"""\bwidth\s*=\s*(?:"(\d+)"|'(\d+)'|(\d+))""", re.IGNORECASE)
_WIDTH_CSS = re.compile(r"\b(min-width|width)\s*:\s*(\d+)\s*px", re.IGNORECASE)
_COLOR_CSS = re.compile(r"\bcolor\s*:\s*(#[0-9a-f]{3,8}|rgba?\([^)]*\))",
                        re.IGNORECASE)
_BGCOLOR = re.compile(r"\bbgcolor\s*=\s*(?:\"[^\"]*\"|'[^']*'|\S+)", re.IGNORECASE)

# Tidy-up after declarations are dropped: `style="; "` and `style=""` left
# behind are harmless but make the output unreadable when debugging a message.
# `[\s;]+`, not `*`: with `*` this matches an ALREADY-empty style="" and the
# replacement appends a second quote, corrupting the tag into `<td">`.
_DANGLING = re.compile(r"""(style\s*=\s*")[\s;]+(?=")""", re.IGNORECASE)
_EMPTY_STYLE = re.compile(r"""\s*style\s*=\s*"[\s;]*"|\s*style\s*=\s*'[\s;]*'""",
                          re.IGNORECASE)

CONTENT_WIDTH = 620          # the pane's usable width; QTextBrowser has no
                             # max-width, so oversized boxes simply overflow


def _luminance(color: str) -> float | None:
    """Perceived luminance 0..1, or None if the colour isn't parseable."""
    c = color.strip().lower()
    if c.startswith("#"):
        h = c[1:]
        if len(h) in (3, 4):
            h = "".join(ch * 2 for ch in h[:3])
        if len(h) not in (6, 8):
            return None
        try:
            r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            return None
    elif c.startswith("rgb"):
        nums = re.findall(r"[\d.]+", c)
        if len(nums) < 3:
            return None
        try:
            r, g, b = (float(n) for n in nums[:3])
        except ValueError:
            return None
    else:
        return None
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255


def _clamp_widths(html: str) -> str:
    """Pixel widths wider than the pane are dropped rather than scaled: the
    horizontal scrollbar is off, so anything oversized is just cut off."""
    def attr(m):
        val = next(g for g in m.groups() if g)
        return "" if int(val) > CONTENT_WIDTH else m.group(0)

    def css(m):
        return "" if int(m.group(2)) > CONTENT_WIDTH else m.group(0)

    return _WIDTH_CSS.sub(css, _WIDTH_ATTR.sub(attr, html))


def _constrain_images(html: str) -> str:
    """Clamp an oversized image's own width to the pane instead of letting
    `_clamp_widths` drop it (todo-fixes #3). Dropping a table's width lets it
    reflow to fit, but dropping an <img>'s width makes it render at its full
    intrinsic size — often 1000px+ — and overflow the pane, which is a big part
    of why HTML mail "still looks weird". Clamping keeps the picture visible and
    inside the card."""
    def clamp_attr(wm):
        val = next(g for g in wm.groups() if g)
        return (f'width="{CONTENT_WIDTH}"' if int(val) > CONTENT_WIDTH
                else wm.group(0))

    def clamp_css(cm):
        return (f"{cm.group(1)}:{CONTENT_WIDTH}px" if int(cm.group(2)) > CONTENT_WIDTH
                else cm.group(0))

    def repl(m):
        tag = m.group(0)
        return _WIDTH_CSS.sub(clamp_css, _WIDTH_ATTR.sub(clamp_attr, tag))

    return _IMG.sub(repl, html or "")


def _fix_invisible_text(html: str) -> str:
    """Light-on-dark mail goes invisible on the white paper card. Any text
    colour too pale to read against it is dropped so it inherits CARD_FG —
    dropping beats recolouring, which would fight the sender's own palette."""
    def repl(m):
        lum = _luminance(m.group(1))
        return "" if lum is not None and lum > 0.72 else m.group(0)
    return _COLOR_CSS.sub(repl, html)


def prepare_html(html: str) -> str:
    """Normalize a sender's HTML into something QTextBrowser renders decently:
    scripts and <style> blocks removed (it cannot apply most of them anyway,
    and half-applied CSS is what makes mail look broken), dark backgrounds
    dropped, unreadable text colours dropped, oversized fixed widths dropped,
    and a sane base font wrapped around the result."""
    out = _VOID_HEAD.sub("", _SCRIPT.sub("", html or ""))
    out = _BGCOLOR.sub("", out)
    # Clamp image widths BEFORE the general width drop, so an oversized <img>
    # is resized to fit rather than stripped of its width and left to overflow.
    out = _constrain_images(out)
    out = _fix_invisible_text(_clamp_widths(out))
    out = _EMPTY_STYLE.sub("", _DANGLING.sub(r"\1", out))
    return (
        f'<div style="color:{CARD_FG}; font-size:13px; line-height:145%;">'
        f"{out}</div>")
