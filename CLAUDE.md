# Neuron Platform — Project memory for Claude (durable instructions)

> This file is read at the start of every Claude session in this repo. It
> captures durable conventions and ongoing requirements that span tasks.
> See also `docs/PROJECT_MEMORY.md` for cross-stage design notes.

## Operating constraints (do not violate)

- **No coupling to ShitalEco.** The platform deploys at
  `https://neuron.shital.org.uk` but the only acceptable touchpoint with
  `/opt/shitaleco/` is the host nginx vhost at
  `/opt/shitaleco/nginx/conf.d/neuron.conf` (managed via the watchdog at
  `scripts/vhost-watchdog.sh` + `/opt/neuron-platform/host-nginx/neuron.conf`
  canonical). Do not edit anything else under `/opt/shitaleco/`.
- **Compose project name is `master_platform`** (default from directory
  basename). Never override `COMPOSE_PROJECT_NAME` in scripts unless you
  also migrate all volumes + container names.
- **VPS work runs as user `neuron`** (UID 1001), filesystem under
  `/opt/neuron-platform/`. Never run admin docker commands as root unless
  the script is explicitly designed for it (e.g. cert issuance).
- **The webhook deployer fetches via SSH** using the deploy key at
  `/home/neuron/.ssh/neuron_platform_deploy`. The key is bind-mounted into
  the deployer container at `/tmp/deploy_key` and copied to
  `/root/.ssh/id_ed25519` at runtime by `deployer/deploy.sh`.

## Durable feature requirements (from product owner)

### 1. Feature / capability request tracker

Maintain a DB-backed registry of every requested feature/capability with
a status flow: `requested` → `in_dev` → `deployed_dev` → `deployed_prod`.
Admin UI lives at `/ui/features` with create/edit/transition affordances.
Every commit that ships a feature should reference the feature row (by
short id) in its commit message so the admin UI can auto-fill the SHA.

### 2. Reusable function / library registry

Every reusable function or library added to the platform must be
catalogued in the `code_functions` table with:
- name (stable identifier, kebab-case)
- description (human-readable)
- language / runtime (python, micropython, ts, sql, shell, ...)
- inputs (JSON schema-like array of `{name, type, required, description}`)
- outputs (JSON schema-like array of `{name, type, description}`)
- api_endpoint (if exposed over HTTP, the `/api/...` path)
- source_path (where in the repo)
- tags
- status (`draft`, `stable`, `deprecated`)
- examples (optional code snippet)

Admin UI lives at `/ui/functions` with search/filter and a detail page
that renders the inputs/outputs and a copy-pasteable call example.
When new reusable code lands, add or update a row.

## UI conventions

- Tailwind via CDN + HTMX + Jinja2. No JS build step.
- **Mobile-first**: every page must work on a 360px-wide viewport.
  - Grids declare a base `grid-cols-1` (or none) and use `sm:` / `md:` /
    `lg:` / `xl:` prefixes for wider breakpoints.
  - Tables MUST be wrapped in `<div class="overflow-x-auto">…</div>` so
    they scroll horizontally instead of overflowing the viewport.
  - Avoid fixed widths (`w-[Npx]` or `min-w-[Npx]`) on top-level
    containers; use `max-w-*` instead.
- Dark mode is the only theme. Backgrounds: `bg-slate-950` / `bg-slate-900`.
  Borders: `border-slate-800`. Text: `text-slate-100/200/300/400` graded.
- Forms use the `forms` Tailwind plugin (already loaded in `base.html`).
- HTMX is loaded globally; prefer `hx-post` / `hx-get` with `hx-target` +
  `hx-swap="outerHTML"` for partial updates over full-page reloads.

## Commit / PR conventions

- Imperative commit subjects, lowercase, prefixed with one of:
  `feat:`, `fix:`, `ops:`, `ci:`, `docs:`, `refactor:`, `decouple:`.
- Body explains the *why*, not the *what*.
- Include a session URL footer (`https://claude.ai/code/session_...`)
  when the commit was driven by a Claude session.
- The deploy branch is currently `claude/init-neuron-platform-YEIak`;
  the canonical post-cutover branch is `main`.

## Pipeline reminders (debug if any of these break)

```
push to main / deploy branch
  └─ GitHub Actions (.github/workflows/deploy.yml)
       └─ POST https://neuron.shital.org.uk/deploy + X-Deploy-Secret
            └─ shitaleco-nginx-1 (host TLS via our OWN cert at
                /etc/letsencrypt/live/neuron.shital.org.uk/)
                 └─ 172.17.0.1:8089 → neuron-deployer container
                      └─ git fetch origin <branch> (SSH via deploy key)
                           └─ bash deploy.sh
                                └─ docker compose build neuron-master
                                     └─ docker compose up -d neuron-master
                                          └─ /healthz → 200
```

Watchdog at `neuron-vhost-watchdog.timer` (systemd, 60s) restores the
host nginx vhost if shitaleco-deployer ever overwrites it.
