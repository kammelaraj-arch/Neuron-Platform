# Neuron Platform — Project memory for Claude (durable instructions)

> This file is read at the start of every Claude session in this repo. It
> captures durable conventions and ongoing requirements that span tasks.
> See also `docs/PROJECT_MEMORY.md` for cross-stage design notes.

## Operating constraints (do not violate)

- **Parent-only communication is a hard rule.** No child device (Edge /
  Gateway / Device) may accept inbound traffic from anyone but its
  parent. Firmware bundles MUST emit deny-all inbound firewall rules
  (except loopback + the established mTLS session to the parent). SSH
  on the child is disabled at boot; the parent exposes a reverse-tunnel
  on demand. No peer-to-peer between siblings. See
  `docs/wizard_spec.md` § "Hard rules".
- **The canonical wizard spec lives at `docs/wizard_spec.md`.** It is
  the streamlined 10-screen funnel that supersedes the historical
  per-step layout. When code disagrees with that file, the file wins
  until amended there.
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

## Architecture rules

### Device-registration flow (step-wise wizard)

Current device-creation form is a single flat page (pick Edge → Compute →
multi-select Components → versions → Register). Product owner wants a
**step-wise wizard** instead, with this hierarchy:

```
Edge system  (already exists)
  └─ Group       (NEW — logical sub-section of an edge,
                   e.g. "Heater bank", "Production Line A")
       └─ Board(s)   (one or more per group; search-and-select from the
                       control_board_library, e.g. L297 stepper driver,
                       SSR relay, ADC HAT, IO expander)
            └─ Component(s)  (one or more per board; search-and-select
                                from components_library — sensors,
                                motors, actuators wired to that board)
                 └─ GPIO pin map  (compute module pins ↔ board pins,
                                     auto-allocated by the pin
                                     allocator, with manual override)
```

Implementation rule:
- New tables: `edge_groups`, `board_instances`, `component_instances`.
- `Device.board_stable_id` deprecated in favour of an FK list to
  `board_instances`.
- The current pin allocator already exists in
  `backend/pin_allocator.py` — it runs **per board** now, not per device.
- UI is a multi-step wizard at `/ui/devices/new` (replaces the flat form).
- Backward-compat: existing devices keep working; migration backfills
  one default group + one default board per legacy device.

### Hierarchy flexibility (Root / Node / Edge / Device)

The 4-level hierarchy described in `docs/PROJECT_MEMORY.md` is the
**maximum** shape, not the required one. Schema and UI MUST support:

- **Root → Node → Edge → Device** (full hierarchy, multi-site)
- **Root → Edge → Device** (Node is optional; small / single-region deploys
  skip it entirely)
- **All-in-one** — a single host plays Root + Node + Edge simultaneously
  for tiny deployments or test rigs. UI must let an operator stand this up
  in one step.

Implementation rule: `EdgeSystem` must accept EITHER a `node_id` OR a
`root_id` as parent (DB check constraint: exactly one of the two is set).
`NodeSystem` always requires a `root_id`. `Device.edge_id` stays required.

The System Designer page (`/ui/systems`) must offer both placement
options when creating an Edge ("under Node X" / "directly under Root Y").

### Per-instance risk + local brain failsafe contract

Every `ComponentInstance` carries operator-set risk metadata that gets
baked into the firmware bundle's `brain.json`:

- `risk_level`: `nominal | advisory | critical | life_safety`
- `risk_types_json`: list from `RISK_TYPES`
  (`fire | scald | burn | shock | chemical | biohazard | pinch |
   crush | cut | fall | freeze | asphyxiation | explosion | uv | laser |
   noise | pressure`)
- `failsafe_action`: `off | hold_last | go_to_safe_value | alarm_only |
  stop | fail_open | fail_closed`
- `failsafe_value_json`: optional concrete value for `go_to_safe_value`
- `disconnect_grace_seconds`: how long the brain tolerates link loss
  before enforcing failsafe (default 30s)
- `watchdog_ms`: max gap between commands before link considered dead
  (default 1000ms)

**Hard rule**: the local brain on the device MUST be able to enforce
the failsafe action without any network connectivity. It is the last
line of defence. The Master and Edge are advisory; the brain is
authoritative for safety. Heaters / pumps / mains-switched relays
should default to `off` or `fail_closed`. Sensors default to
`alarm_only`. The firmware-bundle builder (FR-0005 step 6) translates
these per-instance rules into `brain.json` interlocks.

### Firmware default-channel contract

Every firmware bundle (FR-0005 step 6 builder) MUST include these channels
by default — no operator opt-in required:

1. **Command-and-control channel** — bi-directional link to the parent
   (Edge or, if Edge unavailable, direct Master) over mTLS. Carries
   recipe commands, parameter updates, telemetry uploads.
2. **Emergency channel** — separate mTLS channel with a separate cert
   and a strictly-limited command set (`safe_stop`, `safe_shutdown`,
   `status`). Even when the main control channel is jammed / dead,
   the emergency channel must remain reachable from authorised
   operators. Schema already drafted in
   `shared_schemas/access_policy_schema.json`.
3. **OTA channel** — configured by default in every bundle so devices
   can receive base-firmware / app-bundle / config updates without
   manual provisioning. Subject to the OTA base-version gating rules
   in `docs/ota_base_version_policy.md`.

### State-change auto-push to parent

Any state change at the Edge (device coming online, device going offline,
twin desired/reported drift, recipe step transition, alarm raised,
allow-list violation, cert revocation) MUST be pushed up to the parent
(Node if present, else Root/Master) automatically, without operator
intervention. Push-up is fire-and-forget at the Edge — the Edge
continues running even if Master is unreachable, but it queues missed
state updates and replays them on reconnect.

### Bi-directional heartbeat monitoring

Every link in the hierarchy runs heartbeat both ways:

- **Child → Parent**: the device / Edge periodically heartbeats its
  presence + uptime + summary metrics to the parent. Parent flags
  the child `offline` after a configurable grace period.
- **Parent → Child**: the parent also heartbeats DOWN to the child.
  When the child detects parent-heartbeat loss for longer than the
  child's `disconnect_grace_seconds`, the local brain enforces the
  per-instance failsafe action defined per ComponentInstance.

This bidirectionality is what makes the local-brain failsafe contract
operationally meaningful — without parent-heartbeat-down detection,
the child would never know it should fall back to autonomous mode.

### SSH / first-boot deployment credentials

Each `EdgeGroup` stores the SSH details used to push the firmware
bundle to the compute module on first registration + later OTA:

- `ssh_host`, `ssh_port` (default 22), `ssh_username`
- `ssh_password_encrypted` — Fernet-encrypted, via
  `security/secret_crypto.py`
- `ssh_private_key_encrypted` — preferred over password
- `sudo_password_encrypted` — when sudo needs a password
- `mdns_hostname` — e.g. `raspberrypi.local`

Plaintext is decrypted only at deploy time inside the worker that
SSHes to the device. Audit log captures `group.set_ssh` events with
flags (host, port, username, has_password, has_private_key,
has_sudo_password) but never the actual secrets.

End-to-end deploy flow (FR-0005 step 6 builder):
  1. Wizard captures SSH creds + WiFi + boards + components + pin map.
  2. Build action emits the firmware bundle (DNA + Brain + WiFi +
     allowed-emergency cert + signed app bundle).
  3. Deploy worker reads SSH creds via decrypt_secret, SSHes to the
     compute, scp's the bundle, runs `install.sh` on the device,
     verifies `/healthz` reachable, marks the group as deployed.
  4. Subsequent OTA updates reuse the same SSH path.

### Configuration lock with PIN

After an operator has physically tested a Group's configuration on
hardware, they can lock it from the wizard's Review page (step 6) with
a 4-12 digit PIN (Argon2id-hashed at rest). When locked:

- Write endpoints (pin-map create/delete/lock, component add/delete/
  risk, WiFi assignment, compute change, board add/delete) refuse
  mutations until the PIN is entered.
- Unlock is per-session: enter the PIN once on the Review page and
  the session's `unlocked_groups[<gid>] = <ts>` flag stays set for
  the life of the session.
- Schema fields on `EdgeGroup`: `lock_pin_hash`, `locked_at`,
  `locked_by`. Routes: `POST .../{gid}/lock`, `POST .../{gid}/unlock`.

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
