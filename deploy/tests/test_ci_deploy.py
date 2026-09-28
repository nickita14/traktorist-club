"""deploy/ci-deploy.sh, the forced command of the CI deploy key.

The script runs as a subprocess, as sshd would run it: the request in SSH_ORIGINAL_COMMAND,
the checkout in APP_DIR. The flow tests use a throwaway "origin" repository and a clone whose
deploy/server-deploy.sh is a stub that prints the commit it runs at.
"""

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "ci-deploy.sh"
REFUSED = "refused: expected a 40-character commit SHA\n"
SHA = "0123456789abcdef0123456789abcdef01234567"
STUB = '#!/usr/bin/env bash\necho "SERVER_DEPLOY $(git rev-parse HEAD)"\n'

# Git must not read the developer's config (commit signing, hooks, default branch).
GIT_ENV = {
    "PATH": os.environ["PATH"],
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}


def run(request, app_dir, tmp_path):
    env = {
        **GIT_ENV,
        "HOME": str(tmp_path),
        "APP_DIR": str(app_dir),
        "LOGGER": "true",
        "SSH_CONNECTION": "203.0.113.5 50000 198.51.100.1 22",
    }
    if request is not None:
        env["SSH_ORIGINAL_COMMAND"] = request
    return subprocess.run([SCRIPT], env=env, capture_output=True, text=True, timeout=60)


def git(cwd, *args):
    result = subprocess.run(
        ["git", *args], cwd=cwd, env=GIT_ENV, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def commit(work, message):
    (work / "notes.txt").write_text(message + "\n")
    git(work, "add", "notes.txt")
    git(work, "commit", "--quiet", "-m", message)
    return git(work, "rev-parse", "HEAD")


@pytest.mark.parametrize(
    "request_",
    [
        None,
        "",
        " ",
        SHA[:-1],
        SHA + "8",
        SHA.upper(),
        "0123456789ABCDEF0123456789abcdef01234567",
        SHA + "\n",
        "\n" + SHA,
        " " + SHA,
        SHA + " ",
        "\t" + SHA,
        f"deploy {SHA}",
        f"{SHA}; rm -rf /",
        f"{SHA}\n{SHA}",
        f"{SHA} && id",
        "$(id)" + SHA[:35],
        "`id`" + SHA[:36],
        "../" + SHA[:37],
        "origin/main",
        "HEAD",
        "main",
        "g123456789abcdef0123456789abcdef01234567",
        "0123456789abcdef0123456789abcdef0123456z",
        SHA + SHA[:24],  # a SHA-256 object name
        "０123456789abcdef0123456789abcdef01234567",  # a full-width digit
    ],
)
def test_malformed_request_is_refused_before_anything_runs(request_, tmp_path):
    # APP_DIR does not exist: getting past validation would fail differently (cd, exit 1).
    result = run(request_, tmp_path / "missing", tmp_path)
    assert result.returncode == 2
    assert result.stderr == REFUSED
    assert result.stdout == ""


@pytest.fixture
def repos(tmp_path):
    """Origin: two commits on main, one on a side branch. The server clone is at the first."""
    origin, work, app = tmp_path / "origin.git", tmp_path / "work", tmp_path / "app"
    git(tmp_path, "init", "--quiet", "--bare", "--initial-branch=main", str(origin))
    git(tmp_path, "clone", "--quiet", str(origin), str(work))
    stub = work / "deploy" / "server-deploy.sh"
    stub.parent.mkdir()
    stub.write_text(STUB)
    stub.chmod(0o755)
    git(work, "add", "deploy")
    first = commit(work, "first")
    second = commit(work, "second")
    git(work, "push", "--quiet", "origin", "main")
    git(work, "switch", "--quiet", "-c", "side", first)
    side = commit(work, "side")
    git(work, "push", "--quiet", "origin", "side")
    git(tmp_path, "clone", "--quiet", str(origin), str(app))
    git(app, "checkout", "--quiet", "--detach", first)
    return {"app": app, "first": first, "second": second, "side": side}


def test_commit_on_main_is_deployed(repos, tmp_path):
    result = run(repos["second"], repos["app"], tmp_path)
    assert result.returncode == 0, result.stderr
    assert f"SERVER_DEPLOY {repos['second']}" in result.stdout
    assert git(repos["app"], "rev-parse", "HEAD") == repos["second"]


def test_current_commit_is_deployed_again(repos, tmp_path):
    result = run(repos["first"], repos["app"], tmp_path)
    assert result.returncode == 0, result.stderr
    assert f"SERVER_DEPLOY {repos['first']}" in result.stdout


def test_older_commit_is_skipped(repos, tmp_path):
    git(repos["app"], "checkout", "--quiet", "--detach", repos["second"])
    result = run(repos["first"], repos["app"], tmp_path)
    assert result.returncode == 0, result.stderr
    assert "already at a newer commit" in result.stdout
    assert "SERVER_DEPLOY" not in result.stdout
    assert git(repos["app"], "rev-parse", "HEAD") == repos["second"]


def test_commit_off_main_is_refused(repos, tmp_path):
    result = run(repos["side"], repos["app"], tmp_path)
    assert result.returncode == 1
    assert "is not on origin/main" in result.stderr
    assert "SERVER_DEPLOY" not in result.stdout
    assert git(repos["app"], "rev-parse", "HEAD") == repos["first"]


def test_unknown_commit_is_refused(repos, tmp_path):
    result = run(SHA, repos["app"], tmp_path)
    assert result.returncode == 1
    assert "is not a commit in origin" in result.stderr
    assert "SERVER_DEPLOY" not in result.stdout


def test_local_changes_are_refused(repos, tmp_path):
    (repos["app"] / "notes.txt").write_text("edited on the server\n")
    result = run(repos["second"], repos["app"], tmp_path)
    assert result.returncode == 1
    assert "local changes" in result.stderr
    assert "SERVER_DEPLOY" not in result.stdout
    assert git(repos["app"], "rev-parse", "HEAD") == repos["first"]
