"""deploy/server-deploy.sh, the server half of every deploy.

The script runs as a subprocess with a fake `docker` first on PATH that records each call and
fails the ones a test asks it to. The point: a failed image build (deb.debian.org unreachable at
the apt-get upgrade layer, say) stops the deploy before anything touches the running containers,
and the script exits non-zero so the CI deploy job goes red.
"""

import os
import re
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "server-deploy.sh"

FAKE_DOCKER = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$DOCKER_LOG"
if [[ -n ${DOCKER_FAIL:-} && "$*" == *"$DOCKER_FAIL"* ]]; then
    echo "fake docker: failing $*" >&2
    exit 17
fi
"""


def run(tmp_path, fail=None):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(0o755)
    log = tmp_path / "docker.log"
    log.touch()
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "APP_DIR": str(tmp_path / "app"),
        "BACKUP_DIR": str(tmp_path / "backups"),
        "DOCKER_LOG": str(log),
    }
    if fail:
        env["DOCKER_FAIL"] = fail
    result = subprocess.run([SCRIPT], env=env, capture_output=True, text=True, timeout=60)
    return result, log.read_text().splitlines()


def test_failed_build_stops_the_deploy(tmp_path):
    result, calls = run(tmp_path, fail=" build ")

    assert result.returncode == 17
    # The build was the only docker call: no pull, check, migrate, up, reload or prune.
    assert len(calls) == 1
    assert " build --pull " in calls[0]
    assert "==> Starting" not in result.stdout
    assert "==> Pruning" not in result.stdout


def test_build_comes_first_with_the_daily_apt_refresh(tmp_path):
    result, calls = run(tmp_path)

    assert result.returncode == 0, result.stderr
    assert re.search(r" build --pull --build-arg APT_REFRESH=\d{4}-\d{2}-\d{2} web$", calls[0])
    assert any(" up -d --wait --remove-orphans" in call for call in calls[1:])
    assert calls[-1].startswith("builder prune")
