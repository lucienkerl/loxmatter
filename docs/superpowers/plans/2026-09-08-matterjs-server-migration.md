# Migration to matterjs-server — Implementation Plan

> **For agentic processors:** REQUIRED SUB-SKILL: `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to execute this plan task by task. Steps carry checkboxes (`- [ ]`) for checking off.

**Goal:** Migrate loxmatter from the archived library `python-matter-server` (8.1.2, no further updates) to its successor `matter-python-client` and move the test host from the old server image to `ghcr.io/matter-js/matterjs-server:stable`.

**Architecture:** The successor delivers `matter_server*` and `chip*` under **the same module paths**. The migration is therefore a dependency swap, not a rebuild of `src/`. Because both clients carry `SCHEMA_VERSION = 11`, the new client also talks to the old server — library and image migrate in separate commits, and the test host runs on after the first.

**Tech stack:** Python 3.12+, `uv` for dependencies and lockfile, `pytest` (asyncio_mode=auto), `ruff`, `mypy --strict`, Docker Compose on the test Pi.

**Design:** [docs/superpowers/specs/2026-09-08-matterjs-server-migration-design.md](../specs/2026-09-08-matterjs-server-migration-design.md)

## Global constraints

- All commands run from the worktree root directory, never with `cd` into the main checkout.
- New dependency: `matter-python-client>=1.4.0` (PyPI, Apache-2.0). Replaces `python-matter-server>=8.1.2` **completely** — the old line does not remain as a fallback.
- **No code changes under `src/loxmatter/`.** If a check shows otherwise, that's a finding that refutes the design: report it, don't silently patch it away.
- The full test suite takes around **three minutes**. That looks like a hang, it isn't — don't abort.
- Project language in source code and comments is German without umlauts in new comment blocks where the environment has held it that way; existing umlauts remain as they are.
- The three verification runs of the project (see `docs/DEVELOPMENT.md`): `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, `uv run pytest`.
- Task 3 is **not verifiable on this machine** and must be marked as untested in the commit and in the README.

---

### Task 1: Safeguard color commands against the SDK

**Why first:** The design relies in section 2.3 on command classes in the new `chip` package having the same names and fields. For `LevelControl`, `test_send_command_passes_the_payload_as_command_fields` already checks this. For **`ColorControl` it checks nothing** — `tests/commands/test_translate.py` only verifies the payload dicts that `translate.py` builds, it never runs through `chip.clusters.ClusterObjects.ALL_ACCEPTED_COMMANDS`.

So exactly the last-built feature (color and color temperature, commits `8cf2358` and `fb0a81c`) would be the one silently broken by a field rename in the new SDK. This test is written before the swap so it captures the old behavior and then guards the swap.

**Files:**
- Modify: `tests/matter/test_client.py` (append after `test_send_command_passes_the_payload_as_command_fields`, currently line 674–692)

**Interfaces:**
- Uses: `make_connected_pair(nodes)` and `FakeNode` from the same file (line 599 resp. 31); `MatterCall` from `loxmatter.commands.translate`; all already imported in this file.
- Provides: nothing for later tasks — pure safeguard.

- [x] **Step 1: Write the failing test**

Append to `tests/matter/test_client.py`, immediately after `test_send_command_passes_the_payload_as_command_fields`:

```python
async def test_send_command_builds_the_colour_temperature_command_from_the_sdk():
    """ColorControl (768) MoveToColorTemperature (10) through `chip`.

    `tests/commands/test_translate.py` only checks the payload dict that
    `translate.py` builds - never whether `chip.clusters.ClusterObjects.
    ALL_ACCEPTED_COMMANDS` makes from it a class with exactly these fields.
    Without this test, a renamed SDK field would silently break
    color temperature: the call would go out, the light would stay as
    it was.
    """
    bridge, upstream = make_connected_pair([FakeNode(12, {})])
    await bridge.connect()

    call = MatterCall(
        node_id=12,
        endpoint=1,
        cluster_id=768,
        command_id=10,
        payload={
            "colorTemperatureMireds": 370,
            "optionsMask": 1,
            "optionsOverride": 1,
        },
    )
    await bridge.send_command(call)

    _node_id, _endpoint_id, command = upstream.sent_commands[0]
    assert command.__class__.__name__ == "MoveToColorTemperature"
    assert command.colorTemperatureMireds == 370
    # The bit without which a color command on a switched-off light
    # fizzles (see `_EXECUTE_IF_OFF` in commands/translate.py).
    assert command.optionsMask == 1
    assert command.optionsOverride == 1


async def test_send_command_builds_the_hue_saturation_command_from_the_sdk():
    """ColorControl (768) MoveToHueAndSaturation (6), same rationale."""
    bridge, upstream = make_connected_pair([FakeNode(12, {})])
    await bridge.connect()

    call = MatterCall(
        node_id=12,
        endpoint=1,
        cluster_id=768,
        command_id=6,
        payload={
            "hue": 85,
            "saturation": 254,
            "transitionTime": 0,
            "optionsMask": 1,
            "optionsOverride": 1,
        },
    )
    await bridge.send_command(call)

    _node_id, _endpoint_id, command = upstream.sent_commands[0]
    assert command.__class__.__name__ == "MoveToHueAndSaturation"
    assert command.hue == 85
    assert command.saturation == 254
```

- [x] **Step 2: Verify both tests pass**

```bash
uv run pytest tests/matter/test_client.py -k "colour_temperature_command_from_the_sdk or hue_saturation_command_from_the_sdk" -v
```

Expectation: **2 passed.** Unlike typical TDD, this is correct here — the test captures existing behavior before the underlying basis is swapped. If it fails here, the field names mentioned in the test don't match the **installed old** SDK; then the test is wrong, not the code.

- [x] **Step 3: Prove the test really runs through the SDK**

A passing test doesn't yet prove it checks what it claims to check. The assertion here is specific: that `bridge.send_command` passes the payload **to a real SDK class**, not passes it through as a dict. If the latter were true, a renamed field would silently pass at library swap — and the test would stay green even though color temperature would be dead.

So produce the error experimentally, **in the test itself**: in the payload of `test_send_command_builds_the_colour_temperature_command_from_the_sdk`, mangle the key:

```python
            "colorTemperatureMiredsXXX": 370,
```

Then:

```bash
uv run pytest tests/matter/test_client.py -k "colour_temperature_command_from_the_sdk" -v
```

Expectation: **1 failed**, specifically with a `TypeError` like `__init__() got an unexpected keyword argument 'colorTemperatureMiredsXXX'` — not an `AssertionError`. That's exactly the proof: `send_command` calls `command_cls(**payload)` on a dataclass fetched from `ALL_ACCEPTED_COMMANDS`, and it knows its fields. An `AssertionError` at this point would mean the payload is being passed through as a dict somewhere and the test doesn't touch the SDK boundary at all — then the test is worthless and must be rewritten before task 2 starts.

Undo the mangling and rerun step 2: **2 passed.**

- [x] **Step 4: Verification runs**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

Expectation: all three without findings.

- [x] **Step 5: Commit**

```bash
git add tests/matter/test_client.py
git commit -m "$(cat <<'EOF'
test(matter): Verify color commands through the SDK

`tests/commands/test_translate.py` only verifies the payload dicts that
`translate.py` builds. Whether `chip.clusters.ClusterObjects.ALL_ACCEPTED_COMMANDS`
makes from it a class with exactly these fields is already checked for LevelControl
by `test_send_command_passes_the_payload_as_command_fields` - for
ColorControl nothing so far.

A renamed SDK field during the impending library swap would therefore
silently pass and hit the most recently built feature:
color and color temperature would be sent and remain ineffective.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Swap the dependency

**Files:**
- Modify: `pyproject.toml` (line 12 — dependency; lines 90–92 — mypy override comment)
- Modify: `uv.lock` (generated by `uv lock`, not by hand)
- Modify: `docs/LICENSING.md:20`
- Modify: `README.md:249-250`

**Interfaces:**
- Uses: the test from task 1 as a guard.
- Provides: a `src/` that runs against `matter-python-client`. Task 3 builds on it but doesn't depend on it (the new client also talks to the old server).

- [x] **Step 1: Swap the dependency**

In `pyproject.toml` line 12:

```toml
    "python-matter-server>=8.1.2",
```

replace with:

```toml
    # matter-python-client instead of python-matter-server (8 September 2026):
    # `python-matter-server` is ARCHIVED at 8.1.2 - that is the last
    # version that will ever be released. The successor `matterjs-server`
    # (matter.js, Matter 1.6.0) delivers this package under `python_client/`,
    # which provides `matter_server*` AND `chip*` under the same module
    # paths. Therefore only this line changes here, no module under
    # src/ - see design 2026-09-08, section 2.
    "matter-python-client>=1.4.0",
```

- [x] **Step 2: Refresh lockfile and install**

```bash
uv lock && uv sync
```

Expectation: `uv` resolves `matter-python-client` and removes `python-matter-server`.

- [x] **Step 3: Verify the old SDK is really gone**

This is the step most easily skipped and most costly: until now `home-assistant-chip-clusters` (pulled in by `python-matter-server`) provided the `chip` package. If both remain installed, the path order determines which `chip` wins — and the test from task 1 then proves the wrong one.

```bash
grep -c "home-assistant-chip-clusters" uv.lock
grep -c "python-matter-server" uv.lock
grep -c "matter-python-client" uv.lock
```

Expectation: `0`, `0`, and a number **greater than 0**.

```bash
uv run python -c "import matter_server, chip; print(matter_server.__file__); print(chip.__file__)"
```

Expectation: **both** paths lie below the same distribution directory and neither one lies below a directory whose name contains `home_assistant_chip` or `python_matter_server`.

- [x] **Step 4: Verify the command table is filled in the new package**

`ALL_ACCEPTED_COMMANDS` exists in the new package but is only filled by importing `chip.clusters.Objects` (side effect of class definitions). That it exists is not the same as: it carries something.

```bash
uv run python -c "
import chip.clusters.Objects
from chip.clusters import ClusterObjects
t = ClusterObjects.ALL_ACCEPTED_COMMANDS
print('cluster:', len(t))
print('onoff/1:', t[6][1].__name__)
print('level/4:', t[8][4].__name__)
print('color/10:', t[768][10].__name__)
print('color/6:', t[768][6].__name__)
"
```

Expectation:

```
cluster: <a number well over 40>
onoff/1: On
level/4: MoveToLevelWithOnOff
color/10: MoveToColorTemperature
color/6: MoveToHueAndSaturation
```

- [x] **Step 5: The full test suite**

```bash
uv run pytest
```

Expectation: **all tests pass.** About three minutes runtime — that's normal, don't abort.

If something fails here, that's the point where the design proves wrong. Then **don't** start adjusting `src/`, report the failure: the claim "no code changes in `src/`" should then be corrected before code works around it.

- [x] **Step 6: Update the mypy override comment**

In `pyproject.toml` lines 90–92:

```toml
# chip (part of the CHIP SDK that python-matter-server uses for cluster commands)
# provides no py.typed marking/stubs - see
# BridgeMatterClient.send_command in matter/client.py.
```

replace with:

```toml
# chip provides no py.typed marking/stubs - see
# BridgeMatterClient.send_command in matter/client.py. As of 8 September
# 2026 the package comes from the same distribution as `matter_server`
# (matter-python-client) and is generated from matter.js models, not
# from the CHIP SDK anymore; the missing type marking doesn't change.
```

- [x] **Step 7: Update the two documentation places**

`docs/LICENSING.md` line 20:

```markdown
| `python-matter-server`, chip SDK, `python-multipart` | Apache-2.0 |
```

replace with:

```markdown
| `matter-python-client` (contains `matter_server` and `chip`), `python-multipart` | Apache-2.0 |
```

`README.md` lines 249–250: the sentence currently reads

```markdown
Python 3.12+ with FastAPI and uvicorn for the HTTP service, Typer for the CLI,
[`python-matter-server`](https://github.com/home-assistant-libs/python-matter-server)
for the Matter side, SQLite for stored devices and settings.
```

replace with:

```markdown
Python 3.12+ with FastAPI and uvicorn for the HTTP service, Typer for the CLI,
[`matter-python-client`](https://github.com/matter-js/matterjs-server/tree/main/python_client)
for the Matter side, SQLite for stored devices and settings.
```

- [x] **Step 8: Verify no reference to the old package remains**

```bash
grep -rn "python-matter-server\|python_matter_server\|home-assistant-libs" README.md docs/LICENSING.md docs/SETUP.md docs/OPERATIONS.md docs/DEVELOPMENT.md pyproject.toml install.sh scripts/ src/
```

Expectation: **no hits** except explanatory mentions of history. Hits in `deploy/` belong to task 3 and stay here; hits under `docs/superpowers/` are designs and plans — they describe what applied then, and are not rewritten retroactively.

- [x] **Step 9: Verification runs**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

Expectation: all four without findings.

- [x] **Step 10: Commit**

```bash
git add pyproject.toml uv.lock docs/LICENSING.md README.md
git commit -m "$(cat <<'EOF'
build: migrate from python-matter-server to matter-python-client

python-matter-server is archived at 8.1.2 - that is the last version
that will ever be released. The successor matterjs-server (matter.js, Matter 1.6.0)
delivers the package matter-python-client under python_client/, which
provides `matter_server*` AND `chip*` under the same module paths.

Therefore only the dependency line changes here: no module under src/
is touched. Every one of the seven import locations and all six
used method signatures was verified (design 2026-09-08, section 2), and
the full test suite runs against the new package - including the
color commands that the previous commit first safeguarded against the SDK.

With `python-matter-server` also `home-assistant-chip-clusters` drops from
the lockfile: `chip` now comes from the same distribution as
`matter_server`, so no two packages compete for the same module name.

The server on the test host remains the old one for now - both clients carry
SCHEMA_VERSION 11, so the new one talks to the old server. The image
migration is a separate commit.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Update Compose and test host README for matterjs-server

**This task is not verifiable on the development machine.** There's no Docker here, no Pi, no Bluetooth adapter and no Thread radio module. What is created here is a reasoned design for the migration — exactly the role the first `Dockerfile` of this project took on, with the same explicit note in the file header.

**Files:**
- Modify: `deploy/testhost/docker-compose.yml` (service `matter-server`, lines 108–140)
- Modify: `deploy/testhost/README.md` (section "Enable BLE" from line 176; section "3. matter-server image path" from line 544)

**Interfaces:**
- Uses: nothing from task 1 or 2. The split is exactly the purpose (design, section 2.5).
- Provides: nothing for later tasks.

- [x] **Step 1: Switch the service block**

In `deploy/testhost/docker-compose.yml` replace the block starting at `image: ghcr.io/home-assistant-libs/python-matter-server:stable`. The new block, including the rationales — the file explains every line of it, and these here need more explanation than most:

```yaml
  matter-server:
    # matterjs-server instead of python-matter-server (8 September 2026,
    # UNTESTED - see README, section "Migration to matterjs-server").
    # python-matter-server is archived at 8.1.2; the successor speaks
    # the same WebSocket API and uses the same data directory, but migrates
    # it on the FIRST start to its own format. This migration is
    # one-way: if it fails, the Fabric is gone and every commissioned
    # device must be reset and re-paired. Before that
    # pull `GET /api/diagnostics/fabric-backup` and download the archive from the Pi
    # - that's exactly what the route is for.
    image: ghcr.io/matter-js/matterjs-server:stable
    container_name: matter-server
    network_mode: host
    restart: unless-stopped
    security_opt:
      - apparmor=unconfined
    volumes:
      # Same directory as before. IMPORTANT: the new container runs
      # unprivileged as UID 1000, the old image ran as root. Therefore, once
      # before the first start on the Pi:
      #     sudo chown -R 1000:1000 deploy/testhost/data
      #     sudo chmod -R u+rwX,go+rX deploy/testhost/data
      # Without this the container won't start - it cannot access its own
      # data directory.
      - ./data:/data
      - /run/dbus:/run/dbus:ro
    environment:
      # BLE via the host's BlueZ daemon instead of via a raw
      # HCI socket. The standard would be `hci`, and that requires permissions that
      # the unprivileged container user doesn't have - BLE commissioning
      # would then silently fail. The /run/dbus mount needed for it
      # is already above.
      #
      # The alternative mentioned in the docs - run container as root
      # (`user: 0:0`) - is DELIBERATELY NOT chosen here: it lifts the
      # container isolation, and this service runs with
      # `network_mode: host` anyway, already open on the network.
      NOBLE_BINDINGS: dbus
    command:
      - --storage-path
      - /data
      # `--paa-root-cert-dir` has been DROPPED WITHOUT REPLACEMENT (don't forget):
      # the successor's CLI docs list the option under "Deprecated
      # Options (were used in Python Matter Server) - not supported", because
      # matter.js obtains PAA root certificates via its own
      # DCL client.
      - --bluetooth-adapter
      - "${BLUETOOTH_ADAPTER:-0}"
```

The comment block above `matter-server` at the top of the file (lines 12–14) also mentions the old image; there replace `ghcr.io/home-assistant-libs/python-matter-server:stable` with `ghcr.io/matter-js/matterjs-server:stable` and mention `--bluetooth-adapter` as still valid, `NOBLE_BINDINGS=dbus` as new.

- [x] **Step 2: Verify Compose understands the file**

If `docker` is available on the machine:

```bash
docker compose -f deploy/testhost/docker-compose.yml config >/dev/null && echo "syntactically OK"
```

Expectation: `syntactically OK`. If `docker` is missing — the typical case on this machine — check the YAML syntax instead:

```bash
uv run python -c "import yaml,sys; yaml.safe_load(open('deploy/testhost/docker-compose.yml')); print('YAML OK')"
```

Expectation: `YAML OK`. **This is explicitly not proof that the stack starts** — only that the file is readable.

- [x] **Step 3: Correct the wrong successor hint in the README**

`deploy/testhost/README.md`, section "3. matter-server image path" (from line 544). The paragraph currently names `ghcr.io/matter-js/python-matter-server` as the successor. That's wrong: that is the **mirror of the old repository** under the new organization, not the successor. Following that hint lands back at 8.1.2.

Replace the section with:

```markdown
### 3. matter-server image path

Until 8 September 2026, `ghcr.io/home-assistant-libs/python-matter-server:stable` ran here.
The note that stood at this point named `ghcr.io/matter-js/python-matter-server`
as the successor — **that was wrong**: this path is only a mirror of the old
repository under the new organization and delivers the same frozen 8.1.2.

The actual successor project is
[`matterjs-server`](https://github.com/matter-js/matterjs-server) —
`ghcr.io/matter-js/matterjs-server:stable`, a re-implementation on matter.js
with the same WebSocket API. The migration is in the next section.
```

- [x] **Step 4: Write the migration guide**

Append immediately after the just-modified section:

```markdown
## Migration to matterjs-server (UNTESTED)

Since 8 September 2026, `deploy/testhost/docker-compose.yml` points to
`ghcr.io/matter-js/matterjs-server:stable`. **This migration has not yet
run on a Pi** — it is derived from the successor's documentation, not measured.
What stands here is the order in which it should be performed, and the two
places where it can fail.

### Before: back up the Fabric

The first start migrates `./data` to the format of the new server. This migration
is **one-way** — a path back to the old image is nowhere promised. If it fails,
the Fabric is lost and every commissioned device must be reset and
re-paired.

```bash
curl -sf -H "Authorization: Bearer $LOXMATTER_API_TOKEN" \
  http://<Pi>:8080/api/diagnostics/fabric-backup -o matter-fabric-backup.zip
```

**Download the archive from the Pi**, don't leave it there. It contains the
complete Fabric credentials and belongs neither in the repository nor in a log.

### The migration

```bash
docker compose stop matter-server
sudo chown -R 1000:1000 deploy/testhost/data
sudo chmod -R u+rwX,go+rX deploy/testhost/data
docker compose pull matter-server
docker compose up -d matter-server
docker compose logs -f matter-server
```

The `chown` is not a precaution, but a requirement: the old image ran
as root and wrote the directory accordingly, the new container runs
unprivileged as UID 1000. Without this step it won't start.

The log lines of the first start contain the migration. Only when there's no error
there and `loxmatter` connects again (`GET /api/diagnostics` shows the
`matter-server` point green), is the migration complete.

### What to check afterwards

Two points that don't follow from the documentation and can only be clarified on the device.
Until they are checked, this section remains titled "UNTESTED".

1. **BLE commissioning.** The Compose sets `NOBLE_BINDINGS=dbus` because the
   unprivileged container cannot open a raw HCI socket. The path via
   BlueZ assumes that `bluetoothd` is running and `hci0` is `Powered` — on
   this Pi the adapter was already rfkill-soft-blocked once (see section
   above, that is independent of the server). Check by commissioning a device via the
   pairing code in the WebUI.
2. **Capitalization of command names.** The successor's WebSocket documentation
   shows command names in camelCase (`moveToLevelWithOnOff`); the
   Python client sends PascalCase (`MoveToLevelWithOnOff`) because it
   forwards `command.__class__.__name__`. The server must accept both,
   otherwise its own client would be broken — that is a conclusion, not a measurement.
   Check by switching a light in the WebUI **and** adjusting its
   brightness.
```

- [x] **Step 5: Verify the README references are correct**

```bash
grep -n "home-assistant-libs/python-matter-server" deploy/testhost/docker-compose.yml
grep -n "matter-js/matterjs-server" deploy/testhost/docker-compose.yml deploy/testhost/README.md
```

Expectation: the first command finds **nothing more** in the Compose file. The second finds the new path in both files.

The section "Enable BLE" (from line 176) still quotes `docker run --rm ghcr.io/home-assistant-libs/python-matter-server:stable --help` as proof of where the option name comes from. **This quote remains** — it proves what was measured then, and a proof you rewrite retroactively is no longer a proof. Instead, append at the end of the section:

```markdown
> **As of 8 September 2026**, `matterjs-server` runs here. `--bluetooth-adapter`
> is named the same there, but the container is unprivileged and additionally needs
> `NOBLE_BINDINGS=dbus` — see "Migration to matterjs-server". The quote above remains
> as evidence for the old image.
```

- [x] **Step 6: Verification runs**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

Expectation: all four without findings. This task doesn't touch any Python code; the runs only prove nothing broke incidentally.

- [x] **Step 7: Commit**

```bash
git add deploy/testhost/docker-compose.yml deploy/testhost/README.md
git commit -m "$(cat <<'EOF'
deploy: Switch test host to matterjs-server (UNTESTED)

Image to ghcr.io/matter-js/matterjs-server:stable. Three changes that
are not optional:

- `--paa-root-cert-dir` is dropped without replacement: the successor's CLI docs
  list the option as "not supported", matter.js obtains the
  PAA root certificates via its own DCL client.
- `NOBLE_BINDINGS=dbus`, because the new container runs unprivileged and
  cannot open a raw HCI socket - without it BLE would silently fail.
  The alternative (run container as root) would have lifted the isolation.
- `chown -R 1000:1000` on the data directory before first start, because
  the old image wrote as root and the new user otherwise can't access it.

NOT RUN ON A PI. Derived from the successor's documentation, not
measured - like the first Dockerfile design back then. The README names the
order of the migration, the one-way data migration (back up Fabric first),
and the two points that can only be clarified on the device: BLE via D-Bus and
whether the server accepts PascalCase command names.

Incidentally corrected: the README named `ghcr.io/matter-js/python-matter-server`
as the successor. That's only a mirror of the old repo and delivers the same
frozen 8.1.2.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Coverage against the design

| Design | Task |
| --- | --- |
| 2.1 Imports, 2.2 Method signatures | Task 2, steps 3 and 5 (full suite) |
| 2.3 Command classes | Task 1 (color) + Task 2, step 4 |
| 2.4 `attribute_subscriptions` | no step needed — loxmatter never reads that field |
| 2.5 Schema version | justifies the split; proved by task 3, which doesn't depend on task 2 |
| 3. Commit 1 | Task 2 |
| 4. Commit 2, points 1–4 | Task 3, steps 1 and 4 |
| 5.1 Data migration | Task 3, step 4 (back up Fabric before migration) |
| 5.2 Command name spelling | Task 3, step 4 ("What to check afterwards", point 2) |
| 6. Not part of this design | no task — intentional |

**Deviation from design:** The design names two commits, this plan has three. Task 1 was added because when writing the plan it emerged that section 2.3 relies on command classes, but no test through `chip` exists for `ColorControl`. Without this test, the suite would guard the swap for `LevelControl`, not for the most recently built feature.

---

## Addendum (final review, 8 September 2026)

The plan text above remains unchanged. It is the protocol of what
was commissioned — not what ends up in the repository. Whoever types it in
types in two errors that the implementation has meanwhile fixed.
Both are in **Task 3, step 4** (the Markdown block setting the
migration guide), both fixed with commit `0bfd53b`:

1. **`GET /api/diagnostics` is not a route.** The plan names it as proof
   that the migration is complete. The router carries `/api/diagnostics` as
   a prefix, the route under it is called `/system` (`api/diagnostics.py`) — a
   bare call responds with 404, with the same failure it should exclude.
   Correct is **`GET /api/diagnostics/system`**. The
   README additionally says since `0bfd53b` that the short path doesn't
   exist, so nobody mistakes it for a typo.
2. **The command block mixed two working directories.** `chown`/`chmod`
   took a path from repo root (`deploy/testhost/data`), `docker
   compose` should have run from `deploy/testhost`. Read as a block
   the first `docker compose` call fails with "no configuration file
   provided". Correct is a `cd ~/matter-loxone/deploy/testhost` first and
   then the relative `data`, as the rest of the README holds.

A third point concerns not the plan but the design it relies on: section 2.2
counted the touchpoints wrong ("exactly six methods"). The number appears
that way in the commit message that task 2, step 10 prescribes ("all six
used method signatures"), and thus entered in `beee42f`. Both remain; the
correction — it's eight methods and one read property, and the payload of
`set_thread_operational_dataset` changes — is in the design,
section 2.2.
