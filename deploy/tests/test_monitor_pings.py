"""Heartbeat pings of deploy/backup.sh and deploy/sitecheck.sh (deploy/monitor-ping.sh).

The scripts run as subprocesses with stub commands first on PATH: curl records its arguments
(one call per line) and can fail like the real one, docker fakes pg_dump and pg_restore, age and
rclone fake the encryption and the upload. The telemetry key is random per test: it must never
show up in the scripts' output.
"""

import os
import subprocess
import uuid
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parent.parent
BACKUP = DEPLOY / "backup.sh"
SITECHECK = DEPLOY / "sitecheck.sh"
HELPER = DEPLOY / "monitor-ping.sh"

# Each stub reads its behavior from FAKE_* variables. curl logs its arguments to $CURL_LOG.
STUBS = {
    "curl": r"""#!/usr/bin/env bash
printf '%s\n' "$*" >>"$CURL_LOG"
url=${!#}
if [[ $url == */robots.txt ]]; then
    [[ ${FAKE_SITE_STATUS:-0} == 0 ]] && exit 0
    echo "curl: (22) The requested URL returned error: 502" >&2
    exit 22
fi
if [[ ${FAKE_PING_STATUS:-0} != 0 ]]; then
    echo "curl: (6) Could not resolve host: cronitor.link" >&2
    exit "$FAKE_PING_STATUS"
fi
""",
    "docker": r"""#!/usr/bin/env bash
case " $* " in
    *" pg_dump "*) echo "fake dump" ;;
    *" pg_restore "*)
        cat >/dev/null
        [[ ${FAKE_NO_TABLES:-0} == 1 ]] || echo "3; 0 0 TABLE DATA public club_player owner"
        ;;
esac
""",
    "age": r"""#!/usr/bin/env bash
while [[ $# -gt 0 ]]; do
    if [[ $1 == --output ]]; then echo "encrypted" >"$2"; fi
    shift
done
""",
    "rclone": r"""#!/usr/bin/env bash
exit "${FAKE_RCLONE_STATUS:-0}"
""",
}


@pytest.fixture
def key():
    return uuid.uuid4().hex


@pytest.fixture
def env(tmp_path):
    """Stubs on PATH, an APP_DIR for the .env, a BACKUP_DIR, the curl log."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in STUBS.items():
        stub = bin_dir / name
        stub.write_text(body)
        stub.chmod(0o755)
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    return {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "APP_DIR": str(app_dir),
        "BACKUP_DIR": str(tmp_path / "backups"),
        "CURL_LOG": str(tmp_path / "curl.log"),
    }


def write_env(env, **settings):
    values = {
        "SITE_DOMAIN": "club.example.com",
        "AGE_RECIPIENT": "age1example",
        "B2_BUCKET": "bucket",
        "B2_KEY_ID": "key-id",
        "B2_APPLICATION_KEY": "application-key",
        **settings,
    }
    text = "".join(f"{name}='{value}'\n" for name, value in values.items())
    (Path(env["APP_DIR"]) / ".env").write_text(text)


def run(script, env, *args, **fake):
    full_env = {**env, **{f"FAKE_{name.upper()}": str(value) for name, value in fake.items()}}
    return subprocess.run([script, *args], env=full_env, capture_output=True, text=True, timeout=60)


def curl_calls(env):
    log = Path(env["CURL_LOG"])
    return log.read_text().splitlines() if log.exists() else []


def pinged_urls(env):
    """The URL of every curl call except the site check's own request."""
    urls = [call.split()[-1] for call in curl_calls(env)]
    return [url for url in urls if not url.endswith("/robots.txt")]


def assert_key_hidden(result, key):
    assert key not in result.stdout
    assert key not in result.stderr


def monitor_url(key, name):
    return f"https://cronitor.link/p/{key}/{name}"


# --- the helper ---


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://cronitor.link/p/K/m", "https://cronitor.link/p/K/m?state=fail"),
        ("https://cronitor.link/p/K/m?env=prod", "https://cronitor.link/p/K/m?env=prod&state=fail"),
        ("https://cronitor.link/p/K/m?", "https://cronitor.link/p/K/m?state=fail"),
    ],
)
def test_failure_url_adds_state_to_the_query(url, expected, env):
    subprocess.run(
        ["bash", "-c", f'. "{HELPER}"; monitor_ping "$1" fail', "bash", url],
        env=env,
        check=True,
        timeout=60,
    )
    assert pinged_urls(env) == [expected]


def test_success_url_is_unchanged(env):
    url = "https://cronitor.link/p/K/m?env=prod"
    subprocess.run(
        ["bash", "-c", f'. "{HELPER}"; monitor_ping "$1"', "bash", url],
        env=env,
        check=True,
        timeout=60,
    )
    assert pinged_urls(env) == [url]


# --- backup.sh ---


def test_backup_pings_once_after_the_upload(env, key):
    url = monitor_url(key, "traktorist-backup")
    write_env(env, MONITOR_BACKUP_URL=url)
    result = run(BACKUP, env)
    assert result.returncode == 0, result.stderr
    assert "Uploaded b2:bucket/" in result.stdout
    assert pinged_urls(env) == [url]
    assert_key_hidden(result, key)


def test_failed_upload_sends_one_failure_ping(env, key):
    url = monitor_url(key, "traktorist-backup")
    write_env(env, MONITOR_BACKUP_URL=url)
    result = run(BACKUP, env, rclone_status=1)
    assert result.returncode != 0
    assert "Backup FAILED" in result.stderr
    assert pinged_urls(env) == [f"{url}?state=fail"]
    assert_key_hidden(result, key)


def test_failed_dump_check_sends_one_failure_ping(env, key):
    url = monitor_url(key, "traktorist-backup")
    write_env(env, MONITOR_BACKUP_URL=url)
    result = run(BACKUP, env, no_tables=1)
    assert result.returncode != 0
    assert "no table data" in result.stderr
    assert pinged_urls(env) == [f"{url}?state=fail"]
    assert_key_hidden(result, key)


def test_failure_ping_with_a_query_uses_an_ampersand(env, key):
    url = monitor_url(key, "traktorist-backup") + "?env=production"
    write_env(env, MONITOR_BACKUP_URL=url)
    result = run(BACKUP, env, rclone_status=1)
    assert result.returncode != 0
    assert pinged_urls(env) == [f"{url}&state=fail"]


def test_failing_ping_does_not_fail_the_backup_or_print_the_url(env, key):
    write_env(env, MONITOR_BACKUP_URL=monitor_url(key, "traktorist-backup"))
    result = run(BACKUP, env, ping_status=6)
    assert result.returncode == 0, result.stderr
    assert "Backup complete." in result.stdout
    assert "curl: (6) Could not resolve host" in result.stderr
    assert_key_hidden(result, key)


def test_failing_failure_ping_does_not_print_the_url(env, key):
    write_env(env, MONITOR_BACKUP_URL=monitor_url(key, "traktorist-backup"))
    result = run(BACKUP, env, rclone_status=1, ping_status=6)
    assert result.returncode != 0
    assert len(pinged_urls(env)) == 1
    assert "curl: (6) Could not resolve host" in result.stderr
    assert_key_hidden(result, key)


@pytest.mark.parametrize("rclone_status", [0, 1])
def test_backup_without_a_url_makes_no_call(env, rclone_status):
    write_env(env, MONITOR_BACKUP_URL="")
    result = run(BACKUP, env, rclone_status=rclone_status)
    assert (result.returncode == 0) == (rclone_status == 0), result.stderr
    assert curl_calls(env) == []


@pytest.mark.parametrize("no_tables", [0, 1])
def test_local_only_makes_no_call(env, key, no_tables):
    write_env(env, MONITOR_BACKUP_URL=monitor_url(key, "traktorist-backup"))
    result = run(BACKUP, env, "--local-only", no_tables=no_tables)
    assert (result.returncode == 0) == (no_tables == 0), result.stderr
    assert curl_calls(env) == []


# --- sitecheck.sh ---


def test_site_up_sends_the_heartbeat(env, key):
    url = monitor_url(key, "traktorist-site")
    write_env(env, MONITOR_SITE_URL=url)
    result = run(SITECHECK, env)
    assert result.returncode == 0, result.stderr
    assert pinged_urls(env) == [url]
    assert_key_hidden(result, key)


def test_site_down_sends_a_failure_ping(env, key):
    url = monitor_url(key, "traktorist-site")
    write_env(env, MONITOR_SITE_URL=url)
    result = run(SITECHECK, env, site_status=22)
    assert result.returncode == 0, result.stderr
    assert pinged_urls(env) == [f"{url}?state=fail"]
    assert_key_hidden(result, key)


def test_site_down_with_a_query_uses_an_ampersand(env, key):
    url = monitor_url(key, "traktorist-site") + "?env=production"
    write_env(env, MONITOR_SITE_URL=url)
    run(SITECHECK, env, site_status=22)
    assert pinged_urls(env) == [f"{url}&state=fail"]


def test_site_failing_ping_does_not_print_the_url(env, key):
    write_env(env, MONITOR_SITE_URL=monitor_url(key, "traktorist-site"))
    result = run(SITECHECK, env, site_status=22, ping_status=6)
    assert result.returncode == 0, result.stderr
    assert "curl: (6) Could not resolve host" in result.stderr
    assert_key_hidden(result, key)


def test_sitecheck_without_a_url_makes_no_call(env):
    write_env(env, MONITOR_SITE_URL="")
    result = run(SITECHECK, env)
    assert result.returncode == 0, result.stderr
    assert curl_calls(env) == []
