"""Intent: the version in the UI footer, the changelog and the git tag agree.

Three places record the release and none of them derive from the others:
`frontend/src/config.ts` feeds the footer, `CHANGELOG.md` documents it, and the git
tag is what a checkout resolves to. They drift silently — a tag can land on a
commit whose changelog entry is missing later work, and nothing complains.

The git check is skipped when the repository has no tags, so a fresh clone or a
CI checkout without tags still runs the rest.
"""

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
CONFIG_TS = REPO / "frontend" / "src" / "config.ts"
CHANGELOG = REPO / "CHANGELOG.md"

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def ui_version() -> str:
    match = re.search(r'version:\s*"([^"]+)"', CONFIG_TS.read_text(encoding="utf-8"))
    assert match, f"no `version: \"…\"` found in {CONFIG_TS.relative_to(REPO)}"
    return match.group(1)


def changelog_versions() -> list[str]:
    return re.findall(r"^## \[Lancy v([^\]]+)\]", CHANGELOG.read_text(encoding="utf-8"), re.M)


def git(*args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args], cwd=REPO, capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def test_ui_version_is_a_semver_string():
    assert SEMVER.match(ui_version()), f"{ui_version()!r} is not MAJOR.MINOR.PATCH"


def test_changelog_has_entries():
    assert changelog_versions(), "no `## [Lancy vX.Y.Z]` headings in CHANGELOG.md"


def test_ui_version_matches_the_newest_changelog_entry():
    """The footer must name the release the changelog describes at the top."""
    newest = changelog_versions()[0]

    assert ui_version() == newest, (
        f"config.ts says {ui_version()}, newest CHANGELOG entry is {newest} — "
        "bump both together"
    )


def test_changelog_versions_are_unique():
    versions = changelog_versions()

    duplicates = {v for v in versions if versions.count(v) > 1}
    assert not duplicates, f"CHANGELOG has repeated version headings: {sorted(duplicates)}"


def test_changelog_is_in_descending_version_order():
    def key(v: str) -> tuple:
        return tuple(int(p) for p in v.split("."))

    versions = [v for v in changelog_versions() if SEMVER.match(v)]

    assert versions == sorted(versions, key=key, reverse=True), (
        "CHANGELOG entries are not newest-first"
    )


@pytest.mark.skipif(not git("tag"), reason="repository has no tags")
def test_latest_tag_matches_the_ui_version():
    """Catches a release tagged before the version was bumped, or vice versa."""
    latest = git("describe", "--tags", "--abbrev=0")
    assert latest, "could not read the latest tag"

    assert latest.lstrip("v") == ui_version(), (
        f"latest tag is {latest}, config.ts says {ui_version()}"
    )


@pytest.mark.skipif(not git("tag"), reason="repository has no tags")
def test_the_tagged_commit_is_an_ancestor_of_head():
    """A tag on a side branch means the release is not what main contains."""
    latest = git("describe", "--tags", "--abbrev=0")
    merge_base = git("merge-base", "--is-ancestor", latest, "HEAD")

    # --is-ancestor communicates through the exit code; git() returns "" on success.
    assert merge_base is not None, f"tag {latest} is not an ancestor of HEAD"
