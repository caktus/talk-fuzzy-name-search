# Deployment Guide — Django app on a fresh DigitalOcean VPS

This guide deploys a Django + PostgreSQL app to a fresh DigitalOcean (DO) VPS using
**[django-simple-deploy](https://django-simple-deploy.readthedocs.io)** with the
**[dsd-vps](https://pypi.org/project/dsd-vps/)** plugin, plus manual steps for the two
things `dsd-vps` does **not** manage:

- **PostgreSQL 18** — installed manually (see [Why?](#what-dsd-vps-does-and-does-not-do)).
- **HTTPS via Let's Encrypt** — handled by Caddy (which `dsd-vps` installs) once you
  point your domain at the droplet and update the Caddyfile (see step 7).

Proven end-to-end against: Ubuntu 24.04, PostgreSQL 18, Caddy (CloudSmith repo),
Python 3.12 via `uv`, gunicorn under systemd socket activation, Django 5.2.

> **Example values.** Commands use `<placeholder>` values; the `# e.g.` comments show
> the values used when this guide was proven out
> (`<domain>` = `fuzzy-demo.example.com`, droplet IP = `203.0.113.10`).

---

## What dsd-vps does and does not do

Run by `python manage.py deploy`, the plugin (v0.1.1):

| It does | It does **not** do |
|---|---|
| Updates the server (`apt-get full-upgrade` + reboot if required) | Install PostgreSQL (or **any** database) |
| Creates a `django_user` system user with a restricted sudoers file | Obtain TLS certificates (its Caddyfile serves plain HTTP on the droplet IP) |
| Installs `uv`, Python **3.12** (hardcoded), git, Caddy | Know about `uv`-only projects — it expects a `requirements.txt` (or Poetry/Pipenv) |
| Opens firewall ports 22/80/443 (ufw) | Run migrations for you before the first push (it does run them *on each push*) |
| Sets up a bare git repo on the server with a `post-receive` hook that redeploys on push | Use SSH keys for the existing-VPS flow — it connects as root with a **password** ([dsd-vps#18](https://github.com/django-simple-deploy/dsd-vps/issues/18)) |
| Writes `gunicorn.socket` / `gunicorn.service` systemd units, `serve_project.sh`, and `/etc/caddy/Caddyfile` (reverse proxy IP → gunicorn unix socket) | |
| Appends a "VPS settings" block to your `settings.py`, adds `gunicorn` to `requirements.txt`, generates `serve_project.sh` and `Caddyfile` in your repo | |
| Commits those changes, creates a local `~/.ssh/id_rsa_git` keypair (and edits `~/.ssh/config`), and pushes your project to the server | |

Because of the "does not" column, this guide adds: a manual Postgres 18 install before
the deploy, a `requirements.txt` generated from the `uv` lockfile, a small
`DEBUG=FALSE` tweak after the deploy, and a Caddyfile domain change that switches
Caddy into automatic Let's Encrypt mode.

---

## Prerequisites

1. A fresh DO droplet, **Ubuntu 24.04 LTS**, at least **4 GB RAM / 2 vCPU** if you plan
   to restore a large dump (this guide's proof-of-concept droplet was 4 vCPU / 15 GB).
   In the DO control panel, choose **password** authentication for root for now —
   dsd-vps needs the root password (see the known-issues note about
   [dsd-vps#18](https://github.com/django-simple-deploy/dsd-vps/issues/18)).
2. A **domain** (or subdomain) for the site. Let's Encrypt requires a real hostname —
   you cannot get a public cert for a bare IP. In your DNS provider, create an
   **A record** for `<domain>` pointing at the droplet's public IP. Verify before
   continuing:

   ```sh
   $ dig +short <domain>
   203.0.113.10        # e.g. your droplet IP
   ```

   DNS can take a few minutes to an hour to propagate.
3. On your workstation: `git`, [`uv`](https://docs.astral.sh/uv/), and your project
   cloned.
4. (Recommended) Add an SSH key for the droplet in the DO panel so you can also reach
   the server as `root` by key — dsd-vps itself uses the password, but key access makes
   every manual step below possible.

---

## Step 0 — Base update (do this first, on the droplet)

```sh
$ ssh root@203.0.113.10                 # e.g.
$ sudo apt-get update
$ sudo DEBIAN_FRONTEND=noninteractive apt-get upgrade -y
$ sudo reboot
```

Reboot after the upgrade — a fresh image's first upgrade usually leaves a pending
reboot (new kernel). Wait until the droplet is back before continuing. (dsd-vps will
run its own `full-upgrade` later and handles a needed reboot itself, but doing it up
front is faster and gets it out of the way.)

## Step 1 — Install PostgreSQL 18

Ubuntu 24.04's default repository only ships Postgres 16; use the official
[PGDG](https://www.postgresql.org/download/linux/ubuntu/) repository for 18:

```sh
$ sudo apt-get install -y wget ca-certificates gnupg lsb-release
$ wget -q -O - https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    | sudo gpg --dearmor -o /usr/share/keyrings/pgdg.gpg
$ echo "deb [signed-by=/usr/share/keyrings/pgdg.gpg] http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" \
    | sudo tee /etc/apt/sources.list.d/pgdg.list
$ sudo apt-get update
$ sudo apt-get install -y postgresql-18
```

The cluster starts automatically (verify with `pg_lsclusters` — status `online`,
port 5432).

**Why install it before running dsd-vps?** The first git push triggers
`serve_project.sh`, which runs `manage.py migrate` immediately; the database has to
exist or that first deploy fails. (Ordering note: do **not** run the `migrate` that
creates the empty schema yet — if you're restoring a dump, restore it *first*, in
step 2.)

## Step 2 — Database role and data

`dsd-vps` creates a system user named **`django_user`** and runs the app as that user.
The trick that keeps things simple: give Postgres a role of the **same name**. The app
then connects over the local unix socket with **peer authentication — no password in
any env var or service file**: the OS user `django_user` maps directly to the Postgres
role `django_user`.

Create the role and database **before** the first push:

```sh
$ sudo -u postgres psql \
    -c "CREATE ROLE django_user LOGIN;" \
    -c "CREATE DATABASE fuzzy_demo OWNER django_user;" \
    -c "GRANT CREATE ON DATABASE fuzzy_demo TO django_user;"
```

- `OWNER django_user` matters on Postgres 15+: the `public` schema is no longer
  world-writable, so the role needs to own the database (or at least the schema) to
  create the app's tables.
- `GRANT CREATE ...` lets `django_user` create the `fuzzystrmatch` and `pg_trgm`
  extensions the migrations need. Both extensions are **trusted** in Postgres 18, so a
  non-superuser with CREATE on the database can create them — no superuser app role
  required.

### Option A — Restore an existing dump (same data as your dev machine)

If you have a `pg_dump` custom-format dump (`.tar`) of the database, copy it to the
droplet and restore it. Two gotchas learned the hard way:

1. Put the file somewhere the **`postgres`** user can read — `/root` is `0700`.
   `/var/lib/postgresql` is a fine spot.
2. Pass `--role=django_user --no-owner` so every restored object ends up **owned by
   `django_user`** — no follow-up GRANTs needed.

```sh
# Workstation: verify the dump, then copy it
$ sha256sum fuzzy_demo.tar
7aca4187… <file>
$ rsync -e ssh fuzzy_demo.tar root@203.0.113.10:/var/lib/postgresql/fuzzy_demo.tar

# Droplet: verify the copy, then restore
$ sha256sum /var/lib/postgresql/fuzzy_demo.tar      # must match
$ sudo mv /var/lib/postgresql/fuzzy_demo.tar /var/lib/postgresql/  # (already there)
$ sudo chmod 644 /var/lib/postgresql/fuzzy_demo.tar
$ sudo -u postgres nohup pg_restore --role=django_user --no-owner \
      -d fuzzy_demo /var/lib/postgresql/fuzzy_demo.tar > /tmp/pg_restore.log 2>&1 &
$ tail -f /tmp/pg_restore.log        # watch until it exits; 0 errors expected
```

A 1.5 GB dump of ~tens of millions of rows (including trigram + phonetic functional
indexes) restores in a few minutes on a 4-vCPU droplet. The restore includes the
`fuzzystrmatch`/`pg_trgm` extensions and the `django_migrations` table, so when
`serve_project.sh` runs `manage.py migrate` on the first push, it's a no-op.

### Option B — Generate fake data (fresh servers, no dump)

The project ships a `seed_data` management command that generates realistic court
records (Census/SSA-weighted names, clustered spellings, vectorized via Polars).
Do this **after** step 6 (you need the app venv on the server):

```sh
$ ssh root@203.0.113.10
$ sudo -u django_user bash -lc 'cd /home/django_user/talk-fuzzy-name-search \
    && /home/django_user/.local/bin/uv run python manage.py seed_data --count 1000000 --flush'
```

`--count` is the target row count (cluster expansion applied); 1,000,000 rows is a
good default that resembles the real dataset's shape. Add `--seed 42` for
reproducibility. On a small droplet this takes a while — run it under `nohup` if
needed.

## Step 3 — Local project prep

`dsd-vps` expects a `requirements.txt` (it doesn't understand `uv` lockfiles). Generate
one from your lockfile. Keep it **gitignored** — it's a build artifact of your
dependency set:

```sh
$ uv export --no-hashes --no-dev -o requirements.txt
```

Add `requirements.txt` to `.gitignore` if it isn't already. Regenerate it whenever
dependencies change.

Two more local requirements:

- **Install the plugin**: `uv add dsd-vps` (adds `dsd-vps` to `pyproject.toml`;
  `django-simple-deploy` must be in `INSTALLED_APPS`, which it is).
- **Python floor**: dsd-vps installs **Python 3.12** on the server (hardcoded in
  `install_python()`). If your `pyproject.toml` pins `requires-python` above 3.12, the
  server's `uv venv` will fail. Widen the floor, e.g. `requires-python = ">=3.12"`,
  and re-lock (`uv lock`).

## Step 4 — Run the deploy

```sh
$ export DSD_HOST_IPADDR=203.0.113.10
$ export DSD_HOST_PW='<root password>'
$ python manage.py deploy
```

What happens, in order:

1. Connects to the droplet as **root with the password** in `DSD_HOST_PW`,
   accepting the host key.
2. `apt-get update && full-upgrade` (skips if nothing to do); reboots the droplet if
   a reboot is pending, then re-checks.
3. Creates system user **`django_user`** with a sudoers file that whitelists exactly:
   `apt-get`, `mv`, `systemctl` (daemon-reload/reboot/start/restart for
   `gunicorn.socket` and `caddy`), `ufw`, `gpg`, `tee`.
4. Installs **uv**, **Python 3.12** (`uv python install 3.12`), **git**, and
   **Caddy** (CloudSmith apt repo).
5. Opens ufw: 22, 80, 443.
6. On the server: creates a bare git repo under `/home/django_user/` with a
   `post-receive` hook that checks out the code and runs `serve_project.sh`; sets up
   `git@<ip>` push access using a fresh local keypair the plugin generates at
   `~/.ssh/id_rsa_git` (it also appends an entry to your local `~/.ssh/config`).
7. In your local repo: appends the **VPS settings block** to `settings.py` (sets
   `STATIC_ROOT`, honors the `DEBUG` env var, widens `ALLOWED_HOSTS`), adds `gunicorn`
   to `requirements.txt`, and adds `serve_project.sh` and `Caddyfile`.
8. Writes `/etc/caddy/Caddyfile` (reverse proxy: droplet IP →
   `unix:///run/gunicorn.sock`) and the `gunicorn.socket`/`gunicorn.service` systemd
   units.
9. **Commits** all local changes and **pushes** to the server. The `post-receive` hook
   runs `serve_project.sh`: creates the venv, `uv pip install -r requirements.txt`,
   `manage.py migrate`, `manage.py collectstatic`, starts/restarts `gunicorn.socket`
   and `caddy`.

The deploy takes a few minutes; the plugin prints a success message and opens the
site. At this point the app is live at `http://203.0.113.10/` (plain HTTP, browser
will warn) — and it will be running against **no database yet** unless you did step 2,
and with **`DEBUG=TRUE`** (hardcoded in the generated `gunicorn.service`).

> ⚠️ The plugin runs `git add .` before committing — make sure no large or secret
> files are untracked in the repo (gitignore your dumps!).

## Step 5 — Point the app at Postgres and turn DEBUG off

The generated `gunicorn.service` sets `Environment="DEBUG=TRUE"`, and nothing sets
`DATABASE_URL`. Two small post-deploy tweaks close both gaps.

**1. Create a `.env` in the server checkout.** `settings.py` already reads
`BASE_DIR/".env"` via `django-environ`, and `.env` is gitignored, so it survives git
pushes:

```sh
$ ssh root@203.0.113.10
$ sudo bash -c 'cat > /home/django_user/talk-fuzzy-name-search/.env' <<'EOF'
DEBUG=False
DJANGO_SECRET_KEY=<generate: python -c "import secrets; print(secrets.token_urlsafe(50))">
DJANGO_ALLOWED_HOSTS=<domain>
DATABASE_URL=psql://django_user@/fuzzy_demo
EOF
$ sudo chown django_user:django_user /home/django_user/talk-fuzzy-name-search/.env
```

- `psql://django_user@/fuzzy_demo` — empty host means "local unix socket", which is
  what enables passwordless peer auth.
- `DJANGO_ALLOWED_HOSTS` should list your real domain (the VPS settings block the
  plugin appends also allows `*`, but be explicit).

**2. Fix `DEBUG` in the gunicorn unit and reload:**

```sh
$ sudo sed -i 's/Environment="DEBUG=TRUE"/Environment="DEBUG=FALSE"/' \
    /etc/systemd/system/gunicorn.service
$ sudo systemctl daemon-reload
$ sudo systemctl restart gunicorn.socket
```

## Step 6 — (Fresh servers only) Seed fake data

If you skipped step 2's dump, seed now — see
[step 2, Option B](#option-b--generate-fake-data-fresh-servers-no-dump).

## Step 7 — HTTPS with Let's Encrypt

Caddy's default behavior for a site addressed by a **domain name** is **automatic
HTTPS with Let's Encrypt**: it obtains the cert, installs it, and renews it (Caddy
registers a systemd timer for renewal — no certbot involved). The only change needed
is swapping the droplet IP for the domain in the Caddyfile:

```sh
$ ssh root@203.0.113.10
$ sudo cp /etc/caddy/Caddyfile /etc/caddy/Caddyfile.bak-ip
$ sudo sed -i 's/^203\.0\.113\.10 {/<domain> {/' /etc/caddy/Caddyfile   # e.g. fuzzy-demo.example.com {
$ cat /etc/caddy/Caddyfile
$ sudo systemctl restart caddy
$ journalctl -u caddy -n 20 --no-pager
```

Watch the journal for ` Obtained certificate` — you should have a green padlock within
~30 seconds (requires the A record from Prerequisites to already resolve). If DNS
isn't propagated yet, Caddy retries automatically; you do not need to restart it
again.

Verify:

```sh
$ curl -sI https://<domain>/ | head -3          # HTTP/2 200
$ curl -sI http://<domain>/  | head -3          # 308 → https (Caddy's automatic redirect)
```

## Updating the app

```sh
$ git push origin <branch>
```

The server's `post-receive` hook re-checks out the code and reruns `serve_project.sh`
(install deps → `migrate` → `collectstatic` → restart services). No further steps
needed. If you changed dependencies, regenerate `requirements.txt` first and commit it
is **not** required — it's gitignored, but `serve_project.sh` installs from it, so
either commit it or regenerate it on the server before pushing.

<!-- TODO: clarify the requirements.txt-on-server flow once proven (it lives in the
checkout and is gitignored → it only exists if you copy it. Verify behavior in the
proof-of-concept and rewrite this section). -->

## Day-2 operations (quick reference)

```sh
sudo systemctl status gunicorn.socket caddy
sudo journalctl -u gunicorn.service -n 50 --no-pager
sudo tail /var/log/caddy/*.log           # (Caddyfile has a `debug` block; consider removing)
sudo -u postgres psql -d fuzzy_demo -c 'SELECT count(*) FROM records_courtrecord;'
```

- Let's Encrypt renewal is automatic (Caddy's `systemd` timer, ~every 3 days it checks;
  certs renew at ~2 weeks remaining).
- `serve_project.sh` is safe to re-run by hand after any hiccup.

## Known issues & limitations (as of dsd-vps 0.1.1)

- **Root password required** for the existing-VPS flow — SSH-key support is tracked
  at [dsd-vps#18](https://github.com/django-simple-deploy/dsd-vps/issues/18).
- **Python 3.12 is hardcoded** (`install_python()`); projects needing newer Python must
  either lower their floor or `uv python install <ver>` manually and point the venv at
  it.
- **`DEBUG=TRUE` is hardcoded** in the generated `gunicorn.service` — fix per step 5.
- **No database management** — Postgres install/restore/backup is on you (this guide).
- **No backups** — a VPS is not a database. Take regular `pg_dump` snapshots or use
  DO volumes/snapshots.
- **`--automate-all`** can also *create* the droplet via `doctl`
  (`python manage.py deploy --platform digital_ocean --automate-all`); it currently
  creates a 1 GB Ubuntu 25.04 instance — too small for a serious dataset — and is
  best read as "experimental" by the plugin's own docs. See the
  [dsd-vps README](https://pypi.org/project/dsd-vps/#description) for details.
