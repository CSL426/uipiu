"""Guard CSS ownership and cascade boundaries without freezing visual values."""

from collections import defaultdict
from pathlib import Path
import re


FRONTEND = Path(__file__).resolve().parents[1] / "frontend" / "src"


def style_modules():
    entry = (FRONTEND / "styles.css").read_text()
    imports = re.findall(r'@import "([^"\n]+)";', entry)
    return entry, [FRONTEND / name for name in imports]


def test_styles_entry_imports_each_module_once_without_inline_overrides():
    entry, modules = style_modules()
    assert modules
    assert len(modules) == len(set(modules))
    assert set(modules) == set((FRONTEND / "styles").glob("*.css"))
    remaining = re.sub(r"/\*.*?\*/", "", entry, flags=re.S)
    remaining = re.sub(r'@import "[^"\n]+";', "", remaining)
    assert not remaining.strip()
    for module in modules:
        assert "@import" not in module.read_text()


def test_cross_module_selectors_have_explicit_shared_ownership():
    _, modules = style_modules()
    owners = defaultdict(set)
    for module in modules:
        css = re.sub(r"/\*.*?\*/", "", module.read_text(), flags=re.S)
        for match in re.finditer(r"([^{}]+)\{", css):
            for selector in match[1].strip().split(","):
                selector = " ".join(selector.split())
                if not selector.startswith("@"):
                    owners[selector].add(module.name)

    # Shared surfaces and form primitives intentionally precede feature details.
    allowed = {
        ".composer": {"foundation.css", "composer.css"},
        ".field-select": {"forms-and-feedback.css", "menu.css"},
        ".search-box input": {"forms-and-feedback.css", "menu.css"},
    }
    # Breakpoints may override only the declared workspace primitives.
    responsive = {
        ".workspace", ".hero", ".shell", ".hero-copy", ".hero-card",
        ".panel", ".composer", ".field-row", ".search-box", ".menu-filters",
        ".panel-header-space", ".summary-grid",
    }
    for selector, files in owners.items():
        if selector in responsive:
            files = files - {"workspace-responsive.css"}
        if len(files) > 1:
            assert files <= allowed.get(selector, set()), (selector, files)


def test_workspace_breakpoints_follow_all_shared_base_rules():
    _, modules = style_modules()
    names = [module.name for module in modules]
    responsive = names.index("workspace-responsive.css")
    for base in (
        "foundation.css", "forms-and-feedback.css", "menu.css", "cart.css",
        "composer.css", "admin-sessions.css",
    ):
        assert names.index(base) < responsive
    css = (FRONTEND / "styles" / "workspace-responsive.css").read_text()
    assert css.index("max-width: 1180px") < css.index("max-width: 720px")
    login = (FRONTEND / "styles" / "login.css").read_text()
    assert login.index(".login-card {") < login.index("@media")


def test_generic_select_helpers_are_scoped_without_adding_specificity():
    css = (FRONTEND / "styles" / "custom-select.css").read_text()
    for helper in ("select-arrow", "option-text", "option-subtext", "option-check"):
        assert f":where(.custom-select-container) .{helper}" in css
        assert not re.search(rf"(?m)^\.{helper}(?:[. :{{])", css)
