"""The design document and the code have to keep pointing at each other.

Roughly fifty source comments cite a section, decision or finding by number —
"(fixes F7)", "(§5.4.1)", "(D12a)". Those references are the only thing that
makes the reasoning behind a piece of code findable, and they rot silently: a
section gets renumbered or deleted during a tidy-up and every comment pointing
at it quietly becomes a lie. This test is what makes that a failure instead.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DESIGN = ROOT / "refactor.md"

SOURCES = [
    path
    for pattern in ("bgassist/**/*.py", "bgassist/**/*.js", "tests/*.py",
                    "build/*.spec", "build/*.sh", "build/hooks/*.py")
    for path in ROOT.glob(pattern)
    if path.is_file()
]


def _cited():
    sections, decisions, findings = set(), set(), set()
    for path in SOURCES:
        text = path.read_text(encoding="utf-8", errors="replace")
        sections |= set(re.findall(r"§\d+(?:\.\d+)*", text))
        decisions |= set(re.findall(r"\b(?:D1[0-8][ab]?|D[1-9])\b(?=[),. ])", text))
        findings |= set(re.findall(r"\bF1[0-8]\b|\bF[1-9]\b", text))
    return sections, decisions, findings


def test_the_design_document_exists():
    assert DESIGN.exists()


@pytest.mark.parametrize("section", sorted(_cited()[0]))
def test_every_section_the_code_cites_still_exists(section):
    doc = DESIGN.read_text(encoding="utf-8")
    number = re.escape(section[1:])
    assert re.search(rf"^#{{2,4}} {number}[. ]", doc, re.M), \
        f"the code points at {section}, which is not in refactor.md"


@pytest.mark.parametrize("decision", sorted(_cited()[1]))
def test_every_decision_the_code_cites_still_exists(decision):
    doc = DESIGN.read_text(encoding="utf-8")
    assert re.search(rf"\| {decision} \|", doc), \
        f"the code points at decision {decision}, which is not in refactor.md"


@pytest.mark.parametrize("finding", sorted(_cited()[2]))
def test_every_finding_the_code_cites_is_explained(finding):
    doc = DESIGN.read_text(encoding="utf-8")
    assert re.search(rf"\*\*{finding} —", doc), \
        f"the code says it fixes {finding}, which is not explained in refactor.md"


def test_the_readme_leads_with_what_the_thing_is_for():
    """Not "say a wake word" — every assistant has had that since 2011."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    opening = readme[:1200]
    assert "It already heard the question." in opening
    assert "Install" in readme
    # The install path a person actually takes, near the top.
    assert readme.index("BackgroundAssistant.dmg") < len(readme) // 3


def test_the_readme_is_honest_about_what_is_stored():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Only the exchanges you actually trigger are stored" in readme
    assert "delete" in readme.lower()
