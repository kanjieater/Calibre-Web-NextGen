"""The classic UI must offer a way to support the project — in BOTH themes.

The classic layout renders two different user-menu blocks: a profile dropdown
that only exists when `g.current_theme == 1` (caliBlur), and a flat list of
items for `g.current_theme == 0` (the default theme). A link added to only one
of them is invisible to half the users — the recurring trap in this template,
and the reason cwn-local (which forces caliBlur) can look fine while the
default theme is missing the feature entirely.
"""
import re
from pathlib import Path

LAYOUT = Path(__file__).resolve().parents[2] / "cps" / "templates" / "layout.html"
KOFI = "https://ko-fi.com/calibrewebnextgen"


def _layout():
    return LAYOUT.read_text(encoding="utf-8")


def test_support_link_present_twice_once_per_theme():
    src = _layout()
    assert src.count('id="top_support"') == 2, (
        "expected one support link in the caliBlur profile dropdown and one in "
        "the default-theme user menu"
    )


def test_support_link_points_at_the_project_kofi_page():
    src = _layout()
    for m in re.finditer(r'id="top_support"[^>]*href="([^"]+)"', src):
        assert m.group(1) == KOFI, f"support link points at {m.group(1)}"


def test_support_link_opens_safely_in_a_new_tab():
    # target=_blank without rel=noopener lets the opened page reach back via
    # window.opener; this is an external link so both attributes are required.
    for tag in re.findall(r'<a id="top_support".*?>', _layout()):
        assert 'target="_blank"' in tag
        assert "noopener" in tag and "noreferrer" in tag


def test_support_label_is_translatable():
    # Every user-visible string in this project goes through gettext, or the
    # msgid never reaches the translators and non-English users see English.
    for tag in re.findall(r'<a id="top_support".*?</a>', _layout(), re.S):
        assert "_('Support Calibre-Web NextGen')" in tag


def test_default_theme_block_contains_a_support_link():
    src = _layout()
    start = src.index("{% if g.current_theme == 0 %}")
    end = src.index("{% endif %}", src.index('id="logout"', start))
    assert 'id="top_support"' in src[start:end], (
        "default-theme (g.current_theme == 0) user menu has no support link"
    )
