# Deployment Guide — Django app on a fresh DigitalOcean VPS

This guide deploys a Django + PostgreSQL app to a fresh DigitalOcean (DO) VPS using
**[django-simple-deploy](https://django-simple-deploy.readthedocs.io)** with the
**[dsd-vps](https://pypi.org/project/dsd-vps/)** plugin, plus a few manual steps for
the things `dsd-vps` does **not** manage:

- **PostgreSQL 18** — installed manually (dsd-vps has no database support at all).
- **HTTPS via Let's Encrypt** — Caddy (which dsd-vps installs) does this automatically
  once you point a real domain at the droplet and update the Caddyfile.
- **Data** — either restore an existing `pg_dump` or generate fake data with the
  project's `seed_data` command.

Proven end-to-end against: Ubuntu 24.04, PostgreSQL 18 (PGDG repo), Caddy 2.11
(CloudSmith repo), Python 3.12 via `uv`, gunicorn under systemd socket activation,
Django 5.2, a 1.5 GB / 54M-row database dump, and Let's Encrypt.

> **Example values.** Commands use `<placeholder>` values; `# e.g.` comments show the
> values used when this guide was proven out (`<domain>` = `fuzzy-demo.example.com`,
> droplet IP = `203.0.113.10`).

---

## What dsd-vps does and does not do

Run as `python manage.py deploy`, the plugin (v0.1.1):

| It does | It does **not** do |
|---|---|
| Updates the server (`apt-get full-upgrade`, reboots if required) | Install PostgreSQL (or **any** database) |
| Creates a `django_user` system user with a restricted sudoers file | Obtain real TLS certs — its Caddyfile serves the droplet **IP** (Caddy then uses a self-signed internal cert; see step 7 for the real Let's Encrypt cert) |
| Installs `uv`, Python **3.12** (hardcoded), git, Caddy | Understand `uv`-only projects — it expects a `requirements.txt` (or Poetry/Pipenv) |
| Opens ufw ports 22/80/443 | Push your code for you in the config-only flow (you commit and push) |
| Sets up a bare git repo on the server with a `post-receive` hook that checks out `main` on push | Auto-redeploy on push — the 0.1.1 hook only checks out code; you re-run `serve_project.sh` after pushing |
| Writes `gunicorn.socket` / `gunicorn.service` systemd units, `serve_project.sh`, and `/etc/caddy/Caddyfile` (reverse proxy → gunicorn unix socket) | Run migrations before the first push (it runs them *during* the first `serve_project.sh`) |
| Appends a "VPS settings" block to `settings.py`, adds `gunicorn` to `requirements.txt`, generates `serve_project.sh` | |
| Generates a local `~/.ssh/id_rsa_git` keypair concept (in 0.1.1 the key generation is actually commented out — see known issues) | |

Because of the "does not" column, this guide adds: a manual Postgres 18 install
*before* the deploy, a `requirements.txt` exported from the `uv` lockfile,
compiler/PG-dev packages for source builds, `DEBUG=FALSE` tweaks, a one-time copy of
`requirements.txt`/`.env` to the server, and a Caddyfile domain change that switches
Caddy into automatic Let's Encrypt mode.

---

## Prerequisites

1. A fresh DO droplet, **Ubuntu 24.04 LTS**, **4 GB RAM / 2 vCPU minimum** if you plan
   to restore a large dump (the proof-of-concept droplet was 4 vCPU / 15 GB; a 1.5 GB
   dump with trigram/phonetic indexes restores in a few minutes on that hardware).
   Choose **password** authentication for root for now — dsd-vps 0.1.1 connects as
   root *by password* for the existing-VPS flow (SSH-key support is open at
   [dsd-vps#18](https://github.com/django-simple-deploy/dsd-vps/issues/18)).
2. A **domain** (or subdomain) for the site. Let's Encrypt cannot issue for a bare IP.
   In your DNS provider, create an **A record** for `<domain>` pointing at the
   droplet's public IP, and verify before continuing:

   ```sh
   $ dig +short <domain>
   203.0.113.10        # e.g. your droplet IP
   ```

3. On your workstation: `git`, [`uv`](https://docs.astral.sh/uv/), and your project
   cloned.
4. (Recommended) Add your SSH key to the droplet in the DO control panel so you can
   also reach the server as root/key — dsd-vps itself uses the password, but key
   access makes every manual step below easy.

---

## Step 0 — Base update (do this first, on the droplet)

```sh
$ ssh root@203.0.113.10                  # e.g.
$ sudo apt-get update
$ sudo DEBIAN_FRONTEND=noninteractive apt-get upgrade -y
$ sudo reboot
```

Reboot after the upgrade — a fresh image's first upgrade usually leaves a pending
reboot (new kernel). Wait for the droplet to come back before continuing.
(dsd-vps runs its own `full-upgrade` later and handles a needed reboot itself, but
doing it up front is faster and out of the way.)

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

Also install the packages the Python deps need in case `uv` builds from source
(`psycopg2` for the marimo slides, and possibly `psycopg` C extensions — both need
`pg_config.h`):

```sh
$ sudo apt-get install -y build-essential libpq-dev postgresql-server-dev-18
```

**Why install the database before running dsd-vps?** The first `serve_project.sh`
run executes `manage.py migrate` immediately; the database has to exist or the first
serve fails. (If you're restoring a dump, do it *next*, before the first serve.)

## Step 2 — Database role and data

dsd-vps creates a system user named **`django_user`** and runs the app as that user.
The trick that keeps things simple: give Postgres a role of the **same name**. The
app then connects over the local unix socket with **peer authentication — no
password in any env var or service file**: OS user `django_user` maps directly to
Postgres role `django_user`.

Create the role and database **before** the first serve:

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
  extensions the migrations need. Both are **trusted** in Postgres 18, so a
  non-superuser with CREATE on the database can create them — no superuser app role
  required.

### Option A — Restore an existing dump (same data as your dev machine)

If you have a `pg_dump` custom-format dump (`.tar`), copy it to the droplet and
restore it. Two gotchas learned the hard way:

1. Put the file where the **`postgres`** user can read it — `/root` is mode `0700`.
   `/var/lib/postgresql` is a fine spot.
2. Pass `--role=django_user --no-owner` so every restored object ends up **owned by
   `django_user`** — no follow-up GRANTs needed.

```sh
# Workstation: verify the dump, then copy it
$ sha256sum fuzzy_demo.tar
7aca4187…c857cc812df31bcc   # e.g.
$ rsync -e ssh fuzzy_demo.tar root@203.0.113.10:/var/lib/postgresql/fuzzy_demo.tar

# Droplet: verify the copy, then restore in the background
$ sha256sum /var/lib/postgresql/fuzzy_demo.tar    # must match
$ sudo chmod 644 /var/lib/postgresql/fuzzy_demo.tar
$ sudo -u postgres nohup pg_restore --role=django_user --no-owner \
      -d fuzzy_demo /var/lib/postgresql/fuzzy_demo.tar > /tmp/pg_restore.log 2>&1 &
$ tail -f /tmp/pg_restore.log       # watch until it exits; expect 0 errors
```

A 1.5 GB dump of 54M rows (including trigram + phonetic functional indexes) took
about 30 minutes on a 4-vCPU droplet, most of it index creation. The restore
includes the `fuzzystrmatch`/`pg_trgm` extensions and the `django_migrations` table,
so `manage.py migrate` on the first serve is a no-op ("No migrations to apply").

### Option B — Generate fake data (fresh servers, no dump)

The project ships a `seed_data` management command that generates realistic court
records (Census/SSA-weighted names, clustered spellings, vectorized via Polars).
Do this **after** step 6 (you need the app venv on the server):

```sh
$ ssh root@203.0.113.10
$ sudo -u django_user bash -lc 'cd /home/django_user/fuzzy_demo \
    && /home/django_user/.local/bin/uv run python manage.py seed_data --count 1000000 --flush'
```

`--count` is the target row count (after cluster expansion); **1,000,000** is a good
default that resembles the real dataset's shape. Add `--seed 42` for reproducibility.
On a small droplet this takes a while — run it under `nohup` if needed.

## Step 3 — Local project prep

`dsd-vps` expects a `requirements.txt` (it doesn't understand `uv` lockfiles).
Generate one from your lockfile. Keep it **gitignored** — it's a build artifact of
your dependency set (you'll copy it to the server in step 6):

```sh
$ uv export --no-hashes --no-dev -o requirements.txt
```

Add `requirements.txt` to `.gitignore` if it isn't already. Regenerate it whenever
dependencies change.

Two more local requirements:

- **Install the plugin**: `uv add dsd-vps` (adds it to `pyproject.toml`;
  `django-simple_deploy` must already be in `INSTALLED_APPS`).
  ⚠️ Only **one** `django-simple-deploy` plugin may be installed in the venv — if you
  have another `dsd-*` package around, uninstall it or `manage.py deploy` refuses to
  run ("multiple plugins installed").
- **Python floor**: dsd-vps installs **Python 3.12** on the server (hardcoded in
  `install_python()`). If your `pyproject.toml` pins `requires-python` above 3.12,
  the server's `uv venv` will fail. Widen the floor, e.g.
  `requires-python = ">=3.12"`, and re-lock (`uv lock`).
- **DEBUG-gate dev-only apps**: if `INSTALLED_APPS`/`MIDDLEWARE` include
  `debug_toolbar` (a dev dependency) unconditionally, gate them on `DEBUG` —
  otherwise the server venv (built from the non-dev export) can't import them and
  the first `serve_project.sh` crashes.
- **Static files**: on the server, `DEBUG=False` means Django won't serve
  `/static/`. The VPS settings block dsd-vps appends ships whitenoise middleware code
  **commented out** — uncomment those two lines (whitenoise is already a dependency)
  and run `collectstatic` (which `serve_project.sh` does).

Commit all of the above before deploying — dsd-vps refuses to run on a dirty tree.

## Step 4 — Run the deploy

```sh
$ export DSD_HOST_IPADDR=203.0.113.10
$ export DSD_HOST_PW='<root password>'
$ env -u SSH_AUTH_SOCK python manage.py deploy
```

- `env -u SSH_AUTH_SOCK`: dsd-vps uses paramiko for the password connection; a
  running local ssh-agent confuses it (symptom: `SSHException: No existing
  session`). Unsetting the agent socket for this one command fixes it.
- **dsd-vps 0.1.1 bug (password flow):** the plugin's `PluginConfig` never
  initializes `path_ssh_key`, so the password flow crashes with
  `AttributeError: 'PluginConfig' object has no attribute 'path_ssh_key'` before it
  does anything. Two workarounds, both proven:
  1. **Recommended:** one-line local patch to
     `.venv/lib/python3.14/site-packages/dsd_vps/plugin_config.py` — add
     `self.path_ssh_key = None` to `PluginConfig.__init__`. (Reapply after any
     `uv sync`/upgrade; upstream fix pending.)
  2. Pre-create the `django_user` system user manually (see below) and export
     `DO_DJANGO_USER=django_user` so the plugin talks to that user from the start.

  If you pre-create the user, mirror what the plugin's `add_server_user()` does:

  ```sh
  $ useradd -m django_user
  $ echo "django_user:<same password as DSD_HOST_PW>" | chpasswd
  $ usermod -aG sudo django_user
  $ echo "django_user ALL=(ALL) NOPASSWD:SETENV: /usr/bin/apt-get, NOPASSWD: /usr/bin/apt-get, /usr/bin/mv, /usr/bin/systemctl daemon-reload, /usr/bin/systemctl reboot, /usr/bin/systemctl start gunicorn.socket, /usr/bin/systemctl enable gunicorn.socket, /usr/bin/systemctl restart gunicorn.socket, /usr/bin/systemctl start caddy, /usr/bin/systemctl enable caddy, /usr/bin/systemctl restart caddy, /usr/sbin/ufw, /usr/bin/gpg, /usr/bin/tee" \
      | sudo tee /etc/sudoers.d/django_user
  $ # and add your git-push public key to /home/django_user/.ssh/authorized_keys
  ```

What the command does on success:

1. Connects to the droplet (root with the `DSD_HOST_PW` password, or
   `DO_DJANGO_USER` if set) and runs `apt-get update && full-upgrade`, rebooting if
   required.
2. Ensures the `django_user` system user with the restricted sudoers file above.
3. Installs **uv**, **Python 3.12**, **git**, and **Caddy** (CloudSmith repo).
4. Opens ufw: 22, 80, 443.
5. Creates a bare git repo at `/home/django_user/fuzzy_demo.git` with a
   `post-receive` hook that checks out the `main` branch on push.
   ⚠️ **dsd-vps 0.1.1 bugs in this step:**
   - It adds the local remote as `django_user@None:/...` (it reads
     `plugin_config.ip_address`, which is only set in the `--automate-all` flow).
     Fix it: `git remote set-url do_server django_user@203.0.113.10:/home/django_user/fuzzy_demo.git`
   - Its `ssh-copy-id ... git@<ip>` call is aimed at a nonexistent `git` user;
     authorize your own key for `django_user` instead.
   - The `~/.ssh/id_rsa_git` keypair it talks about is never actually generated
     (that code is commented out in 0.1.1).
6. In your local repo: appends the **VPS settings block** to `settings.py`,
   ensures `gunicorn` is in `requirements.txt`, and adds `serve_project.sh`.
7. Writes `/etc/caddy/Caddyfile` (reverse proxy: droplet IP → gunicorn unix socket)
   and the `gunicorn.socket` / `gunicorn.service` systemd units.

It stops there — config-only mode does **not** commit or push.

## Step 5 — Commit, push, first serve

```sh
$ git add . && git commit -am "Configured project for deployment."
$ git push do_server main
```

⚠️ The hook hardcodes `main`. If you're deploying from a feature branch (as this
guide's proof-of-concept did), either merge to `main` or, for testing only, edit the
server hook to track your branch:
`sed -i 's/main/<branch>/g' /home/django_user/fuzzy_demo.git/hooks/post-receive`
(revert before merging for real).

The push checks out code to `/home/django_user/fuzzy_demo`. Now give the app the two
files git can't carry (both are gitignored):

```sh
# From your workstation:
$ rsync -e ssh requirements.txt django_user@203.0.113.10:/home/django_user/fuzzy_demo/requirements.txt

# .env — Django reads BASE_DIR/.env via django-environ; it survives git pushes
# because git doesn't touch untracked files:
$ ssh root@203.0.113.10
# create /home/django_user/fuzzy_demo/.env:
DEBUG=False
DJANGO_SECRET_KEY=<generate: python -c "import secrets; print(secrets.token_urlsafe(50))">
DJANGO_ALLOWED_HOSTS=<domain>
DATABASE_URL=psql://django_user@/fuzzy_demo
$ chown django_user:django_user /home/django_user/fuzzy_demo/.env
```

- `psql://django_user@/fuzzy_demo` — **empty host** means "local unix socket", which
  is what enables passwordless peer auth.
- Real env vars override `.env`, so keep `DEBUG` consistent between `.env` and the
  gunicorn unit (below).

Then turn `DEBUG` off in the gunicorn unit (the generated template hardcodes
`DEBUG=TRUE`):

```sh
$ sudo sed -i 's/Environment="DEBUG=TRUE"/Environment="DEBUG=FALSE"/' \
    /etc/systemd/system/gunicorn.service
```

And run the first serve (creates the venv, installs deps, migrates, collectstatic,
starts `gunicorn.socket` + `caddy`):

```sh
$ ssh django_user@203.0.113.10
$ bash /home/django_user/fuzzy_demo/serve_project.sh
```

Two known hiccups on the first run:

- If `requirements.txt` still says `export DEBUG=TRUE` (the plugin template), change
  it to `FALSE` locally, commit, and push first — otherwise the script's env forces
  `DEBUG=True` in the one-off `manage.py` calls it makes.
- `uv venv` warns "A virtual environment already exists" if you re-run the script —
  harmless.

At this point the app is live at `http://203.0.113.10/`. Expect **308 → HTTPS with a
self-signed "Caddy Local Authority" cert**: recent Caddy versions serve IP sites over
TLS with an internal self-signed cert and redirect HTTP→HTTPS. That's expected
interim behavior, not an error — the real cert comes in step 7.

## Step 6 — (Fresh servers only) Seed fake data

If you skipped step 2's dump, seed now — see
[step 2, Option B](#option-b--generate-fake-data-fresh-servers-no-dump).

## Step 7 — HTTPS with Let's Encrypt

Caddy's default behavior for a site addressed by a **domain name** is **automatic
HTTPS with Let's Encrypt**: it obtains the cert, installs it, and renews it
automatically (Caddy registers its own renewal — no certbot involved). The only
change needed is swapping the droplet IP for the domain in the Caddyfile:

```sh
$ ssh root@203.0.113.10
$ sudo cp /etc/caddy/Caddyfile /etc/caddy/Caddyfile.bak-ip
$ sudo sed -i 's/^203\.0\.113\.10 {/<domain> {/' /etc/caddy/Caddyfile   # e.g. fuzzy-demo.example.com {
$ sudo systemctl restart caddy
$ journalctl -u caddy --since "1 min ago" --no-pager | grep -iE "obtained|error"
#   ... "certificate obtained successfully","identifier":"<domain>","issuer":"acme-v02.api.letsencrypt.org-directory"
```

You should see the certificate in the journal within ~30 seconds (the A record from
Prerequisites must already resolve; if DNS isn't propagated yet, Caddy retries
automatically — no restart needed).

Verify:

```sh
$ curl -s -o /dev/null -w "%{http_code} ssl_verify=%{ssl_verify_result}\n" https://<domain>/
200 ssl_verify=0
$ curl -skv https://<domain>/ -o /dev/null 2>&1 | grep -E "subject:|issuer:"
*   subject: CN=<domain>
*   issuer: C=US; O=Let's Encrypt; CN=...
$ curl -s -o /dev/null -w "%{http_code} -> %{redirect_url}\n" http://<domain>/
308 -> https://<domain>/
$ curl -s -o /dev/null -w "static: %{http_code}\n" https://<domain>/static/admin/css/base.css
static: 200
```

## Updating the app

```sh
# Workstation: (if dependencies changed, first)
$ uv export --no-hashes --no-dev -o requirements.txt
$ rsync -e ssh requirements.txt django_user@203.0.113.10:/home/django_user/fuzzy_demo/

$ git push do_server main        # hook checks out the new code
$ ssh django_user@203.0.113.10 'bash /home/django_user/fuzzy_demo/serve_project.sh'
```

`serve_project.sh` is idempotent: reinstall deps → `migrate` → `collectstatic` →
restart `gunicorn.socket` and `caddy`. Re-running it is the fix for nearly any
"stale code / broken deploy" situation.

## Day-2 operations (quick reference)

```sh
sudo systemctl status gunicorn.socket caddy
sudo journalctl -u gunicorn.service -n 50 --no-pager
sudo -u postgres psql -d fuzzy_demo -c 'SELECT count(*) FROM records_courtrecord;'
```

- Let's Encrypt renewal is automatic (Caddy checks every few days and renews at
  ~2 weeks before expiry).
- The generated Caddyfile contains a `{ debug }` global option — noisy logs; remove
  it when you stop troubleshooting.
- The restored dump sits at `/var/lib/postgresql/fuzzy_demo.tar` (in the proof run) —
  keep it as your restore point, and set up your own periodic `pg_dump` backups: a
  VPS is **not** a database.

## Known issues & limitations (as of dsd-vps 0.1.1)

- **Root password required** for the existing-VPS flow; SSH-key support is open at
  [dsd-vps#18](https://github.com/django-simple-deploy/dsd-vps/issues/18).
- **`path_ssh_key` AttributeError** crashes the password flow — one-line local patch
  documented in step 4; upstream fix pending.
- **`do_server` remote gets `@None`** in the config-only flow — fix with
  `git remote set-url` (step 4).
- **`post-receive` hook hardcodes `main`** and **only checks out** (no auto serve) —
  re-run `serve_project.sh` after each push.
- **Python 3.12 is hardcoded** (`install_python()`); projects needing newer Python
  must widen their floor (step 3) or `uv python install <ver>` manually.
- **`DEBUG=TRUE` hardcoded** in the generated `gunicorn.service` and
  `serve_project.sh` — fix per steps 3/5.
- **No database management** — Postgres install/restore/backup is on you (this guide).
- **`uv`-only projects** need a generated `requirements.txt` (steps 3/5).
- **`--automate-all`** can also *create* the droplet via `doctl`
  (`python manage.py deploy --platform digital_ocean --automate-all`); it currently
  creates a 1 GB Ubuntu 25.04 instance — too small for a serious dataset — and the
  plugin's own docs call it experimental. See the
  [dsd-vps README](https://pypi.org/project/dsd-vps/#description) for details.
