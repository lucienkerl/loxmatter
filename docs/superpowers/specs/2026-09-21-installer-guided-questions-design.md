# Installer: Guided Questions for the Thread Stick, Bluetooth and the Miniserver

Design, 21 September 2026. Replaces the three bare prompts in `install.sh`
that decide the radios and the Miniserver address with numbered menus that
show what was found on the host, say what each question is for, and check
the Miniserver address before writing it.

Connects to [the one-liner design](2026-09-05-install-oneliner-design.md),
whose flow (section 4) and test harness (section 9) this changes, and to
[Radios in the Web UI](2026-09-11-radios-in-the-web-ui-design.md), whose
Radios card stays the place where every radio choice can be changed later.

## 1. The Problem

Measured on a fresh install on a Raspberry Pi 3B on 21 September 2026, and
read out of `install.sh` as it stands at `312c133`:

- **The Thread stick is a guess.** `detect_radio_device` takes the first
  `/dev/ttyUSB*` or `/dev/ttyACM*` that exists. On a host with a Zigbee
  coordinator, that stick is proposed as the Thread radio, and the mode
  question defaults to `thread` because of it. On a host with two sticks,
  which one is "first" depends on enumeration order at boot.
- **The mode question asks for a word, not a choice.**
  `Operating mode - 'thread' for Thread and WiFi, 'wifi' for WiFi and
  Ethernet only [thread]:` names neither the stick it would use nor what
  the alternative costs.
- **Two questions nobody can answer.** `Thread radio baud rate [460800]`
  and `Bluetooth adapter id for BLE commissioning [0]` ask for values a
  user has no way to look up, and the right answer is the default in
  practically every installation.
- **The Miniserver question has no help and no check.**
  `IPv4 address of the Loxone Miniserver:` accepts any well-formed address.
  A typo shows up hours later as a bridge that sends nothing.

## 2. Agreed Decisions

Decided with the maintainer while designing this (binding):

| Question | Decision |
|---|---|
| How far the installer goes | Numbered menus in the installer, not a slimmer installer that defers everything to the Radios card |
| Which radios the menus cover | Thread stick and Bluetooth adapter. The Zigbee stick stays on the Radios card; the installer only says so |
| Miniserver help | Explain where to find the address, then check it over HTTP. No network discovery |

**Rejected, with reasons.**

- *Installer asks only for the Miniserver, radios on the Radios card only.*
  Less code, one place for radio choices - but a Thread user would finish
  the install with a stack that has no border router and a second step to
  learn about. The Radios card remains the place to *change* the choice.
- *Assigning roles (Thread / Zigbee / unused) to every stick in one menu.*
  Would configure Zigbee at install time too. Not wanted for now; Zigbee
  setup stays with its commissioning flow in the web UI.
- *Miniserver discovery by UDP broadcast to port 7070.* Needs a tool the
  installer does not otherwise depend on (`socat` or `python3`), only works
  inside one broadcast domain, and saves typing one address.
- *`whiptail`/`dialog` menus.* Not installed on every Debian host, hard to
  drive from the test harness, and fragile when stdin is the `curl` pipe.
  Plain numbered prompts on `/dev/tty` are what `ask()` already does.
- *A Python helper reusing `radios/inventory.py`.* The radio decision runs
  in phase one, before the checkout exists, and the host is not required
  to have `python3`.

## 3. Principles

1. **Show what is there, let the user choose.** The installer lists what it
   found and proposes a default. It never picks silently between two
   candidates.
2. **No label the installer cannot back up.** A stick's firmware is not
   visible from its name: the maintainer's own Thread stick is a SONOFF
   Dongle Plus MG24, a name `radios/fingerprints.py` knows as a Zigbee
   coordinator. The menu shows the `by-id` name and nothing that claims
   "Thread" or "Zigbee".
3. **Ask only what a user can answer.** A value with a default that fits
   practically every installation is written and printed, not asked. The
   environment variable of the same name still overrides it.
4. **The non-interactive path does not change.** Without a terminal, every
   value comes from the environment or the run stops with an explanation,
   exactly as today. None of the new checks turn into a new abort.
5. **Installer text is English.** `install.sh` runs before the bridge and
   its i18n exist; its output has always been English. The mock-ups below
   are the text to ship.

## 4. Flow

**All questions are asked in phase one, before anything is installed.**
Today the mode is asked in phase one, but the backbone, the Bluetooth
adapter and the Miniserver are asked in phase four - after the package and
Docker installation, which takes minutes on a Pi. Asking everything up
front lets the user answer once and walk away. Phase four only writes what
phase one decided. This works because everything the questions need is
there before the install: `/dev/serial/by-id` and `/sys/class/bluetooth`
are the host's, `ip` is checked in `collect_missing`, and `curl` is the
tool the one-liner was fetched with. When `curl` is missing anyway (the
script was copied over by hand), the Miniserver check is skipped with one
line saying so; `curl` is installed in phase two and the health check
needs it, but a check that could only run after the questions are over
has nothing to offer the user.

The questions are announced once, before the first one:

```
Three questions follow: the Thread stick, the Bluetooth adapter, and the
address of your Loxone Miniserver.
```

A run that finds an existing `.env` (section 4.4) says which of them it
skips instead.

### 4.1 Thread stick (replaces `decide_mode`'s question)

```
Thread stick
  Thread devices need a USB stick running OpenThread RCP firmware.
  A Zigbee stick does not belong here - set that up on the Radios card
  of the web interface after the installation.

    1) usb-ITead_Sonoff_Zigbee_3.0_USB_Dongle_Plus_V2_...-if00-port0
    2) usb-SONOFF_SONOFF_Dongle_Plus_MG24_...-if00-port0
    0) None - WiFi and Ethernet only (Thread can be switched on later
       on the Radios card)

  Which one is the Thread stick? [0]:
```

- **Candidates** are the entries of `/dev/serial/by-id`, sorted. They are
  readable, and they stay the same when `ttyUSB0` and `ttyUSB1` swap at
  boot. When that directory does not exist or is empty, the list falls back
  to `/dev/ttyUSB*` and `/dev/ttyACM*`.
- **The value written** to `RADIO_DEVICE` is the full
  `/dev/serial/by-id/...` path. `docker-compose.yml` already maps
  `${RADIO_DEVICE}` onto the fixed `/dev/ttyThread` inside `otbr`, which
  is what makes a `by-id` value work there (see the comment above that
  line).
- **The default** is `1` when exactly one candidate exists, and `0` when
  there are none or several. `0` means mode `wifi`; any other number means
  mode `thread` with that stick.
- **The separate mode question goes away.** `LOXMATTER_MODE` still sets
  the mode without a question, and `RADIO_DEVICE` still names the stick
  without one.
- **An invalid answer** (not a number, out of range) repeats the question
  with one line saying why. It never aborts.
- **No candidates at all** skips the menu and prints one line: no USB
  stick found, installing for WiFi and Ethernet, Thread can be switched on
  later on the Radios card.

**Baud rate.** `RADIO_BAUDRATE` is no longer asked. `460800` is written,
which is what the bundled RCP image expects, and printed with the note
that `RADIO_BAUDRATE=...` overrides it.

**Backbone interface.** `BACKBONE_IF` is asked only when
`detect_backbone_if` returns nothing. Otherwise the detected value is
written and printed with one line: the border router reaches the rest of
the network over this interface.

### 4.2 Bluetooth adapter (replaces the adapter-id question)

Adapters are read from `/sys/class/bluetooth/hci*`, classified the same way
`radios/inventory.py`'s `scan_bluetooth` does it: a `device` link whose
target contains `/usb` is a USB adapter, named by the `product` file of its
USB device; one containing `serial` is built in (UART). Measured on the
Pi 3B: `hci0 -> .../3f201000.serial/.../serial0/serial0-0/bluetooth/hci0`.

- **Exactly one adapter:** no question, one line:
  `Bluetooth: hci0 - built in (UART), used to commission Matter devices.`
- **Several adapters:** a menu in the style of 4.1, the lowest index
  proposed:

  ```
  Bluetooth adapter
    Most Matter devices are commissioned over Bluetooth. The built-in
    adapter is usually enough; a USB adapter reaches further.

      1) hci0 - built in (UART)
      2) hci1 - USB: <product>

    Which adapter should be used? [1]:
  ```

  The number written to `BLUETOOTH_ADAPTER` is the `hciN` index, not the
  menu position.
- **No adapter:** no question, but a warning: new Matter devices can then
  only be commissioned if they are already on the network (for example
  through the manufacturer's app); an adapter can be chosen later on the
  Radios card. `BLUETOOTH_ADAPTER` is written as `0`, as today.
- **A soft-blocked adapter** is still offered. `check_rfkill` reports it
  as a finding, unchanged.

### 4.3 Miniserver (replaces `ask_miniserver`'s prompt)

```
Loxone Miniserver
  loxmatter signs in to the Miniserver and creates the devices there.
  You find its address in Loxone Config under the Miniserver's
  properties, or in the Loxone app under Settings -> Miniserver.

  IPv4 address of the Miniserver: 10.0.1.99
  Checking 10.0.1.99 ... Miniserver found (serial 50:4F:94:xx:xx:xx,
  firmware 15.3.x)
```

- **Order:** `valid_ipv4` first, unchanged. Only a well-formed address is
  checked over the network.
- **The check** is `curl -fsS -m 3 http://<address>/jdev/cfg/api`, which a
  Miniserver answers without authentication with a JSON body carrying
  `snr` and `version`. Both are pulled out with `sed`. A body that answers
  but carries neither counts as "not a Miniserver".
- **When the check fails** (timeout, refused, HTTP error, foreign body):

  ```
  No Miniserver answers at 10.0.1.99 (timeout).
    1) Enter a different address
    2) Use it anyway - the Miniserver is not reachable right now
  Choice [1]:
  ```

  "Use it anyway" writes the address and adds a finding to the final
  report, naming the address and pointing to the web interface, where the
  address can be changed.
- **Without a terminal** the check still runs, but a failure only adds the
  same finding. The Miniserver may be switched off during an installation;
  that must not stop it.
- **In a dry run** the check is printed as `would check`, not run.

### 4.4 A second run

A run that finds an existing `.env` keeps every value it already holds, as
today, and asks nothing about it. Since the questions now come before
the checkout step, phase one reads `$TARGET_DIR/deploy/testhost/.env`
itself (through `env_file_value`) to know which questions to skip: with a
`COMPOSE_PROFILES` line the Thread menu is skipped with one line - mode
kept, change the Thread stick on the Radios card - and with
`BLUETOOTH_ADAPTER` or `MINISERVER_IP` set, those questions are skipped
too. A skipped Miniserver question is not checked over the network either. Today the mode question
is asked and its answer then overruled by the file with a warning
(`configure_mode`), which asks a question whose answer is thrown away.

## 5. How the Questions Are Asked

`ask()` stays the single place that talks to the terminal. Two additions:

- **`choose()`** prints a numbered list plus an optional `0)` line, asks
  through `ask()`, repeats on an invalid answer, and echoes the chosen
  number. Without a terminal it echoes the default, like `ask()`.
- **One file descriptor for the terminal.** `check_tty` opens the terminal
  once as descriptor 3, and `ask()` reads from it. The terminal path comes
  from `LOXMATTER_TTY`, default `/dev/tty`. When `LOXMATTER_TTY` is set,
  prompts go to stderr instead of back into that path. This exists for the
  tests (section 7) and is not documented in `--help`. Reading from one
  descriptor that stays open is what makes a file of answers work: `ask()`
  runs inside `$(...)`, the subshell shares the descriptor's offset with
  its parent, so each call continues where the last one stopped.

## 6. Files

| File | Change |
|---|---|
| `install.sh` | `list_serial_candidates`, `list_bt_adapters`, `choose`, `check_miniserver`; a new phase-one `ask_questions` holding every question, `configure` only writes its answers; `decide_mode`, `ask_miniserver`, `check_config_source`, `check_tty`, `ask` changed; `SERIAL_BY_ID_DIR`, `SERIAL_DEV_DIR`, `BT_SYS_DIR` overridable like `RFKILL_DIR` |
| `tests/test_install_script.py` | Fabricated `by-id`, `/dev` and `sysfs` directories; `curl` stub answers `/jdev/cfg/api`; answer files through `LOXMATTER_TTY` |
| `deploy/testhost/README.md` | The installer section describes the new questions |
| `README.md` | Only if its quick start quotes the old prompts |

## 7. Test Strategy

The existing harness (one-liner design, section 9) stays: sealed `PATH`,
stubs, no controlling terminal. New:

- **Fabricated hardware.** `SERIAL_BY_ID_DIR`, `SERIAL_DEV_DIR` and
  `BT_SYS_DIR` point at directories the test builds: `by-id` symlinks,
  `ttyUSB*` files, `hci*` entries whose `device` link resolves to a path
  containing `serial` or `/usb`, with a `product` file.
- **Answers.** `LOXMATTER_TTY` points at a file holding one answer per
  line. A test that wants the interactive branch writes the answers, a
  test for the non-interactive branch leaves it unset.
- **`curl` stub.** Answers `*/jdev/cfg/api` with a Miniserver body by
  default; a test replaces that branch with a timeout (`exit 28`) or a
  foreign body.

Cases, at least:

| Case | Expected |
|---|---|
| Two sticks, answer `2` | `RADIO_DEVICE` is the second `by-id` path, `COMPOSE_PROFILES=thread` |
| Two sticks, empty answer | Mode `wifi`, no `RADIO_DEVICE` question |
| One stick, empty answer | That stick, mode `thread` |
| No `by-id` directory, one `ttyUSB0` | Offered as `/dev/ttyUSB0` |
| Answer `7` then `1` | Repeats once, then takes `1` |
| `RADIO_BAUDRATE` | Never asked; `460800` written; env override wins |
| Backbone detected / not detected | Not asked / asked |
| One UART adapter | No question, line names it built in |
| USB adapter at `hci1` plus `hci0`, answer `2` | `BLUETOOTH_ADAPTER=1` |
| No adapter | Warning, `BLUETOOTH_ADAPTER=0` |
| Miniserver answers | Serial and firmware printed, no finding |
| Timeout, answer `2` | Address written, finding in the report |
| Timeout, answer `1`, then a working address | Second address written |
| Timeout, no terminal | Address written, finding, exit status of a normal run |
| Existing `.env` with `COMPOSE_PROFILES` | No Thread menu, no mode warning |
| Dry run | No `curl` to `/jdev/cfg/api`, `would check` printed |
| All questions answered | Every question appears in the output before the first `apt-get` or `get.docker.com` call |
| No `curl` on the host | Miniserver check skipped with a note, address written |

## 8. Boundaries

- No Zigbee question, and no `ZIGBEE_*` value written by the installer.
- No stick type detection, no probing of any serial port (see the reasons
  at the top of `radios/fingerprints.py`: opening a tty can reset a stick).
- No Miniserver credentials. The installer checks reachability; signing in
  is the bridge's job, in the web interface.
- No change to what an existing `.env` keeps.

## 9. Open Points and Risks

- **`/jdev/cfg/api` without authentication** is what this design rests
  on. It must be verified against a real Miniserver before implementation;
  if it turns out to need credentials, the check falls back to "something
  answers HTTP on port 80", and the success line says only that.
- **Firmware versions.** The shape of the `value` field inside the JSON
  answer (single quotes inside a string) should be captured from a real
  Miniserver as a fixture, not written from memory.
- **`read` on a shared descriptor.** dash reads byte by byte; bash reads a
  regular file in blocks and seeks back. Both keep the offset right, but
  the tests run under `/bin/sh` only - on macOS that is bash in POSIX
  mode, in CI dash. That is the combination to keep green.
