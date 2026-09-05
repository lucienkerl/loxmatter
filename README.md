<div align="center">

<img src="src/loxmatter/web/icon.svg" alt="" width="96" height="96" />

# loxmatter

### Matter devices in Loxone — self-hosted, no cloud

Your Miniserver does not speak Matter. This bridge makes it anyway: any value a
Matter device reports can become a virtual input, every Loxone command becomes a
Matter command, and the Loxone objects for it are generated rather than typed by
hand.

![License: GPL-3.0-or-later](https://img.shields.io/badge/license-GPL--3.0--or--later-blue.svg)
![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)
[![CI](https://github.com/lucienkerl/loxmatter/actions/workflows/ci.yml/badge.svg)](https://github.com/lucienkerl/loxmatter/actions/workflows/ci.yml)

[What it does](#-what-you-can-do) · [Screenshots](#-the-web-interface) ·
[How it works](#-how-it-works) · [Quickstart](#-quickstart) · [Docs](#-documentation)

</div>

## Why loxmatter

Loxone has no Matter support, and Matter devices have no idea what a Miniserver is.
The usual answer is a cloud bridge per vendor. This is the other answer: one service
on your own hardware that reads devices generically — no curated list of supported
models, so a device bought tomorrow works today — and hands Loxone something it already
understands. That held without exception for attributes in testing; event detection is
FeatureMap-based and cluster-specific instead, since neither test device advertised an
`EventList`. Details, numbers and the consequences are in the design spec's validation
section,
[section 3.5](docs/superpowers/specs/2026-09-01-matter-loxone-bridge-design.md#35-abbildung-generisch-statt-kuratiert).

## ✨ What you can do

<table>
<tr>
<td width="50%" valign="top">

### 📟 Commission devices from the browser
Add a Matter device over Bluetooth with its setup code. Thread devices reach the
bridge through the border router in the same stack; Wi-Fi and Ethernet devices go
straight over IP.

</td>
<td width="50%" valign="top">

### 🎛 Pick the signals you actually want
A single plug can expose over a hundred values. The functional ones are selected by
default; everything else waits in a collapsed “expert” block with its own checkbox.

</td>
</tr>
<tr>
<td width="50%" valign="top">

### 📄 Generate the Loxone objects
Virtual UDP inputs and virtual outputs come out as importable template files, one
pair per device, instead of being typed into Loxone Config by hand.

</td>
<td width="50%" valign="top">

### 🔁 Patch your existing project file
Upload the Loxone project you already have, see exactly what would change per device
and per signal, download the patched copy. Nothing is downloaded before you have seen
the plan. Updating existing inputs/outputs is the default path; adding completely new
device containers is experimental and unverified, see
[docs/OPERATIONS.md#project-file-sync](docs/OPERATIONS.md#project-file-sync).

</td>
</tr>
<tr>
<td width="50%" valign="top">

### 🔍 Watch it work
A live feed of log lines, outgoing datagrams and incoming commands — the same lines
`docker logs` would show, without shell access to the host.

</td>
<td width="50%" valign="top">

### 🔒 Locked down by default, in your language
No `/api` route answers before a password is set. `/cmd` and `/resync` stay open
regardless — the Miniserver cannot send a header or a cookie — so whoever reaches the
port can still switch a device; see
[docs/OPERATIONS.md#access-control](docs/OPERATIONS.md#access-control). The interface
speaks English or German, switchable in the settings, and the setting applies to the
CLI too.

</td>
</tr>
</table>

## 🖼 The web interface

<table>
<tr>
<td width="50%" valign="top">
<img src="docs/screenshots/dashboard.png" alt="Device list with live values and controls" />

**Devices**<br>Every commissioned device on one page, with live values and controls, and a badge where signals changed since the last export.

</td>
<td width="50%" valign="top">
<img src="docs/screenshots/commissioning.png" alt="Commissioning field with a pairing code entered" />

**Commissioning**<br>Paste the pairing code from the device or its packaging and start — no account, no cloud round trip. A device already paired with Apple, Google or a DIRIGERA needs an extra multi-admin code from that vendor's app first; its own printed code no longer works here.

</td>
</tr>
<tr>
<td width="50%" valign="top">
<img src="docs/screenshots/signals.png" alt="Signal list with Loxone addresses and export checkboxes" />

**Signals**<br>Each signal with the Loxone address it will get and its own export checkbox; the administrative ones sit behind the collapsed expert section.

</td>
<td width="50%" valign="top">
<img src="docs/screenshots/export.png" alt="Project file sync card, template export card and a filled-in export preview table" />

**Export**<br>Upload a project file for a patched copy, or generate the per-device template files — with the bridge address and ports they use shown alongside, and a preview of what each file will contain before anything downloads.

</td>
</tr>
<tr>
<td width="50%" valign="top">
<img src="docs/screenshots/project-sync.png" alt="Diff plan showing new, updated and orphaned entries" />

**Project file sync**<br>The plan before anything is written: how many entries are new, updated or orphaned, and per device the old value next to the new one.

</td>
<td width="50%" valign="top">
<img src="docs/screenshots/system.png" alt="Live diagnostics controls above log, UDP capture and command log panes" />

**System**<br>Three live panes below the diagnostics controls — the command log fills as requests come in; log lines and UDP capture stream the same way once a real `matter-server` and device traffic sit behind the bridge. The Matter fabric backup is pulled from here too.

</td>
</tr>
<tr>
<td width="50%" valign="top">
<img src="docs/screenshots/settings.png" alt="Miniserver connection, language selection and periodic resend" />

**Settings**<br>The Miniserver connection, the interface language, and how often marked signals are resent even when nothing changed.

</td>
</tr>
</table>

## 🏗 How it works

```mermaid
flowchart TB
    thread["🌡️ Thread device<br/>sensor, button …"]
    ip["💡 Wi-Fi or Ethernet device<br/>lamp, plug, appliance …"]

    subgraph Host["One host — e.g. a Raspberry Pi"]
        otbr["🔀 otbr<br/>Thread border router"]
        ms["🧠 matter-server<br/>drives the devices"]
        lm["🌉 loxmatter<br/>this bridge"]
    end

    mini["🏠 Loxone Miniserver<br/>virtual inputs and outputs"]
    browser["🖥️ Browser<br/>setup & diagnostics"]

    thread -- Thread --> otbr
    otbr --> ms
    ip -- "IP network" --> ms
    ms -- "values · subscription" --> lm
    lm -- commands --> ms
    lm -- "values · UDP" --> mini
    mini -- "commands · HTTP" --> lm
    lm <-- "HTTP · WebSocket" --> browser

    classDef device fill:#98a3ad,stroke:#6f7a85,color:#12161a
    classDef svc fill:#5b6572,stroke:#3f4750,color:#f4f5f7
    classDef bridge fill:#a15a2c,stroke:#7d4522,color:#fdf3e0
    classDef endpoint fill:#e2915c,stroke:#a15a2c,color:#2a1a10

    class thread,ip device
    class otbr,ms svc
    class lm bridge
    class mini,browser endpoint

    style Host fill:none,stroke:#98a3ad,stroke-dasharray:5 5
```

`matter-server` holds the Matter fabric and delivers values by subscription. loxmatter
turns those values into datagrams for the Miniserver, and the commands coming back
from Loxone over HTTP into Matter commands. The browser hangs off the bridge for setup
and diagnostics only — the runtime path between devices and Miniserver does not use it.

## 🚀 Quickstart

Try it without any hardware:

```bash
git clone git@github.com:lucienkerl/loxmatter.git
cd loxmatter
uv sync
uv run loxmatter inspect --fixture tests/fixtures/nodes/example_light.json
```

For a real setup — Docker stack with `otbr`, `matter-server` and the bridge — follow
[docs/SETUP.md](docs/SETUP.md).

## 🗺 Status

**Built:** commissioning, signal extraction, the template export, the runtime path in
both directions, the web interface and its access control.

**Validated** against two real IKEA devices on a running `matter-server`:
commissioning, signal extraction, the runtime path in both directions, and the web
interface with its access control.

**Not yet done: the run against a real Miniserver.** The generated templates have only
been checked against a rebuilt Miniserver, never imported into Loxone Config.

**No TLS.** The service speaks plain HTTP — the password and any API token cross the
network in the clear. Use a randomly generated password that is used nowhere else.

**First come, first served.** Until a password is set, anyone who can reach the port
can claim the bridge. Set it within minutes of the first start, not days.

## 📚 Documentation

| Document | What is in it |
|---|---|
| [docs/SETUP.md](docs/SETUP.md) | [Requirements](docs/SETUP.md#requirements), the [hardware-free tour](docs/SETUP.md#try-it-without-hardware), [your own Docker setup](docs/SETUP.md#your-own-setup), and [looking at a device](docs/SETUP.md#looking-at-a-device) from the CLI. |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | [Running the bridge](docs/OPERATIONS.md#running-the-bridge), [what a template contains](docs/OPERATIONS.md#what-a-template-contains), [project file sync](docs/OPERATIONS.md#project-file-sync), [access control](docs/OPERATIONS.md#access-control) and [language](docs/OPERATIONS.md#language). |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | Running the test suite and the checks CI runs. |
| [docs/LICENSING.md](docs/LICENSING.md) | Third-party licences and the notices in the source files. |

## 🧰 Tech stack

Python 3.12+ with FastAPI and uvicorn for the HTTP service, Typer for the CLI,
[`python-matter-server`](https://github.com/home-assistant-libs/python-matter-server)
for the Matter side, SQLite for stored devices and settings. The web interface is plain
HTML, CSS and Alpine.js — no build step, nothing fetched from a CDN at runtime.

## Contributing

Issues and pull requests are welcome. Please run the checks from
[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) before opening one; the commit messages in
this repository are written in German.

## License

**GNU General Public License, version 3 or later** — the full text is in
[`LICENSE`](LICENSE). In practice: you may use, modify and pass on this tool, and
whoever passes on a modified version has to publish their changes under the same
licence. Third-party licences and the notices in each source file:
[docs/LICENSING.md](docs/LICENSING.md).
