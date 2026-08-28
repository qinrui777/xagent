"""Invariants that keep `CI Summary` fail-closed.

`CI Summary` is a required status check on `main`. GitHub reports a *skipped*
job to branch protection as a success, so the summary can only stay fail-closed
while two properties hold:

1. every job it gathers is also named in its ``check_job`` list, and
2. none of those jobs is ever skipped, which is why their conditions live on
   their steps rather than on the job.

Both are prose in ``docs/branch-protection.md``. This module turns them into
something that fails. See that document's "Required contexts must be summary
jobs" and "Gate at the step, not at the job" sections for the reasoning.

``ci.yml`` has a second contract test, ``frontend/src/ci/frontend-test-manifest.test.ts``,
which freezes the summary script and the frontend-build steps by exact text. A
change to either of those regions has to update both files or CI fails in the
frontend lane.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
PYPROJECT = REPO_ROOT / "pyproject.toml"

DRAFT_GUARD = (
    "github.event_name != 'pull_request' || github.event.pull_request.draft == false"
)

# Jobs whose steps are gated on the `changes` job, and the output each one
# reads. Everything else CI Summary gathers runs unconditionally.
GATED_JOBS = {
    "pytest-fast": "code",
    "pytest-fast-deepdoc": "code",
    "pytest-slow": "code",
    "e2e": "code",
    "frontend-build": "frontend",
}

# Widening this set means docs-only pull requests stop running some part of the
# suite. Changing it here as well as in the workflow is the point: it has to be
# a deliberate act, not a one-line edit that reads as harmless in review.
CODE_FILTER_EXCLUDES = frozenset({"!docs/**", "!assets/**", "!*.md"})

# This action decides whether the test suite runs at all, so it is pinned by
# commit. Bumping it must be deliberate -- update this constant in the same
# change.
PATHS_FILTER_PIN = "dorny/paths-filter@ceb8a2b8f2d89434be7ff52d3de7ec3738c5cc9d"

# pulls.listFiles caps its response at this many files, and paths-filter does
# not compare the rows it received against the pull request's changed_files.
LIST_FILES_MAX = 3000


class _NoDuplicateKeyLoader(yaml.SafeLoader):
    """A loader that rejects duplicate mapping keys.

    PyYAML accepts them and keeps the last one, which silently drops a step's
    guard when an edit adds a second ``if:`` to the same step. GitHub rejects
    the workflow outright, so the permissive parse is the wrong default here.
    """


def _no_duplicates(loader: yaml.Loader, node: yaml.MappingNode, deep: bool = False):
    seen = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise AssertionError(
                f"duplicate key {key!r} at line {key_node.start_mark.line + 1}"
            )
        seen.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)


_NoDuplicateKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_duplicates
)


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.load(
        CI_WORKFLOW.read_text(encoding="utf-8"), Loader=_NoDuplicateKeyLoader
    )


@pytest.fixture(scope="module")
def jobs(workflow: dict) -> dict:
    return workflow["jobs"]


@pytest.fixture(scope="module")
def summary(jobs: dict) -> dict:
    return jobs["ci-summary"]


def _filter_step(jobs: dict) -> dict:
    return next(step for step in jobs["changes"]["steps"] if step.get("id") == "filter")


def _normalise(expression: str) -> str:
    return re.sub(r"\s+", " ", expression).strip()


def test_summary_gathers_every_job_it_checks(jobs: dict, summary: dict) -> None:
    """`needs` and the `check_job` calls must name the same set of jobs.

    A job missing from either list is advisory only: it can fail without
    failing the required check.
    """
    script = summary["steps"][0]["run"]
    checked = set(re.findall(r'check_job "([^"]+)"', script))
    gathered = set(summary["needs"])

    assert checked == gathered, (
        "ci-summary's needs and its check_job list have drifted apart; "
        f"only in needs: {sorted(gathered - checked)}, "
        f"only in check_job: {sorted(checked - gathered)}"
    )

    # Nothing may be left out of the summary altogether.
    everything_else = set(jobs) - {"ci-summary"}
    assert gathered == everything_else, (
        "every job in ci.yml must be gathered by ci-summary; "
        f"missing: {sorted(everything_else - gathered)}"
    )


def test_gathered_jobs_are_never_skipped(summary: dict, jobs: dict) -> None:
    """No gathered job may carry a condition beyond the draft guard.

    Any other job-level `if:` makes the job skippable, and a skipped required
    job reports success to branch protection. Conditions belong on the steps.
    """
    for name in summary["needs"]:
        condition = _normalise(jobs[name].get("if", DRAFT_GUARD))
        assert condition == _normalise(DRAFT_GUARD), (
            f"job '{name}' carries a job-level condition: {condition!r}. "
            "A gathered job that can be skipped reports success to branch "
            "protection -- gate its steps instead. See "
            "docs/branch-protection.md 'Gate at the step, not at the job'."
        )


def test_summary_rejects_a_non_literal_changes_output(summary: dict) -> None:
    """Empty outputs would skip every gated step while the job stays green."""
    script = summary["steps"][0]["run"]
    checked = set(re.findall(r'check_flag "([^"]+)"', script))
    assert checked == set(GATED_JOBS.values()), (
        "every output the gated jobs read must be asserted to be a literal "
        f"true/false; missing: {sorted(set(GATED_JOBS.values()) - checked)}"
    )


@pytest.mark.parametrize("job_name", sorted(GATED_JOBS))
def test_every_step_of_a_gated_job_is_guarded(jobs: dict, job_name: str) -> None:
    """A step added without a guard silently costs a docs-only PR a full run."""
    output = GATED_JOBS[job_name]
    guard = f"needs.changes.outputs.{output}"

    for step in jobs[job_name]["steps"]:
        label = step.get("name") or step.get("uses") or "<unnamed>"
        condition = step.get("if")
        assert condition is not None, (
            f"step '{label}' in job '{job_name}' has no condition; every step "
            f"of a gated job must be guarded on {guard}"
        )
        assert guard in condition, (
            f"step '{label}' in job '{job_name}' is conditional on "
            f"{condition!r}, which does not reference {guard}"
        )


@pytest.mark.parametrize("job_name", sorted(GATED_JOBS))
def test_gated_jobs_depend_on_changes(jobs: dict, job_name: str) -> None:
    needs = jobs[job_name]["needs"]
    needs = [needs] if isinstance(needs, str) else needs
    assert "changes" in needs, (
        f"job '{job_name}' reads needs.changes.outputs but does not list "
        "'changes' in its needs"
    )


def test_changes_job_can_read_the_pull_request(jobs: dict) -> None:
    """paths-filter uses the pulls.listFiles API whenever a token is present.

    Without this scope the call is a 403 and every pull request fails here.
    """
    permissions = jobs["changes"]["permissions"]
    assert permissions.get("pull-requests") == "read"
    assert permissions.get("contents") == "read"


def test_paths_filter_is_pinned_by_commit(jobs: dict) -> None:
    uses = [step["uses"] for step in jobs["changes"]["steps"] if "uses" in step]
    assert PATHS_FILTER_PIN in uses, (
        f"expected {PATHS_FILTER_PIN}, found {uses}. Bumping the action is a "
        "supply-chain decision: it gates whether the test suite runs at all."
    )


def test_code_filter_exclusions_are_exactly_as_declared(jobs: dict) -> None:
    step = _filter_step(jobs)
    filters = yaml.safe_load(step["with"]["filters"])

    excludes = {p for p in filters["code"] if p.startswith("!")}
    assert excludes == CODE_FILTER_EXCLUDES, (
        "the set of paths treated as 'not code' changed; anything listed here "
        "stops triggering the test suite on its own. "
        f"added: {sorted(excludes - CODE_FILTER_EXCLUDES)}, "
        f"removed: {sorted(CODE_FILTER_EXCLUDES - excludes)}"
    )

    # The include side has to stay open: everything is code until excluded, so
    # a newly added top-level directory runs the suite rather than skipping it.
    assert "**" in filters["code"]

    # Without this quantifier the exclusions above do not mean what they read
    # as -- the default `some` does not combine includes with excludes.
    assert step["with"]["predicate-quantifier"] == "some-with-excludes"


def test_non_pull_request_events_never_filter(jobs: dict) -> None:
    """The merge queue must behave exactly as it did before this job existed."""
    for output in GATED_JOBS.values():
        expression = jobs["changes"]["outputs"][output]
        assert "github.event_name != 'pull_request' ||" in expression, (
            f"output '{output}' must short-circuit to true on non-pull_request "
            "events so merge_group, push and workflow_dispatch run everything"
        )
        assert f"steps.filter.outputs.{output} != 'false'" in expression, (
            f"output '{output}' must use != 'false' so a missing filter output "
            "runs everything instead of silently skipping"
        )


def test_a_truncated_file_list_runs_everything(jobs: dict) -> None:
    """paths-filter cannot see past the 3000th file, and does not notice.

    It paginates pulls.listFiles without comparing the rows it got back to
    changed_files, so a larger pull request whose visible portion is all
    excluded paths yields a genuine `false` with code after the cutoff.
    """
    for output in sorted(set(GATED_JOBS.values())):
        expression = _normalise(jobs["changes"]["outputs"][output])
        assert (
            f"github.event.pull_request.changed_files > {LIST_FILES_MAX}" in expression
        ), (
            f"output '{output}' must fall back to true once the pull request "
            f"exceeds {LIST_FILES_MAX} files, which truncates the file list "
            f"paths-filter reads; got {expression!r}"
        )
        assert "github.event.pull_request.changed_files == null" in expression, (
            f"output '{output}' must also fall back to true when the file count "
            "is unavailable -- an absent count compares as 0 and would pass the "
            "size check silently"
        )


def test_frontend_filter_covers_the_readme_the_wheel_needs(jobs: dict) -> None:
    """frontend-build is the only job that builds a wheel.

    pyproject points `readme` at a root markdown file, which `code` excludes, so
    unless the frontend filter names it too, renaming it skips that build.
    """
    readme = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["readme"]
    filters = yaml.safe_load(_filter_step(jobs)["with"]["filters"])

    assert readme in filters["frontend"], (
        f"pyproject declares readme = {readme!r}, so it is a wheel build input, "
        f"but the frontend filter is {filters['frontend']}. A pull request "
        "touching only that file would skip 'Verify package bundles the "
        "frontend', the one step that builds the wheel."
    )
