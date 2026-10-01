"""Keep the route entry small and dependencies pointed toward shared domain code."""

import re
from pathlib import Path


SRC = Path(__file__).resolve().parents[1] / "frontend" / "src"


def test_app_keeps_at_least_seventy_percent_of_original_lines_out_of_entry():
    # The refactor began with a 3,017-line entry; prevent page logic growing back.
    assert len((SRC / "App.tsx").read_text().splitlines()) <= 905


def test_components_and_domain_helpers_do_not_depend_on_pages():
    for folder in ("components", "lib"):
        for source in (SRC / folder).glob("*.ts*"):
            if ".test." in source.name:
                continue
            for imported in re.findall(r'from [\'"]([^\'"]+)[\'"]', source.read_text()):
                assert "/pages/" not in imported, (source.name, imported)
                assert not imported.endswith("/App"), (source.name, imported)
                if folder == "lib":
                    assert "/components/" not in imported, (source.name, imported)
