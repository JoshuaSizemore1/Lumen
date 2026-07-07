from lumen.ui import theme
from lumen.ui.widgets import Panel, button, chip, label


def test_qss_threads_accent_and_fonts():
    qss = theme.build_qss("#9ece6a")
    assert "#9ece6a" in qss
    assert theme.ACCENT not in qss          # accent fully swapped, not appended
    assert theme.FONT_MONO in qss and theme.FONT_SANS in qss


def test_widget_factories_set_style_properties(qtbot):
    lab = label("hello", "eyebrow")
    assert lab.property("role") == "eyebrow"
    c = chip("work", theme.TAG_COLORS["work"])
    assert c.property("role") == "chip"
    p = Panel()
    assert p.property("role") == "panel"
    b = button("Go", "primary")
    assert b.property("kind") == "primary"
