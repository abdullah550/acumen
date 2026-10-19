"""scripts/keep_report.sh keeps a workflow's log on a branch of its own. The tests push to a local bare repository."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "keep_report.sh"

pytestmark = pytest.mark.skipif(shutil.which("git") is None or shutil.which("bash") is None, reason="needs git and bash")


def git(*args: str, cwd: Path | str | None = None) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return done.stdout.strip()


def keep(tmp_path: Path, remote: Path, branch: str, name: str, text: str, env_extra: dict[str, str] | None = None, *extra: str) -> subprocess.CompletedProcess[str]:
    """Run the script from a folder that is itself a git checkout, the way the workflows do."""
    checkout = tmp_path / "checkout"
    checkout.mkdir(exist_ok=True)
    if not (checkout / ".git").exists():
        git("init", "-q", "-b", "main", cwd=checkout)
    (checkout / name).write_text(text)
    env = {k: v for k, v in os.environ.items() if k not in ("COINGECKO_API_KEY", "ACUMEN_PASSPHRASE", "ACUMEN_CONTACT")}
    env["KEEP_REPORT_REMOTE"] = str(remote)
    env.update(env_extra or {})
    return subprocess.run(["bash", str(SCRIPT), branch, name, *extra], cwd=checkout, env=env, capture_output=True, text=True)


@pytest.fixture()
def remote(tmp_path: Path) -> Path:
    path = tmp_path / "remote.git"
    git("init", "-q", "--bare", str(path))
    return path


def test_the_report_lands_on_its_own_branch_as_a_single_commit(tmp_path, remote):
    done = keep(tmp_path, remote, "report-doctor", "doctor.txt", "Acumen doctor\nverdict: ok\n")
    assert done.returncode == 0, done.stderr
    assert git("show", "report-doctor:doctor.txt", cwd=remote) == "Acumen doctor\nverdict: ok"
    assert git("rev-list", "--count", "report-doctor", cwd=remote) == "1"
    assert git("ls-tree", "--name-only", "report-doctor", cwd=remote) == "doctor.txt"
    assert git("branch", "--list", cwd=remote).split() == ["report-doctor"]  # nothing else was pushed


def test_a_second_report_replaces_the_first_instead_of_piling_up(tmp_path, remote):
    assert keep(tmp_path, remote, "report-doctor", "doctor.txt", "first\n").returncode == 0
    assert keep(tmp_path, remote, "report-doctor", "doctor.txt", "second\n").returncode == 0
    assert git("show", "report-doctor:doctor.txt", cwd=remote) == "second"
    assert git("rev-list", "--count", "report-doctor", cwd=remote) == "1"


def test_secrets_and_the_contact_address_never_reach_the_branch(tmp_path, remote):
    text = "user agent: AcumenPersonalAnalytics/1.0 (+me@example.org)\nGET /coins?key=CG-abc123XYZ failed\nphrase correct horse battery staple\n"
    env = {"ACUMEN_CONTACT": "me@example.org", "COINGECKO_API_KEY": "CG-abc123XYZ", "ACUMEN_PASSPHRASE": "correct horse battery staple"}
    assert keep(tmp_path, remote, "report-refresh", "refresh.txt", text, env).returncode == 0
    kept = git("show", "report-refresh:refresh.txt", cwd=remote)
    for secret in env.values():
        assert secret not in kept
    assert "(acumen_contact)" in kept and "(coingecko_api_key)" in kept and "(acumen_passphrase)" in kept
    assert "GET /coins?key=" in kept  # the rest of the line is still there to read


def test_a_long_log_keeps_its_end(tmp_path, remote):
    text = "".join(f"line {i}\n" for i in range(1, 101))
    assert keep(tmp_path, remote, "report-refresh", "refresh.txt", text, None, "10").returncode == 0
    kept = git("show", "report-refresh:refresh.txt", cwd=remote).splitlines()
    assert kept == [f"line {i}" for i in range(91, 101)]


def test_an_empty_report_pushes_nothing(tmp_path, remote):
    done = keep(tmp_path, remote, "report-doctor", "doctor.txt", "")
    assert done.returncode == 0 and "nothing to keep" in done.stdout
    assert git("branch", "--list", cwd=remote) == ""


def test_a_missing_argument_is_a_usage_error(tmp_path):
    done = subprocess.run(["bash", str(SCRIPT)], cwd=tmp_path, capture_output=True, text=True)
    assert done.returncode != 0 and "usage" in done.stderr
