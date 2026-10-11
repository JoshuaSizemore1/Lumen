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
rebuilds the layout a mail reader is expected to show.

`inline_remote_images` fetches with a bounded thread pool (todo-fixes #112):
_POOL_SIZE workers in parallel, overall deadline _TOTAL_DEADLINE so even a page
with 20 slow images shows within a few seconds. SSRF: every URL is resolved
before fetching and refused if it maps to a loopback, private, link-local, or
otherwise reserved address."""

import base64
import concurrent.futures
import html as _html
import ipaddress
import re
import socket as _socket
import urllib.request
from urllib.parse import urlparse

_IMG = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_SRC = re.compile(r"""\bsrc\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""", re.IGNORECASE)

_FETCH_TIMEOUT = 6.0
_MAX_BYTES = 3_000_000
_MAX_IMAGES = 80
_UA = "Mozilla/5.0 (Lumen local mail reader)"

_POOL_SIZE = 6            # concurrent image-fetch threads per message (#112)
_TOTAL_DEADLINE = 8.0     # overall per-message deadline (seconds)    (#112)

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


def _is_private_host(url: str) -> bool:
    """True when the URL's hostname resolves to a loopback, private, link-local,
    or otherwise reserved address — or when resolution fails. Prevents the image
    loader from being used as an SSRF probe against local services (#112)."""
    try:
        hostname = urlparse(url).hostname
        if not hostname:
            return True
        for _fam, _typ, _proto, _canon, sockaddr in _socket.getaddrinfo(
                hostname, None):
            try:
                ip = ipaddress.ip_address(sockaddr[0])
            except ValueError:
                continue
            if (ip.is_loopback or ip.is_private or ip.is_link_local
                    or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
                return True
    except OSError:
        return True   # can't resolve → refuse
    return False


class _PublicRedirects(urllib.request.HTTPRedirectHandler):
    """A public image URL may not redirect the loader onto the LAN either:
    tracking pixels redirect all the time (#112)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if _is_private_host(newurl):
            return None              # urllib raises; the fetch yields no image
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_PublicRedirects)


def _fetch_data_uri(url: str) -> str | None:
    if url.startswith("//"):
        url = "https:" + url
    if _is_private_host(url):
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with _OPENER.open(req, timeout=_FETCH_TIMEOUT) as resp:
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


def inline_remote_images(html: str, *, deadline: float = _TOTAL_DEADLINE) -> str:
    """Fetch remote images in parallel (up to _POOL_SIZE threads) and rewrite
    each src to a data: URI. Images not fetched within `deadline` seconds, or
    that fail or that resolve to private/loopback addresses, are dropped (no
    broken boxes). cid: parts are always dropped. Blocking — call off GUI (#112).
    """
    # --- collect unique remote URLs in document order -------------------------
    unique_urls: list[str] = []
    seen: set[str] = set()
    for tag in _IMG.findall(html or ""):
        src = _src_of(tag)
        if _is_remote(src) and not src.lower().startswith("cid:") and src not in seen:
            if len(seen) < _MAX_IMAGES:
                seen.add(src)
                unique_urls.append(src)

    # --- fetch in parallel with a bounded pool and an overall deadline --------
    results: dict[str, str | None] = {}
    if unique_urls:
        n_workers = min(_POOL_SIZE, len(unique_urls))
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=n_workers)
        try:
            futures = {pool.submit(_fetch_data_uri, url): url
                       for url in unique_urls}
            try:
                for fut in concurrent.futures.as_completed(
                        futures, timeout=deadline):
                    url = futures[fut]
                    try:
                        results[url] = fut.result()
                    except Exception:
                        results[url] = None
            except concurrent.futures.TimeoutError:
                pass   # remaining futures timed out — they stay None below
        finally:
            # Don't block on slow threads; already-running ones finish on their
            # own (socket timeout caps them at _FETCH_TIMEOUT).
            pool.shutdown(wait=False, cancel_futures=True)
        # Any URL not yet in results expired before we could collect it.
        for url in unique_urls:
            if url not in results:
                results[url] = None

    # --- rewrite the HTML -----------------------------------------------------
    def repl(m: re.Match) -> str:
        tag, src = m.group(0), _src_of(m.group(0))
        if src.lower().startswith("cid:"):
            return ""
        if not _is_remote(src):
            return tag
        data_uri = results.get(src)
        if not data_uri:
            return ""
        return _SRC.sub(lambda _m: f'src="{data_uri}"', tag, count=1)

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
_BG_CSS = re.compile(
    r"\bbackground(?:-color)?\s*:\s*(#[0-9a-f]{3,8}|rgba?\([^)]*\))",
    re.IGNORECASE)
_BGCOLOR = re.compile(r"\bbgcolor\s*=\s*(?:\"[^\"]*\"|'[^']*'|\S+)", re.IGNORECASE)
_TABLE_TAG = re.compile(r"<(table|td|th)\b[^>]*>", re.IGNORECASE)
_HAS_WIDTH_ATTR = re.compile(r"\swidth\s*=", re.IGNORECASE)
_STYLE_ATTR = re.compile(r"""\bstyle\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.IGNORECASE)
_CSS_WIDTH_DECL = re.compile(r"(?<![-\w])width\s*:\s*(\d+(?:\.\d+)?)\s*(px|%)",
                             re.IGNORECASE)
_HIDDEN_TAG = re.compile(
    r"""<(\w+)\b[^>]*\bstyle\s*=\s*(?:"[^"]*\bdisplay\s*:\s*none[^"]*"|"""
    r"""'[^']*\bdisplay\s*:\s*none[^']*')[^>]*>""", re.IGNORECASE)
_VOID_TAGS = {"img", "br", "hr", "input", "area", "col", "wbr", "source", "embed"}
_ROWLESS_OPEN = re.compile(r"(<(?:table|tbody|thead|tfoot)\b[^>]*>\s*)(?=<t[dh]\b)",
                           re.IGNORECASE)
_ROWLESS_CLOSE = re.compile(r"(</t[dh]>\s*)(?=</(?:table|tbody|thead|tfoot)>)",
                            re.IGNORECASE)

# Tidy-up after declarations are dropped: `style="; "` and `style=""` left
# behind are harmless but make the output unreadable when debugging a message.
# `[\s;]+`, not `*`: with `*` this matches an ALREADY-empty style="" and the
# replacement appends a second quote, corrupting the tag into `<td">`.
_DANGLING = re.compile(r"""(style\s*=\s*")[\s;]+(?=")""", re.IGNORECASE)
_EMPTY_STYLE = re.compile(r"""\s*style\s*=\s*"[\s;]*"|\s*style\s*=\s*'[\s;]*'""",
                          re.IGNORECASE)

CONTENT_WIDTH = 620          # default usable pane width; QTextBrowser has no
                             # max-width, so oversized boxes simply overflow.
                             # A view that knows its real width passes it.


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


def _drop_widths(html: str, max_width: int = CONTENT_WIDTH) -> str:
    """Pixel widths wider than the pane are dropped rather than scaled: the
    horizontal scrollbar is off, so anything oversized is just cut off."""
    def attr(m):
        val = next(g for g in m.groups() if g)
        return "" if int(val) > max_width else m.group(0)

    def css(m):
        return "" if int(m.group(2)) > max_width else m.group(0)

    return _WIDTH_CSS.sub(css, _WIDTH_ATTR.sub(attr, html))


def _clamp_tables(html: str, max_width: int = CONTENT_WIDTH) -> str:
    """The structured layout clamps an oversized table to the pane instead:
    dropping its width would leave the widths inside it with no sized box to
    share, which squeezes them (#30)."""
    def clamp(wm):
        val = next(g for g in wm.groups() if g)
        return f'width="{max_width}"' if int(val) > max_width else wm.group(0)
    return re.sub(r"<table\b[^>]*>", lambda m: _WIDTH_ATTR.sub(clamp, m.group(0)),
                  html, flags=re.IGNORECASE)


def _fit_widths(html: str, max_width: int = CONTENT_WIDTH) -> str:
    """Scale every pixel width on tables and cells by ONE factor, so the
    widest fixed box fits the pane and the layout keeps its proportions: a
    480px column and the 482px cells inside it shrink together. Clamping
    each to the pane separately left columns that no longer added up, and
    Qt squeezed the content (#30). Widths past twice the default pane are
    outliers (a stray 1200px banner): they don't set the factor, and the
    clamp/drop that follows deals with them alone."""
    limit = 2 * CONTENT_WIDTH
    tags = [m.group(0) for m in _TABLE_TAG.finditer(html)]
    widths = [int(next(g for g in w.groups() if g))
              for t in tags for w in _WIDTH_ATTR.finditer(t)]
    widest = max((w for w in widths if w <= limit), default=0)
    if widest <= max_width:
        return html
    factor = max_width / widest

    def scale(wm):
        val = int(next(g for g in wm.groups() if g))
        return wm.group(0) if val > limit else f'width="{round(val * factor)}"'
    return _TABLE_TAG.sub(lambda m: _WIDTH_ATTR.sub(scale, m.group(0)), html)


def _constrain_images(html: str, max_width: int = CONTENT_WIDTH) -> str:
    """Clamp an oversized image's own width to the pane instead of letting
    `_drop_widths` drop it (todo-fixes #3). Dropping a table's width lets it
    reflow to fit, but dropping an <img>'s width makes it render at its full
    intrinsic size — often 1000px+ — and overflow the pane, which is a big part
    of why HTML mail "still looks weird". Clamping keeps the picture visible and
    inside the card."""
    def clamp_attr(wm):
        val = next(g for g in wm.groups() if g)
        return (f'width="{max_width}"' if int(val) > max_width
                else wm.group(0))

    def clamp_css(cm):
        return (f"{cm.group(1)}:{max_width}px" if int(cm.group(2)) > max_width
                else cm.group(0))

    def repl(m):
        tag = m.group(0)
        return _WIDTH_CSS.sub(clamp_css, _WIDTH_ATTR.sub(clamp_attr, tag))

    return _IMG.sub(repl, html or "")


def _css_widths_to_attrs(html: str) -> str:
    """Qt's rich text sizes a table or cell from its width ATTRIBUTE only and
    ignores a CSS `width:` there. Mail built on CSS widths (todo-fixes #30: a
    Stripe receipt) lost every column size and collapsed into a strip a few
    letters wide. Copy the last CSS width onto the attribute when there is
    none. Only the structured layout does this: on most mail it changes
    nothing or makes it worse, which is why `fit_html` measures both."""
    def repl(m):
        tag = m.group(0)
        style = _STYLE_ATTR.search(tag)
        if _HAS_WIDTH_ATTR.search(tag) or not style:
            return tag
        decls = _CSS_WIDTH_DECL.findall(style.group(1) or style.group(2) or "")
        if not decls:
            return tag
        num, unit = decls[-1]
        val = f"{round(float(num))}{'%' if unit == '%' else ''}"
        cut = 1 + len(m.group(1))
        return f'{tag[:cut]} width="{val}"{tag[cut:]}'
    return _TABLE_TAG.sub(repl, html)


def _add_missing_rows(html: str) -> str:
    """Cells written straight into a table with no <tr> get an implied row in
    a browser. Qt instead treats them as more cells of the ENCLOSING table's
    row, so a stray links table became extra columns that squeezed the
    content beside it to a few letters wide (#30). Supply the row."""
    return _ROWLESS_CLOSE.sub(r"\1</tr>", _ROWLESS_OPEN.sub(r"\1<tr>", html))


def _drop_hidden(html: str) -> str:
    """Qt ignores `display:none`, so text a sender hid (the preheader that
    feeds the inbox preview) printed at the top of the message. Remove each
    hidden element with its content; one whose end tag can't be found loses
    only its start tag, so a malformed message never loses the rest."""
    out, pos = [], 0
    while (m := _HIDDEN_TAG.search(html, pos)):
        out.append(html[pos:m.start()])
        name = m.group(1).lower()
        pos = m.end()
        if name in _VOID_TAGS or m.group(0).endswith("/>"):
            continue
        depth = 1
        for t in re.compile(rf"<(/?){name}\b[^>]*>", re.IGNORECASE).finditer(
                html, m.end()):
            depth += -1 if t.group(1) else 1
            if depth == 0:
                pos = t.end()
                break
    out.append(html[pos:])
    return "".join(out)


# A background darker than this can't carry CARD_FG at 4.5:1 contrast.
_DARK_BG = 0.24


def _fix_invisible_text(html: str) -> str:
    """Light-on-dark mail goes invisible on the white paper card.

    Any text colour too pale to read against CARD_BG is dropped so it
    inherits CARD_FG (dropping beats recolouring, which fights the sender's own
    palette). So any CSS background too dark to carry CARD_FG goes too, as
    every bgcolor= attribute already does: the dark header/footer bands and
    buttons kept their background while their white text turned dark (#102).
    The band is usually a container (`<td style="background:#222">`) and the
    pale colour an element inside it, so this can't be decided per element."""

    def _fix_style(css: str) -> str:
        def drop_light(m):
            lum = _luminance(m.group(1))
            return "" if lum is not None and lum > 0.72 else m.group(0)

        def drop_dark_bg(m):
            lum = _luminance(m.group(1))
            return "" if lum is not None and lum < _DARK_BG else m.group(0)
        return _BG_CSS.sub(drop_dark_bg, _COLOR_CSS.sub(drop_light, css))

    def repl_style(m):
        inner = m.group(1) if m.group(1) is not None else m.group(2)
        quote = '"' if m.group(1) is not None else "'"
        return f'style={quote}{_fix_style(inner)}{quote}'

    return _STYLE_ATTR.sub(repl_style, html)


def prepare_html(html: str, max_width: int = CONTENT_WIDTH, *,
                 structured: bool = False) -> str:
    """Normalize a sender's HTML into something QTextBrowser renders decently:
    scripts, <style> blocks and hidden elements removed (it cannot apply most
    of that CSS anyway, and half-applied CSS is what makes mail look broken),
    rowless table cells given their row, dark backgrounds and unreadable text
    colours dropped, anything wider than `max_width` dropped, and a sane base
    font wrapped around the result.

    `structured` keeps the sender's column layout instead: CSS widths become
    attributes Qt honours, and the whole layout is scaled to fit (#30). That
    rescues mail built on CSS widths and hurts other mail, so a view that can
    measure picks between the two with `fit_html`."""
    out = _VOID_HEAD.sub("", _SCRIPT.sub("", html or ""))
    out = _add_missing_rows(_drop_hidden(out))
    if structured:
        out = _css_widths_to_attrs(out)
    out = _BGCOLOR.sub("", out)
    # Clamp image widths BEFORE the general width drop, so an oversized <img>
    # is resized to fit rather than stripped of its width and left to overflow.
    out = _constrain_images(out, max_width)
    if structured:
        out = _clamp_tables(_fit_widths(out, max_width), max_width)
    out = _fix_invisible_text(_drop_widths(out, max_width))
    out = _EMPTY_STYLE.sub("", _DANGLING.sub(r"\1", out))
    return (
        f'<div style="color:{CARD_FG}; font-size:13px; line-height:145%;">'
        f"{out}</div>")


FIT_MAX_BYTES = 150_000      # markup past this costs too much to lay out twice
_DATA_SRC = re.compile(r"""\bsrc\s*=\s*(["'])data:.*?\1""", re.IGNORECASE | re.DOTALL)


def fit_html(html: str, width: int, font=None) -> str:
    """Prepare `html` for a pane `width` px wide, choosing by measurement.

    The conservative and the structured preparation are both laid out at
    `width`; the one that fits wins, and between two that fit, the shorter
    (a squeezed layout is a tall one). A structured layout that overflows
    gets one more try at the width it measured. Measured over Josh's 344
    HTML messages at the ~376px pane a 1280px window gives: overflowing mail
    went from 63 to 17 and no message came out wider or 1.5x taller than
    before; a typical message costs ~15ms. Needs a QGuiApplication."""
    from PyQt6.QtGui import QTextDocument

    def measure(markup):
        # Inlined images don't change which layout fits (their widths are
        # in attributes); decoding them for every candidate would.
        doc = QTextDocument()
        if font is not None:
            doc.setDefaultFont(font)
        doc.setHtml(_DATA_SRC.sub('src=""', markup))
        doc.setTextWidth(width)
        return doc.idealWidth() > width + 2, doc.idealWidth(), doc.size().height()

    plain = prepare_html(html, width)
    # Measured without inlined images: with "always load images" on, those
    # alone put an ordinary receipt past the limit.
    if len(_DATA_SRC.sub("", html or "")) > FIT_MAX_BYTES:
        return plain
    rich = prepare_html(html, width, structured=True)
    if rich == plain:
        return plain
    cands = [(measure(plain), plain), (measure(rich), rich)]
    (over, ideal, _), _ = cands[1]
    if over:
        retry = prepare_html(html, max(120, int(width * width / ideal) - 8),
                             structured=True)
        cands.append((measure(retry), retry))
    return min(cands, key=lambda c: (c[0][0], c[0][1] if c[0][0] else c[0][2]))[1]
