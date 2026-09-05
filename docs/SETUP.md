# Setup

[Back to the README](../README.md)

## Requirements

**Software**

- [Docker](https://docs.docker.com/get-docker/) with the Compose plugin —
  for the full stack (recommended for running it yourself)
- or, for anyone who wants only the command line without containers:
  Python 3.12+ and [`uv`](https://docs.astral.sh/uv/getting-started/installation/)
- Git

**Hardware**

- A Loxone Miniserver on the same network as the machine running loxmatter
- A host where the service runs permanently — e.g. a Raspberry Pi 4 on the
  same network as the Miniserver and the devices (this project's test
  environment runs this way, see [`deploy/testhost/`](../deploy/testhost/))
- Only for **Thread** devices: a USB radio module as a Thread radio adapter
  (e.g. a SONOFF Dongle Plus MG24) on the host — the Docker stack brings its
  own OpenThread Border Router for this
- A Bluetooth adapter on the host, for commissioning devices over BLE
  (Matter commissioning)

No prior knowledge of Matter or Thread required — the Docker stack already
brings the complete Matter controller (`matter-server`) and the Thread
border router (`otbr`) with it.

## Getting started

### Try it without hardware

Shows how loxmatter reads a device — runs entirely against a stored example
device, without network access or real hardware:

```bash
git clone git@github.com:lucienkerl/loxmatter.git
cd loxmatter
uv sync
uv run pytest                                                    # Testsuite, ohne Hardware
uv run loxmatter inspect --fixture tests/fixtures/nodes/example_light.json
```

See [Looking at a device](#looking-at-a-device) for more on this.

### Your own setup

With real hardware:

1. Clone the repository on the host where it should run permanently:

   ```bash
   git clone git@github.com:lucienkerl/loxmatter.git
   cd loxmatter/deploy/testhost
   ```

2. Create `.env` from the template and fill it in (radio adapter, Bluetooth,
   the Miniserver's IP):

   ```bash
   cp .env.example .env
   ```

   Details on each variable are in the comments in
   [`.env.example`](../deploy/testhost/.env.example).

3. Start the stack:

   ```bash
   docker compose up -d --build
   ```

   This builds and starts three containers. See
   [how the pieces fit together](../README.md#-how-it-works) for what each
   of the three containers does.

4. Open `http://<Host>:8080/` in your browser. On the very first visit, the
   interface shows an initial setup step — pick a password, see access
   protection under [Running loxmatter permanently](../README.md#dauerhaft-betreiben-loxmatter-run)
   in the README.

5. In the web interface, commission a device, view it, and export the
   template (`VIU_*.xml`, `VO_*.xml`). Import these files into Loxone Config
   and drag the resulting inputs/outputs onto the desired function blocks —
   that stays manual work, but only once per device.

> **Note:** [`deploy/testhost/`](../deploy/testhost/) is the environment
> this project has been tested against so far — not a hardened production
> image (a non-root user, pinned digests, and the like are still open).
> Still the most straightforward path for home use today; the security
> notes under [Running loxmatter permanently](../README.md#dauerhaft-betreiben-loxmatter-run)
> apply unchanged.

## Looking at a device

```bash
uv run loxmatter inspect --fixture tests/fixtures/nodes/example_light.json
uv run loxmatter inspect --node 12          # gegen laufenden matter-server
```

The first call works today with no further preparation. The second needs a
reachable matter-server (default address `ws://localhost:5580/ws`,
changeable via `--url`) — it works and has been tried against real
hardware, see [`deploy/testhost/`](../deploy/testhost/) for the test
environment.
