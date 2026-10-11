"""mail_html: remote-image handling for the reading pane (todo-fixes #12).
Pure functions — no network (fetch is monkeypatched); only the #30 layout
tests at the end lay markup out in Qt."""
import re

import pytest

from lumen.ui_v2 import mail_html

_HTML = (
    '<p>hi</p>'
    '<img src="https://track.example/pixel.gif?e=me">'
    "<img src='http://cdn.example/logo.png'>"
    '<img src="cid:inline-part-1">'
    '<img src="data:image/png;base64,AAAA">'
)


def test_remote_image_count_counts_remote_and_cid_only():
    assert mail_html.remote_image_count(_HTML) == 3      # 2 http + 1 cid, not data:
    assert mail_html.remote_image_count("<p>no images</p>") == 0


def test_strip_remote_images_removes_remote_keeps_data():
    out = mail_html.strip_remote_images(_HTML)
    assert "track.example" not in out and "cdn.example" not in out
    assert "cid:" not in out
    assert "data:image/png;base64,AAAA" in out           # inline image survives
    assert "<p>hi</p>" in out


def test_inline_remote_images_swaps_src_and_drops_failures(monkeypatch):
    def fake_fetch(url):
        return "data:image/png;base64,ZZZZ" if "logo" in url else None
    monkeypatch.setattr(mail_html, "_fetch_data_uri", fake_fetch)
    out = mail_html.inline_remote_images(_HTML)
    assert "data:image/png;base64,ZZZZ" in out           # logo fetched + inlined
    assert "track.example" not in out                    # pixel failed → removed
    assert "cid:" not in out                             # cid can't be fetched → removed
    assert "data:image/png;base64,AAAA" in out           # pre-inlined image untouched


def test_inline_fetches_each_unique_url_once(monkeypatch):
    calls = []
    monkeypatch.setattr(mail_html, "_fetch_data_uri",
                        lambda url: calls.append(url) or "data:image/png;base64,Q")
    html = '<img src="https://x/a.png"><img src="https://x/a.png">'
    mail_html.inline_remote_images(html)
    assert calls == ["https://x/a.png"]                  # deduped


# ---- plain-text bodies (todo-fixes #16, #17) --------------------------------

from lumen.ui_v2.mail_html import plain_to_html, prepare_html, CONTENT_WIDTH


def test_plain_text_is_escaped_before_anything_else():
    # The bug: Qt's AutoText heuristic renders this as "a d".
    out = plain_to_html("a < b and c > d")
    assert "a &lt; b and c &gt; d" in out
    assert "<b " not in out and "<d>" not in out


def test_plain_text_html_is_not_interpreted_as_markup():
    out = plain_to_html("Use the <br> tag and &amp; entities carefully")
    assert "&lt;br&gt;" in out and "&amp;amp;" in out


def test_plain_text_keeps_line_structure():
    out = plain_to_html("line one\nline two\n\npara two")
    assert out.count("<div>") >= 3
    assert "height:8px" in out          # the blank line became spacing


def test_plain_text_marks_quoted_passages():
    out = plain_to_html("> On Tue, Chris wrote:\n>> deeper\n\nThanks!")
    assert "margin-left:12px" in out and "margin-left:24px" in out
    assert "Thanks!" in out


def test_plain_text_linkifies_urls_and_addresses():
    out = plain_to_html("See https://example.com/x or mail me@x.com")
    assert '<a href="https://example.com/x">' in out
    assert '<a href="mailto:me@x.com">' in out


def test_plain_text_linkify_cannot_inject_markup():
    # a URL containing a quote must not break out of the href attribute
    out = plain_to_html('http://x.com/"onmouseover="alert(1)')
    assert "onmouseover=\"alert" not in out or "&quot;" in out


def test_plain_text_dims_the_signature():
    out = plain_to_html("Body text\n--\nChris\nSome Corp")
    assert out.count("#6b6b6b") >= 2     # the marker and the lines after it


def test_empty_plain_text_says_so():
    assert "no text" in plain_to_html("")


# ---- HTML normalization (todo-fixes #15) ------------------------------------

def test_prepare_html_drops_scripts_and_style_blocks():
    out = prepare_html("<style>p{color:red}</style><script>x()</script><p>hi</p>")
    assert "color:red" not in out and "x()" not in out and "hi" in out


def test_prepare_html_drops_oversized_widths():
    out = prepare_html(f'<table width="{CONTENT_WIDTH + 300}"><td>a</td></table>')
    assert f"{CONTENT_WIDTH + 300}" not in out


def test_prepare_html_keeps_widths_that_fit():
    out = prepare_html('<table width="400"><td>a</td></table>')
    assert 'width="400"' in out


def test_prepare_html_clamps_oversized_image_instead_of_dropping():
    # An oversized <img> keeps a width — clamped to the pane — rather than being
    # stripped bare and rendering at full intrinsic size (overflow, #3).
    out = prepare_html(f'<img src="data:image/png;base64,x" width="{CONTENT_WIDTH + 500}">')
    assert f'width="{CONTENT_WIDTH}"' in out
    assert f"{CONTENT_WIDTH + 500}" not in out


def test_prepare_html_clamps_oversized_image_css_width():
    out = prepare_html(
        f'<img src="data:image/png;base64,x" style="width:{CONTENT_WIDTH + 400}px">')
    assert f"width:{CONTENT_WIDTH}px" in out


def test_prepare_html_keeps_small_image_width():
    out = prepare_html('<img src="data:image/png;base64,x" width="300">')
    assert 'width="300"' in out


def test_prepare_html_drops_unreadable_light_text():
    # white-on-dark mail is invisible on the white paper card
    out = prepare_html('<p style="color:#ffffff">important</p>')
    assert "#ffffff" not in out and "important" in out


def test_prepare_html_keeps_readable_colors():
    out = prepare_html('<p style="color:#333333">readable</p>')
    assert "#333333" in out


def test_prepare_html_leaves_no_broken_style_attributes():
    out = prepare_html('<td style="color:#fff; width:9999px">hi</td>')
    assert '<td>hi</td>' in out          # not `<td">` or `<td style="">`


def test_prepare_html_is_idempotent():
    once = prepare_html('<td style="color:#fff; width:9999px">hi</td>')
    assert prepare_html(once).count("<td>") == 1


# ---- CSS-built layouts and the measured fit (todo-fixes #30) ----------------
#
# Shaped like the Stripe receipt that rendered as a strip a few letters wide:
# a hidden preheader, a centred 480px column sized only in CSS, cells sized
# `width:100%; width:482px`, and a links table whose cells sit straight in
# <tbody> with no <tr>.

from lumen.ui_v2.mail_html import _add_missing_rows, _drop_hidden, fit_html

_CELL = '<td style="width:100%; width:482px">'
RECEIPT_LIKE = (
    '<table width="100%"><tr><td></td><td>'
    '<table align="center" style="width:480px; min-width:480px; max-width:480px">'
    '<tr><td><div style="display:none !important">PREHEADER-TEXT</div>'
    '<table style="width:100%"><tr>' + _CELL +
    '<table style="width:100%"><tr><td style="width:32px"></td><td>'
    '<table style="width:100%"><tr><td style="width:100%">'
    'Receipt from Example Store, Inc.</td></tr></table>'
    '<table style="width:100%"><tr><td style="width:100%">$20.00</td></tr></table>'
    '<table style="width:100%"><tr><td style="width:100%">Paid July 16, 2026'
    '</td></tr></table></td><td style="width:76px"></td></tr></table>'
    '<table><tbody><td><a href="#">Download invoice</a></td>'
    '<td style="width:16px"></td><td><a href="#">Download receipt</a></td>'
    '</tbody></table>'
    '<table style="width:100%"><tr><td>Receipt number</td>'
    '<td align="right">1234-5678-9012</td></tr></table>'
    '</td></tr></table></td></tr></table></td><td></td></tr></table>')


def _layout(markup, width):
    from PyQt6.QtGui import QTextDocument
    doc = QTextDocument()
    doc.setHtml(markup)
    doc.setTextWidth(width)
    return doc


def test_hidden_elements_are_removed_with_their_content():
    out = _drop_hidden('<p>a</p><div style="color:red;display: none">'
                       '<div>b</div></div><p>c</p>')
    assert out == "<p>a</p><p>c</p>"


def test_a_hidden_element_without_an_end_tag_keeps_the_rest():
    out = _drop_hidden('<span style="display:none">x <p>rest of the mail</p>')
    assert "rest of the mail" in out and "display" not in out


def test_rowless_cells_get_a_row():
    out = _add_missing_rows("<table><tbody><td>a</td><td>b</td></tbody></table>")
    assert out == "<table><tbody><tr><td>a</td><td>b</td></tr></tbody></table>"
    ok = "<table><tr><td>a</td></tr></table>"
    assert _add_missing_rows(ok) == ok


def test_structured_turns_css_widths_into_attributes_and_scales_together():
    out = prepare_html(RECEIPT_LIKE, 360, structured=True)
    # 480 and 482 shrink by one factor (360/482), spacers with them.
    f = 360 / 482
    for px in (480, 482, 32, 76):
        assert f'width="{round(px * f)}"' in out
    assert "PREHEADER-TEXT" not in out


def test_the_conservative_preparation_leaves_css_widths_alone():
    out = prepare_html(RECEIPT_LIKE, 360)
    assert 'width="359"' not in out and 'width="480"' not in out
    assert "PREHEADER-TEXT" not in out


@pytest.mark.parametrize("width", [360, 600])
def test_fit_html_lays_a_css_built_receipt_out_readably(qapp, width):
    """The bug: Qt ignores CSS widths, so every column collapsed and the
    receipt became a tall strip of broken words."""
    before = _layout(prepare_html(RECEIPT_LIKE, width), width)
    after = _layout(fit_html(RECEIPT_LIKE, width), width)
    assert after.idealWidth() <= width + 2               # nothing cut off
    assert after.size().height() < before.size().height() / 2
    text = after.toPlainText()
    assert "Receipt from Example Store, Inc." in text
    assert "PREHEADER-TEXT" not in text


def test_fit_html_keeps_the_conservative_layout_when_scaling_would_squeeze(qapp):
    # One wide box sets the scale factor; shrinking everything by it would
    # squeeze the text column, so the measured choice keeps the reflow.
    words = " ".join(["word"] * 120)
    html = (f'<table style="width:1100px"><tr><td style="width:1100px">banner'
            f'</td></tr></table><table style="width:300px"><tr>'
            f'<td style="width:300px">{words}</td></tr></table>')
    plain = _layout(prepare_html(html, 360), 360)
    chosen = _layout(fit_html(html, 360), 360)
    assert chosen.idealWidth() <= 362
    assert chosen.size().height() <= plain.size().height()


def test_fit_html_skips_measuring_mail_with_nothing_to_restructure(qapp):
    html = "<p>Just a short note.</p>"
    assert fit_html(html, 360) == prepare_html(html, 360)


def test_fit_html_still_fits_a_receipt_carrying_inlined_images(qapp):
    """With "always load images" on, inlined data: URIs alone used to push an
    ordinary receipt past FIT_MAX_BYTES, skipping the fix exactly there."""
    big = "A" * (mail_html.FIT_MAX_BYTES + 50_000)
    html = RECEIPT_LIKE.replace(
        "Receipt from", f'<img src="data:image/png;base64,{big}" width="32"> Receipt from')
    chosen = fit_html(html, 360)
    # The structured layout: the CSS-only 480px column got a width attribute.
    assert re.search(r'<table width="\d+" align="center"', chosen)
    assert big in chosen                                 # images kept in the result
