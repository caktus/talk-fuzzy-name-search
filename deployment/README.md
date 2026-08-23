# Deployment

The server is managed by the Ansible playbook in this directory.

## What runs where

A single DigitalOcean droplet (Ubuntu 24.04) hosts all three concerns:

| Component | How it runs |
|-----------|-------------|
| **App** (Django 5.2 / Python 3.14) | gunicorn in a **podman container** (quadlet user unit for `django_user`), built on the droplet from git, listening on `127.0.0.1:8000` |
| **Web server** (Caddy) | `caddy` apt package (cloudsmith stable repo), managed by the `maxhoesel.caddy.caddy_server` role. Automatic Let's Encrypt HTTPS for the domain; the droplet IP 301-redirects to the domain |
| **Database** (PostgreSQL 18) | Host service from the PGDG repo. The container reaches it via a dedicated loopback alias IP (`10.99.0.1`) |

Roles and collections are declared in `requirements.yaml` and installed into
`roles/` and `collections/` (both gitignored).

## Prerequisites

- A fresh **Ubuntu 24.04** droplet (the tuning in `postgres.yaml` assumes
  ~4 vCPU / 16 GB).
- This checkout of the repo, with the dev group installed (the default
  `uv sync` from the repo root includes ansible):
  ```bash
  uv sync                            # from the repo root
  ```
  All commands below use `uv run ...` and are run from the `deployment/`
  directory.

## Configure before the first run

Edit these to match your target:

- `group_vars/all.yaml`
  - `app_public_host` — the droplet's public IPv4 address.
  - `env_allowed_hosts` — the domain(s) to serve over HTTPS (A record must
    point at the droplet).
  - `app_public_ports` — the ports UFW opens to the world (22 for SSH, 80
    and 443 for Caddy). SSH is open to any IP, so rely on key-based auth
    only (see Gotchas).
  - `git_branch` — the branch the app image is built from.
- `inventory` — `ansible_host` (droplet IP) and `ansible_user`.
- `group_vars/app.yaml` — `podman_build_repos[].repo` (the `git@github.com:...`
  URL of the private repo) and the container env vars.

## First-time setup (new server)

Run from `deployment/`. The plays in `setup.yaml` are ordered so a single
pass provisions a brand-new droplet.

1. Install the roles/collections:
   ```bash
   uv run ansible-galaxy install -fr requirements.yaml
   ```

2. Run through the steps up to (and including) the GitHub deploy key:
   ```bash
   uv run ansible-playbook setup.yaml \
     -t common -t postgres -t podman_user -t secrets -t postgres_objects -t git_key
   ```
   This sets up the base system, PostgreSQL, the app user, generates the
   secrets, creates the database, and **prints a public SSH key**.

3. **One-time manual step:** add the printed public key to the GitHub repo —
   *Settings → Deploy keys → Add deploy key* (read-only). The droplet uses it
   to clone the private repo.

4. Finish the deployment (idempotent — re-runs steps 2 harmlessly):
   ```bash
   uv run ansible-playbook setup.yaml
   ```
   The `app` play clones the repo, builds the image, and starts the gunicorn
   container (which runs migrations on start). The `caddy` play installs Caddy,
   writes the Caddyfile, and starts it. Caddy obtains the Let's Encrypt
   certificate for the domain on first start.

5. Verify:
   ```bash
   curl -sI https://<domain>/            # 200, valid TLS
   curl -sI http://<droplet-ip>/         # 301 → https://<domain>/
   ```

> Tip: run the individual tags in the order above if you prefer to watch each
> step, or just run the whole playbook (step 4) after step 3 — it is
> idempotent.

## Deploying (day-2)

- **Ship a code change:** push to `git_branch`, then
  ```bash
  uv run ansible-playbook setup.yaml -t app
  ```
  The podman role re-clones, rebuilds the image only if the git checkout
  changed, recreates the container (env changes also trigger a recreate), and
  the entrypoint runs migrations.

- **Change the web config / Caddyfile:** edit `group_vars/caddy.yaml`, then
  `uv run ansible-playbook setup.yaml -t caddy`.

- **Change app env / container:** edit `group_vars/app.yaml`, then
  `uv run ansible-playbook setup.yaml -t app`.

- **Full re-run** (`ansible-playbook setup.yaml`) is always safe and
  idempotent; use it after any change or to reconcile drift.

## Seeding demo data (optional)

The database starts empty (migrations create the schema and extensions; the
container runs them on start). To load realistic demo data, run the app's
`seed_data` command. The name-frequency CSVs it uses are gitignored local
artifacts — download them first (see the repo `README` for
`name_dataset/download_name_data.py`), then run inside the container:

```bash
# on the droplet
sudo -u django_user XDG_RUNTIME_DIR=/run/user/$(id -u django_user)/tmp \
  podman exec gunicorn uv run --no-sync python manage.py seed_data --count 100000 --flush
```

## Tags

| Tag | Play | What it does |
|-----|------|--------------|
| `common` | Base system | unattended upgrades, 2 GB swap, timezone, UFW (open `app_public_ports`: 22, 80, 443), git |
| `postgres` | PostgreSQL | PGDG repo, `lo-alias` loopback-alias service, `geerlingguy.postgresql` (install + configure + tune) |
| `podman_user` | Podman and the app user | create `django_user`, install podman, podman group, linger |
| `secrets` | Deployment secrets | first run only: generate `DJANGO_SECRET_KEY` + `DATABASE_PASSWORD` into `deploy_env_file` |
| `postgres_objects` | PostgreSQL users and database | idempotent DB user + database; sets the password to match the generated secrets |
| `git_key` | GitHub deploy key | generate a deploy keypair (prints the public key once), install the private key for the app user |
| `app` | App | git checkout of `git_branch`, `podman build`, quadlet gunicorn container, prune old images |
| `caddy` | Caddy web server | install Caddy (apt), write + validate the Caddyfile, start/reload |

## Secrets and credentials (all on-droplet, none committed)

| What | Where | Notes |
|------|-------|-------|
| `DJANGO_SECRET_KEY`, `DATABASE_PASSWORD` | `deploy_env_file` (default `/etc/talk-fuzzy-name-search/deploy.env`, `root:deploy` 0640) | Regenerate: delete the file, re-run `-t secrets`, then `-t postgres_objects -t app` |
| GitHub deploy keypair | `/etc/talk-fuzzy-name-search/github_deploy[.pub]` (`root:deploy`) | public key added to the repo as a read-only deploy key; private key installed to `~/.ssh/id_github_deploy` for the app user |
| Container env | `/home/django_user/env/gunicorn.env` (app user, 0600) | written by the podman role from `podman_env_vars`; the container is labeled with an env checksum so changed env triggers a recreate |

## Operational checks (read-only)

```bash
# app container + user units
sudo -u django_user XDG_RUNTIME_DIR=/run/user/$(id -u django_user) \
  systemctl --user status gunicorn app-network
sudo -u django_user XDG_RUNTIME_DIR=/run/user/$(id -u django_user) podman logs gunicorn

# web server
journalctl -u caddy -n 50

# database
pg_lsclusters
sudo -u postgres psql -l
```

## Gotchas

- **group_vars are per-group.** A host must be a member of the group a
  `group_vars/<name>.yaml` targets. The inventory uses `[postgres:children] app`
  and `[caddy:children] app` so those files apply to the single droplet.
- **SSH is exposed to the whole internet** (22 is in `app_public_ports`).
  Keep it key-based auth only, and consider fail2ban / rate limiting if the
  box is publicly reachable for long.
- **`tasks-load-deploy-env.yml` must stay a single-line Jinja template** —
  folding a multi-line expression into a `>-` block scalar silently yields `{}`.
- **Caddy runs as the `caddy` package user** (from the apt unit), storage under
  `/var/lib/caddy`. The droplet IP site is an `http://` address in the
  Caddyfile, so it never attempts a certificate and never reaches the app.
- **Postgres data is not backed up by this playbook.** Take a DO snapshot or a
  scheduled `pg_dump` separately.
