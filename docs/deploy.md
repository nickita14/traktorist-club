# Deploy runbook

This guide takes the site from a fresh VPS to production, then covers routine work: deploys, backups, restores and organizer accounts. Run every command yourself. Commands marked **local** run on your own machine (WSL); commands marked **server** run over SSH as `deploy`.

| | |
|---|---|
| Server | RackNerd KVM VPS: 1 vCPU, 1 GB RAM, 20 GB SSD, Ubuntu 24.04, one IPv4 |
| Domain | `traktorist.duckdns.org` (DuckDNS A record points to the server) |
| Code on the server | `/srv/traktorist` (a clone of the public GitHub repo, owned by `deploy`) |
| Settings on the server | `/srv/traktorist/.env` (mode 600, template `.env.prod.example`, never committed) |
| Backups on the server | `/var/backups/traktorist` (mode 700, 14 nightly dumps) |
| Off-site backups | Backblaze B2 bucket, age-encrypted, kept 30 days |
| Monitoring | healthchecks.io: nightly backup (and optionally the site) |

The stack (`compose.prod.yaml`) has three containers:
- **`caddy`** (ports 80/443): HTTPS with Let's Encrypt, security headers, and `/static/`.
- **`web`**: Django on gunicorn with 2 workers, as a non-root user, reachable only by Caddy.
- **`db`**: Postgres 17 on an internal network with no ports and no internet access.

Replace `SERVER_IP` below with the IP address from the RackNerd welcome email.

Contents:

1. [Before you start](#1-before-you-start)
2. [Server setup](#2-server-setup)
3. [Settings file](#3-settings-file)
4. [First deploy](#4-first-deploy)
5. [Admin accounts](#5-admin-accounts)
6. [Backups](#6-backups)
7. [Restore](#7-restore)
8. [First data load](#8-first-data-load)
9. [Routine operations](#9-routine-operations)
10. [Enabling 2FA later (optional)](#10-enabling-2fa-later-optional)
11. [Resource budget](#11-resource-budget)
12. [Automatic deploys from GitHub](#12-automatic-deploys-from-github)

## 1. Before you start

- [ ] **Local:** the Stage 6 commits are pushed to `main` on GitHub. The server deploys what is on GitHub, not what is on your disk.
- [ ] **Local:** you have an SSH key. Check with `ls ~/.ssh/id_ed25519.pub`. If it is missing, create one with `ssh-keygen -t ed25519`.
- [ ] **Local:** the domain resolves to the server:

  ```bash
  getent ahostsv4 traktorist.duckdns.org   # must print SERVER_IP
  getent ahostsv6 traktorist.duckdns.org   # should print nothing
  ```

  If the name has an IPv6 (AAAA) address that is not this server's, Let's Encrypt tries it first and certificate issuance fails. Clear the IPv6 field on the DuckDNS page.
- [ ] Keep the RackNerd root password in your password manager. It is not used over SSH after section 2.4, but it is your way into the **VNC console** in the RackNerd control panel (SolusVM) if SSH ever locks you out.

## 2. Server setup

### 2.1 First login and updates

**Local:**

```bash
ssh root@SERVER_IP          # password from the welcome email
```

**Server (as root):**

```bash
apt update && apt full-upgrade -y
timedatectl set-timezone Europe/Chisinau
hostnamectl set-hostname traktorist
timedatectl                 # "System clock synchronized: yes" (2FA codes, if enabled, need it)
```

If `timedatectl` says the clock is not synchronized, run `systemctl enable --now systemd-timesyncd` and check again.

If the upgrade installed a new kernel, run `reboot`, wait a minute, and log in again as root.

### 2.2 The `deploy` user

**Server (as root):**

```bash
adduser deploy              # set a strong password: sudo asks for it
usermod -aG sudo deploy
```

### 2.3 Copy your SSH key

**Local:**

```bash
ssh-copy-id -i ~/.ssh/id_ed25519.pub deploy@SERVER_IP    # asks for deploy's password once
```

### 2.4 Lock down SSH (without locking yourself out)

Keep the **root session from 2.1 open** until the end of this section. If anything goes wrong, fix it from there.

**Step 1: prove that key login and sudo work.** Open a **second local terminal**:

```bash
ssh -t -o PreferredAuthentications=publickey -o PasswordAuthentication=no deploy@SERVER_IP \
    'sudo -v && echo KEY_OK'
```

It must log in without asking for an SSH password (sudo asks for deploy's password) and print `KEY_OK`. **Do not continue until it does.**

**Step 2: get the repository.** It holds the config files used from here on. Log in as deploy (**local:** `ssh deploy@SERVER_IP`), then **server:**

```bash
sudo install -d -o deploy -g deploy /srv/traktorist
sudo install -d -o deploy -g deploy -m 700 /var/backups/traktorist
git clone https://github.com/nickita14/traktorist-club.git /srv/traktorist
```

**Step 3: install the SSH hardening file** (key-only, no root, only `deploy`). **Server:**

```bash
sudo install -m 644 /srv/traktorist/deploy/host/00-hardening.conf /etc/ssh/sshd_config.d/
sudo sshd -t && echo CONFIG_OK
sudo sshd -T | grep -Ei '^(passwordauthentication|kbdinteractiveauthentication|permitrootlogin|allowusers) '
```

Expected output:

```
CONFIG_OK
permitrootlogin no
passwordauthentication no
kbdinteractiveauthentication no
allowusers deploy
```

If `passwordauthentication` still says `yes`, another file in `/etc/ssh/sshd_config.d/` with a lower number sets it. Look at `ls /etc/ssh/sshd_config.d/` and fix it before going on.

Apply the new config (open sessions stay connected):

```bash
sudo systemctl restart ssh
```

**Step 4: check from new local terminals**, keeping the root session open:

```bash
ssh deploy@SERVER_IP 'echo KEY_LOGIN_OK'                    # prints KEY_LOGIN_OK
ssh -o PubkeyAuthentication=no deploy@SERVER_IP             # "Permission denied (publickey)."
ssh root@SERVER_IP                                          # "Permission denied (publickey)."
```

Only when all three behave as shown, close the root session.

### 2.5 Firewall

**Server:**

```bash
sudo apt install -y ufw
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443          # tcp for HTTPS, udp for HTTP/3
sudo ufw enable             # answer y; your SSH session stays up
sudo ufw status verbose
```

Docker publishes container ports through its own iptables rules, which bypass ufw. That is harmless here: only Caddy publishes ports (80 and 443, open anyway), and Postgres publishes none. Never add a `ports:` entry to `db` or `web`.

### 2.6 Automatic security updates

**Server:**

```bash
sudo apt install -y unattended-upgrades
printf 'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\n' \
    | sudo tee /etc/apt/apt.conf.d/20auto-upgrades
sudo install -m 644 /srv/traktorist/deploy/host/52unattended-upgrades-local /etc/apt/apt.conf.d/
sudo unattended-upgrade --dry-run --debug 2>&1 | grep -E 'Allowed origins|Docker'
```

The `Allowed origins` line must list `...-security` and `origin=Docker`.

What happens now:
- Security updates install daily.
- When one needs a reboot (kernel, libc), the server reboots at **05:00 Chisinau time**, and the containers come back by their restart policy.
- Docker's own packages update from the Docker repository too.

### 2.7 Swap

**Server:**

```bash
swapon --show               # if a swap device of 1 GB or more is already listed, skip this block
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/99-swap.conf
sudo sysctl --system >/dev/null
free -h                     # Swap: 2.0Gi
```

### 2.8 Journal size

**Server:**

```bash
sudo install -D -m 644 /srv/traktorist/deploy/host/journald-size.conf \
    /etc/systemd/journald.conf.d/size.conf
sudo systemctl restart systemd-journald
```

### 2.9 Docker (official repository)

**Server:**

```bash
sudo apt install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Signed-By: /etc/apt/keyrings/docker.asc
EOF
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Log rotation for every container: 3 files of 10 MB each.
sudo install -m 644 /srv/traktorist/deploy/host/daemon.json /etc/docker/daemon.json
sudo systemctl restart docker

sudo usermod -aG docker deploy
exit                        # log out and back in so the group applies
```

**Local:** `ssh deploy@SERVER_IP`, then **server:**

```bash
docker run --rm hello-world | grep 'Hello from Docker'
docker info --format '{{.LoggingDriver}}'      # json-file
```

Membership in the `docker` group is equivalent to root. That is acceptable because `deploy` is the only login.

A shortcut for the commands below. **Server:**

```bash
echo "alias dc='docker compose --project-directory /srv/traktorist -f /srv/traktorist/compose.prod.yaml'" >> ~/.bashrc
source ~/.bashrc
```

## 3. Settings file

**Server:**

```bash
cd /srv/traktorist
cp .env.prod.example .env
chmod 600 .env
# Generate the three secrets:
python3 -c 'import secrets; print("SECRET_KEY=" + secrets.token_urlsafe(50))'
python3 -c 'import secrets; print("POSTGRES_PASSWORD=" + secrets.token_urlsafe(32))'
python3 -c 'import secrets; print("ADMIN_URL=" + secrets.token_hex(6) + "/")'
nano .env
```

In `.env`:
- Paste the three generated lines over the placeholders.
- Set `SITE_DOMAIN=traktorist.duckdns.org`.
- Leave the backup values as placeholders for now; section 6 fills them in.

Then save a copy of the whole `.env` in your password manager. It holds the only copy of the B2 key and the admin URL.

`ENVIRONMENT` is not in this file on purpose: `compose.prod.yaml` sets it to `production`, and the image refuses to start with anything else.

## 4. First deploy

**Local**, from the repository root. First accept the server's host key once:

```bash
ssh deploy@traktorist.duckdns.org true
deploy/deploy.sh
```

The script does the following:
1. Checks out `origin/main` on the server.
2. Builds the image. The first build takes a few minutes; later ones use the cache.
3. Runs `check --deploy`.
4. Creates the database and runs the migrations.
5. Starts the three containers, waiting until they are healthy.
6. From your machine, checks that the site answers 200 over HTTPS with HSTS and that HTTP redirects to HTTPS.

The last lines read:

```
==> Smoke check passed: home 200, robots.txt 200 with HSTS, HTTP redirects to HTTPS
==> Deployed origin/main
```

On the very first start, Caddy needs a few seconds to get the certificate. If the smoke check fails with a TLS error:
1. Wait a minute and run `deploy/deploy.sh` again (it is idempotent).
2. If it still fails, look at the certificate log. **Server:**

   ```bash
   dc logs caddy | grep -iE 'certificate|error'
   ```

   It should show `certificate obtained successfully`. Common causes: port 80 closed (check `sudo ufw status`), or DNS pointing elsewhere (section 1).

No email is configured for Let's Encrypt: it no longer sends expiry notices, and Caddy renews certificates by itself about 30 days before they expire.

## 5. Admin accounts

The admin lives at `https://traktorist.duckdns.org/<ADMIN_URL>` (the value in `.env`). Login is by username and password. Two-factor login is built in but off; section 10 turns it on.

**Server**, to create your own account:

```bash
dc exec web python manage.py createsuperuser
```

For each **organizer**, open Users in the admin, add the user, tick "Staff status" and add them to the **Organizer** group.

**Lockout:** 5 failed logins for the same username from the same address lock that pair out for an hour. The page says "Слишком много неудачных попыток входа". To unlock early:

```bash
dc exec web python manage.py axes_list_attempts
dc exec web python manage.py axes_reset_username <username>
```

## 6. Backups

The nightly job (`deploy/backup.sh`, 03:30 server time) does this:
1. Dumps the database into `/var/backups/traktorist`.
2. Verifies that `pg_restore` can read the dump.
3. Keeps the newest 14 dumps.
4. Encrypts the dump with your **age** public key and uploads it to **Backblaze B2** with a key that cannot delete anything.
5. Only then pings **healthchecks.io**.

If a night fails, the job pings the check's `/fail` URL right away. If the timer never runs at all, healthchecks.io emails you 26 hours after the last ping.

### 6.1 Encryption key (local)

**Local:**

```bash
sudo apt install -y age
mkdir -p ~/.config/traktorist && chmod 700 ~/.config/traktorist
age-keygen -o ~/.config/traktorist/backup-key.txt
chmod 600 ~/.config/traktorist/backup-key.txt
```

The command prints `Public key: age1...`. That public key goes into `.env` as `AGE_RECIPIENT`.

The private key file stays on your machine and **never goes to the server**. A stolen server therefore cannot read old backups. Put a copy of `backup-key.txt` in your password manager: without it, every off-site backup is unreadable.

### 6.2 Backblaze B2 bucket and key

Sign up at https://www.backblaze.com/sign-up/cloud-storage. The first 10 GB are free, and these backups use a few MB.

**1. Buckets → Create a Bucket:**

| Setting | Value |
|---|---|
| Bucket Unique Name | something unguessable, e.g. `traktorist-bk-` + 8 random characters |
| Files in Bucket are | **Private** |
| Default Encryption | **Enable** |
| Object Lock | **Disable** |

**2. On the new bucket, Lifecycle Settings → "Use custom lifecycle rules" → Add Rule:**

| Setting | Value |
|---|---|
| File Path | (empty: the whole bucket) |
| Days Till Hide | **30** |
| Days Till Delete | **1** |

Each copy is kept about 31 days, then removed by B2 itself. The server never needs delete rights.

**3. Application Keys → Add a New Application Key:**

| Setting | Value |
|---|---|
| Name of Key | `traktorist-server` |
| Allow access to Bucket(s) | **only this bucket** |
| Type of Access | **Write Only** (no read, no list, no delete) |
| Allow List All Bucket Names | unchecked |
| File name prefix, Duration | empty |

B2 shows `keyID` and `applicationKey` **once**. Put them into the server's `.env` as `B2_KEY_ID` and `B2_APPLICATION_KEY`, together with `B2_BUCKET`. Update the password-manager copy of `.env`.

The upload uses `rclone copyto --no-check-dest`, so rclone never needs to list or read the bucket. This is the one piece that could not be tested against real B2 before the first run. If the first manual run (6.5) fails with a 401 or 403 error from B2:
1. Delete the key.
2. Create one with the B2 command-line tool and exactly these capabilities: `listBuckets,writeFiles`. If that still fails, use `listBuckets,listFiles,writeFiles`. Never `deleteFiles`.

### 6.3 Dead man's switch (healthchecks.io)

1. Sign up at https://healthchecks.io (the free plan allows 20 checks). Email alerts go to your account address by default.
2. **Add Check:**
   - Name: `traktorist backup`
   - Schedule: **Simple**
   - Period: **1 day**
   - Grace Time: **2 hours**

   With these values, no successful ping for 26 hours means an email.
3. Copy the ping URL (`https://hc-ping.com/<uuid>`) into `.env` as `HEALTHCHECKS_BACKUP_URL`.

### 6.4 Tools on the server

**Server:**

```bash
sudo apt install -y age rclone
```

### 6.5 First run by hand

**Server:**

```bash
/srv/traktorist/deploy/backup.sh
```

Expected output:

```
Dump: /var/backups/traktorist/traktorist-2026-..._...dump (..K, 19 tables)
Uploaded b2:<bucket>/traktorist-2026-..._....dump.age
Backup complete.
```

Then check both ends:
- The file appears under Browse Files in B2.
- The check turns green ("up") on healthchecks.io.

Before B2 is set up, `deploy/backup.sh --local-only` does steps 1 to 3 only (no upload, no ping).

### 6.6 Nightly timer

**Server:**

```bash
sudo cp /srv/traktorist/deploy/systemd/traktorist-backup.service \
        /srv/traktorist/deploy/systemd/traktorist-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now traktorist-backup.timer
systemctl list-timers traktorist-backup.timer      # NEXT: tonight around 03:30
sudo systemctl start traktorist-backup.service     # one run through systemd, as the timer will do it
journalctl -u traktorist-backup.service -n 20 --no-pager
```

The unit files live in the repository. If a deploy changes them, copy them again and run `daemon-reload`.

### 6.7 Optional: site uptime check

healthchecks.io only waits for pings; it does not visit the site. `deploy/sitecheck.sh` runs on the server every 5 minutes: when `https://traktorist.duckdns.org/robots.txt` answers 200, it pings a second check. If the app, Caddy, the certificate or the whole server goes down, the pings stop and you get an email.

It can't catch problems that only outside visitors see (DuckDNS resolving elsewhere, the provider's network). For that, add a free external monitor such as UptimeRobot on the same URL.

1. On healthchecks.io, add a check `traktorist site`: Simple, Period **5 minutes**, Grace **10 minutes**.
2. Put its URL into `.env` as `HEALTHCHECKS_SITE_URL`.
3. **Server:**

   ```bash
   sudo cp /srv/traktorist/deploy/systemd/traktorist-sitecheck.service \
           /srv/traktorist/deploy/systemd/traktorist-sitecheck.timer /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now traktorist-sitecheck.timer
   ```

The unattended reboot at 05:00 takes about a minute, well inside the 10-minute grace time.

## 7. Restore

### 7.1 Drill (safe, changes nothing)

This restores a dump into a scratch database, compares row counts with the live database, and drops the scratch database. **Server:**

```bash
/srv/traktorist/deploy/restore.sh "$(ls -t /var/backups/traktorist/traktorist-*.dump | head -1)"
```

Expected output:

```
Rows per table (dump / live):
  club_player                  NN / NN
  club_season                   N / N
  club_game                   NNN / NNN
  club_result                 NNN / NNN
  auth_user                     N / N
  otp_totp_totpdevice           N / N
Restore drill OK: the dump restores cleanly and matches the live database.
```

If the dump is older than today's edits, some counts differ and the script says so. That is expected. Run a drill after the first data load and then once a month.

### 7.2 Replace the live database

**Server:**

```bash
/srv/traktorist/deploy/restore.sh --replace /var/backups/traktorist/traktorist-<date>.dump
```

The script:
1. Asks you to type `restore`.
2. Saves a safety dump of the current database in `/var/backups/traktorist/pre-restore/`.
3. Stops `web`.
4. Restores in a single transaction: all or nothing.
5. Starts `web` again and prints the row counts.

### 7.3 From the off-site copy

This is for when the server's own dumps are gone.

1. In B2, open **Browse Files**, pick the newest `traktorist-<date>.dump.age` and click **Download**. The server's key cannot read the bucket; your B2 web login can.
2. **Local:** decrypt the file and send it to the server:

   ```bash
   age -d -i ~/.config/traktorist/backup-key.txt -o traktorist.dump ~/Downloads/traktorist-<date>.dump.age
   scp traktorist.dump deploy@traktorist.duckdns.org:/var/backups/traktorist/from-offsite.dump
   shred -u traktorist.dump
   ```

3. **Server:** first run the drill (7.1) on `/var/backups/traktorist/from-offsite.dump`, then `--replace` (7.2).

### 7.4 The whole server is lost

1. On a new VPS, repeat sections 2 to 4.
2. Restore `.env` from your password manager. A new `POSTGRES_PASSWORD` is fine; a new `SECRET_KEY` only logs everyone out.
3. Restore from off-site (7.3) with `--replace`.

The dump includes users (and their 2FA devices, if 2FA is on), so everyone logs in as before.

## 8. First data load

Real data never goes into the repository or the image. It visits the server only for the duration of the import.

**Server**, take a backup of the still-empty database first:

```bash
/srv/traktorist/deploy/backup.sh --local-only
```

**Local**, from the repository root. The spreadsheet and `aliases.yaml` are in `data/` (gitignored):

```bash
ssh deploy@traktorist.duckdns.org 'install -d -m 700 ~/import'
scp data/<file>.xlsx data/aliases.yaml deploy@traktorist.duckdns.org:import/
```

**Server:** the container runs as `deploy`'s own user id (`--user`), because the files are private to `deploy`:

```bash
chmod 600 ~/import/*
dc run --rm --user "$(id -u):$(id -g)" -v ~/import:/import:ro web \
    python manage.py import_sheet /import/<file>.xlsx --aliases /import/aliases.yaml --dry-run
```

Read the dry-run report. It should show the same fixed dates, tie notes and verification results you saw locally.

**Server**, the real import:

```bash
dc run --rm --user "$(id -u):$(id -g)" -v ~/import:/import:ro web \
    python manage.py import_sheet /import/<file>.xlsx --aliases /import/aliases.yaml
```

**Server**, then delete the files and check they are gone:

```bash
shred -u ~/import/*
rmdir ~/import
ls -la ~                    # no import directory
```

**Server**, back up the real data and prove the backup restores:

```bash
/srv/traktorist/deploy/backup.sh
/srv/traktorist/deploy/restore.sh "$(ls -t /var/backups/traktorist/traktorist-*.dump | head -1)"
```

Finally, open the site and compare a few standings with the spreadsheet.

## 9. Routine operations

| Task | Where | Command |
|---|---|---|
| Deploy `main` | GitHub | automatic on every push to `main` once the tests pass (section 12); by hand: `deploy/deploy.sh` |
| Deploy a tag or commit | local | `deploy/deploy.sh v1.2` or `deploy/deploy.sh <sha>` |
| Roll back | local | `deploy/deploy.sh <previous sha>` (migrations are not reversed: restore a pre-deploy dump if one ran) |
| Status | server | `dc ps` |
| Logs | server | `dc logs -f --tail 100 web` (or `caddy`, `db`) |
| Restart the app | server | `dc restart web` |
| Django shell | server | `dc exec web python manage.py shell` |
| Disk use | server | `df -h /` and `docker system df` |
| Memory | server | `free -m` and `docker stats --no-stream` |
| CI deploy log | server | `journalctl -t traktorist-ci-deploy -n 50` (the build output is in the GitHub job log) |
| Backup timer | server | `systemctl list-timers 'traktorist-*'` and `journalctl -u traktorist-backup -n 50` |

Other details:
- **Pre-deploy dumps.** A deploy with pending migrations saves a dump to `/var/backups/traktorist/pre-deploy/` before migrating. The last 5 are kept.
- **Image updates.** Each deploy pulls newer `python:3.13-slim`, `postgres:17` and `caddy:2-alpine` images. Unchanged images and code leave the running containers alone, so a repeated deploy restarts nothing.
- **Server edits.** Don't edit files in `/srv/traktorist` on the server (except `.env`): `deploy.sh` and CI deploys refuse to run while the checkout has local changes. `git -C /srv/traktorist status` shows them.
- **Changing `.env`.** Run `dc up -d` afterwards. Compose recreates the containers whose settings changed.
- **Rotating secrets.**
  - `SECRET_KEY`: edit `.env`, then `dc up -d` (everyone is logged out).
  - B2 key: create a new one, update `.env`, delete the old one in B2.
  - CI deploy key: section 12.7.
  - `POSTGRES_PASSWORD`: can't be changed through `.env` alone, since Postgres keeps the password it was initialized with. Change it with `dc exec db psql -U traktorist -d traktorist_club -c "ALTER USER traktorist PASSWORD '<new>'"`, then update `.env` and run `dc up -d`.

## 10. Enabling 2FA later (optional)

With `ADMIN_REQUIRE_2FA=True`, every admin login needs the password **and** a 6-digit code from an authenticator app (Aegis, Google Authenticator, 1Password, ...). While it is off (the default), devices can be set up at any time without changing how anyone logs in.

**Step 1: give every staff user a device.** Anyone without one cannot log in once 2FA is on. That includes you, and all superusers. **Server:**

```bash
dc exec web python manage.py totp_enroll <username>
```

`totp_enroll` prints a QR code in the terminal. Make the terminal window at least 70 columns wide and scan the code with the app. The command then asks for one code from the app, and only a correct code activates the device. A wrong or mis-scanned code changes nothing, and any previous device keeps working.

For an organizer, run it while they are with you (or on a screen share) and let them scan the code.

Check that nobody is missing:

```bash
dc exec web python manage.py shell -c "from django.contrib.auth.models import User; print(list(User.objects.filter(is_staff=True, is_active=True).exclude(totpdevice__confirmed=True).values_list('username', flat=True)))"
```

It must print `[]`.

**Step 2: turn it on.** **Server:**

```bash
cd /srv/traktorist
sed -i 's/^ADMIN_REQUIRE_2FA=.*/ADMIN_REQUIRE_2FA=True/' .env
grep ADMIN_REQUIRE_2FA .env         # ADMIN_REQUIRE_2FA=True (add the line if grep prints nothing)
dc up -d                            # recreates web with the new setting
```

Open the admin login in a private window. It now has a "Код из приложения" field. Log in with password and code. Sessions that were open before stay logged in until they expire.

**Afterwards:**
- **Lost phone:** run `totp_enroll` again for that user. The new device replaces the old one.
- **Wrong codes** count as failed logins: 5 in an hour lock the account and address as above.
- **Turning it off again:** set `ADMIN_REQUIRE_2FA=False` and run `dc up -d`. Devices stay in the database for next time.

## 11. Resource budget

Measured on the production image and compose file running locally with test data (`docker stats`, `docker image ls`, `du`). The "Budget" column adds room for growth.

**Memory (1 GB RAM, 2 GB swap):**

| Component | Measured | Budget |
|---|---|---|
| Ubuntu 24.04 (systemd, journald, sshd, cron, timers) | not measured | 150-200 MB |
| dockerd and containerd | not measured | 80-100 MB |
| `db`: Postgres, `shared_buffers=128MB`, up to 20 connections | 42 MB idle | 180 MB once the buffers fill |
| `web`: gunicorn master and 2 workers, `preload_app` | 98 MB after 60 requests | 200 MB (workers recycle every ~1000 requests) |
| `caddy` | 15 MB | 40 MB |
| **Total in steady state** | | **650-720 MB** |

That leaves about 300 MB for the page cache. Swap covers the peaks: an image build (uv, the Tailwind binary) or a Postgres restart during a deploy. Swap use above a few hundred MB during normal traffic, seen with `free -m`, means it's time to drop to one gunicorn worker or move to a 2 GB plan.

**Disk (20 GB):**

| Item | Size |
|---|---|
| Ubuntu 24.04 base and apt cache | ~3.5 GB |
| Swapfile | 2 GB |
| Images: `web` 304 MB, `postgres:17` 646 MB, `caddy:2-alpine` 89 MB | ~1.05 GB |
| Build cache (entries older than 7 days are pruned on each deploy) | ≤ 1.5 GB |
| Postgres volume: 48 MB with test data, WAL capped by `max_wal_size=256MB` | ≤ 400 MB |
| Container logs: 3 containers × 3 files × 10 MB | ≤ 90 MB |
| systemd journal (`SystemMaxUse=200M`) | ≤ 200 MB |
| Backups: 14 nightly, up to 5 pre-deploy, safety dumps. A dump is 60 KB with test data; a real one well under 1 MB | < 50 MB |
| Static files volume, Caddy certificates | < 20 MB |
| **Total** | **~8.8 GB** |

That leaves about 11 GB free. `deploy.sh` prints the free space after every deploy; investigate if it drops below 5 GB (usually with `docker system df`).

## 12. Automatic deploys from GitHub

Every push to `main` deploys itself once the tests pass. The `deploy` job in `.github/workflows/ci.yml` does it in these steps:
1. It waits for the `lint-and-test` and `docker-image` jobs.
2. It connects over SSH with a **CI key** of its own and sends the tested commit's SHA (`github.sha`), nothing else.
3. It runs the same smoke check as `deploy.sh`. A failed smoke check fails the job.

The CI key can't open a shell. In `authorized_keys` it carries a forced command, `deploy/ci-deploy.sh`, which does this:
1. It accepts exactly a 40-character lowercase SHA, and nothing else.
2. It refuses a commit that is not on `origin/main`.
3. It skips (with success) a commit older than the one deployed, so it never rolls back.
4. It runs `deploy/server-deploy.sh`.

`deploy.sh` and the CI deploy share a lock, so they never run at the same time: the second one waits for the first, up to 15 minutes. Manual deploys and rollbacks still go through `deploy/deploy.sh` with your own key.

The job runs only for pushes to `main` in this repository. Pull requests (Dependabot's and forks' included) never deploy, and never see the secrets: they live in the GitHub environment `production`, which only `main` may use.

Follow the steps in this order. The workflow commit goes in last, so its first run is not red for lack of a key.

### 12.1 Put the forced command on the server

**Local:** push the commits that add `deploy/smoke-check.sh` and `deploy/ci-deploy.sh` to `main`, but **not yet** the one that adds the `deploy` job to `ci.yml`. Deploy them by hand:

```bash
deploy/deploy.sh
ssh deploy@traktorist.duckdns.org 'ls -l /srv/traktorist/deploy/ci-deploy.sh'   # -rwxr-xr-x
```

### 12.2 Generate the CI key

**Local.** This key is separate from your personal key and has no passphrase. It is used only by GitHub. The comment names the key generation, so a rotation can remove exactly the old line.

```bash
ssh-keygen -t ed25519 -N '' -C "traktorist-ci-$(date +%Y-%m)" -f ~/.config/traktorist/ci_key
```

### 12.3 Install it with the forced command

**Local.** This appends one line to deploy's `authorized_keys`, over your own key. It uses `>>`, never `>`: a `>` would replace your personal key and lock you out.

```bash
{ printf 'command="/srv/traktorist/deploy/ci-deploy.sh",restrict,no-pty,no-port-forwarding,no-agent-forwarding,no-X11-forwarding '
  cat ~/.config/traktorist/ci_key.pub; } \
    | ssh deploy@traktorist.duckdns.org 'cat >> ~/.ssh/authorized_keys'
ssh deploy@traktorist.duckdns.org 'tail -n 1 ~/.ssh/authorized_keys'
```

The last line must start with `command="/srv/traktorist/deploy/ci-deploy.sh",restrict,` and end with `traktorist-ci-YYYY-MM`. `restrict` already turns off every forwarding, the PTY and `~/.ssh/rc`, including restrictions future OpenSSH versions add; the explicit options keep the line readable.

**Test it (local).** Always pass `-o IdentitiesOnly=yes` with this key. Without it, ssh may offer your personal key from the agent first, log in with that, and prove nothing.

```bash
ci=(ssh -i ~/.config/traktorist/ci_key -o IdentitiesOnly=yes)
host=deploy@traktorist.duckdns.org
git fetch origin

"${ci[@]}" "$host" 'bash -i'; echo "exit $?"      # refused: expected a 40-character commit SHA / exit 2
"${ci[@]}" "$host"; echo "exit $?"                # PTY allocation request failed ... refused ... / exit 2
"${ci[@]}" -W localhost:22 "$host"                # open failed: administratively prohibited
"${ci[@]}" "$host" origin/main; echo "exit $?"    # refused ... / exit 2
"${ci[@]}" "$host" "$(git rev-parse origin/main)" # a real deploy: "==> At ..." and the usual output
```

The last command redeploys the commit that is already running. That is harmless: it builds from cache and restarts nothing. **Server:** the requests are logged:

```bash
journalctl -t traktorist-ci-deploy -n 10
```

### 12.4 Pin the host key

GitHub must connect only to this server, with `StrictHostKeyChecking=yes`. The job gets the server's host key as a secret; here you fetch it and check it.

**Local:**

```bash
ssh-keyscan -t ed25519 traktorist.duckdns.org 2>/dev/null > ~/.config/traktorist/ci_known_hosts
ssh-keygen -lf ~/.config/traktorist/ci_known_hosts
ssh-keygen -lF traktorist.duckdns.org | grep ED25519       # what your own ssh already trusts
```

**Server** (over your existing session, which your own `known_hosts` already authenticates):

```bash
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

All three must print the same fingerprint: `SHA256:5ehJBO3EtbGLU/wQFOkGUxNGmJeqe8rlXElGC8vlPWQ`, the one accepted at the first login in section 2.1.

What to do if they differ:
- **The `ssh-keyscan` result differs from the server's own value:** stop. Something between you and the server answered in its place. Don't upload the file.
- **Only the expected value above differs from the other two:** the first login showed a different key type. Compare with `ssh-keygen -lf /etc/ssh/ssh_host_ecdsa_key.pub` on the server.

The file holds a single line: `traktorist.duckdns.org ssh-ed25519 AAAA...`.

### 12.5 The `production` environment and its secrets

On GitHub, open the repository's **Settings → Environments → New environment**, name it `production`, then:

| Setting | Value |
|---|---|
| Deployment branches and tags | **Selected branches and tags**, add the rule `main` |
| Required reviewers | off (on it pauses every deploy until you approve it, see 12.7) |
| Environment secrets | `DEPLOY_SSH_KEY` and `DEPLOY_KNOWN_HOSTS`, below |

Add the secrets to the **environment**, not as repository secrets. Only a job that names the environment can read them, and only from `main`. Either paste the files' contents into the web form (the whole `ci_key`, from `-----BEGIN` to `-----END ...-----`), or **local**, with the GitHub CLI:

```bash
gh secret set DEPLOY_SSH_KEY --env production < ~/.config/traktorist/ci_key
gh secret set DEPLOY_KNOWN_HOSTS --env production < ~/.config/traktorist/ci_known_hosts
gh secret list --env production                        # both names, no values
```

Then delete the private key. GitHub holds the only copy it needs, and a lost key is replaced by rotating (12.7), never recovered. Keep `ci_key.pub` and `ci_known_hosts`: neither is secret.

```bash
shred -u ~/.config/traktorist/ci_key
```

### 12.6 Turn it on

**Local:** push the commit that adds the `deploy` job to `ci.yml`. In the repository's **Actions** tab, that run shows:
1. `lint-and-test` and `docker-image`.
2. Then `deploy`, in the `production` environment. Its log shows:
   - `==> At <sha> <subject>` for the pushed commit
   - the build
   - `==> Smoke check passed: ...`

**Server:**

```bash
git -C /srv/traktorist rev-parse HEAD                  # the pushed commit
journalctl -t traktorist-ci-deploy -n 5
```

From now on:
- **Failures.** A push whose tests fail is not deployed. A failed deploy leaves the job red; **Re-run failed jobs** deploys the same commit again.
- **Pushes in a row.** Two pushes in a row deploy one after the other, and a running deploy is never cancelled. If a third push comes while one is waiting, the waiting one is dropped and the newest one runs.
- **Rollbacks.** `deploy/deploy.sh <previous sha>` stays the way to roll back. The CI key never goes backwards: re-running an old workflow is skipped as "already at a newer commit". The next push to `main` deploys again.
- **The job's time limit is 30 minutes.** ssh gives up on a dead connection after about a minute. So a hung deploy fails the job instead of holding the lock and the queue.

### 12.7 Revoke, pause or rotate the key

**Revoke** (the key leaked, or you stop using CI deploys). **Server:**

```bash
sed -i '/ traktorist-ci-[0-9-]*$/d' ~/.ssh/authorized_keys
grep -c traktorist-ci ~/.ssh/authorized_keys           # 0
journalctl -t traktorist-ci-deploy --since '30 days ago'    # every request, with its source address
```

Then delete the secret. **Local:** `gh secret delete DEPLOY_SSH_KEY --env production`. From then on, deploy jobs fail at the ssh step until a new key is installed (12.2 to 12.5). A leaked key could only have deployed commits that were already on `main`. Look in the journal for requests that match no run in the Actions tab.

**Pause** (deploy by hand for a while, keep the key): tick **Required reviewers** in the `production` environment and add yourself. Each deploy then waits for your approval in the Actions tab; the tests still run.

**Rotate** (yearly, or when in doubt):
1. Generate a new key and install it next to the old one (12.2, 12.3). Its comment has the new month.
2. Replace `DEPLOY_SSH_KEY` (12.5).
3. Re-run the last deploy job and check that it passes.
4. **Server:** remove the old line by its comment: `sed -i '/ traktorist-ci-2026-09$/d' ~/.ssh/authorized_keys` (with the old month).

**New server or new host key** (section 7.4): repeat 12.3 to 12.5 on the new server. The old `DEPLOY_KNOWN_HOSTS` then fails with "Host key verification failed", which is the pinning doing its job.
