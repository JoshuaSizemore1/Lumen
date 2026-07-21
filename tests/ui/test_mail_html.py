"""mail_html: remote-image handling for the reading pane (todo-fixes #12).
Pure functions — no Qt, no network (fetch is monkeypatched)."""
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
