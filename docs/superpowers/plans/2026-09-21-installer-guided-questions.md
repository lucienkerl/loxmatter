# Installer Guided Questions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the installer's bare prompts for the Thread stick, the Bluetooth adapter and the Miniserver address with numbered menus built from what the host actually has, a Miniserver address that is checked before it is written, and all questions asked before anything is installed.

**Architecture:** Everything stays in POSIX `sh` inside `install.sh`. A new phase-one function `ask_questions` runs every question and leaves its answers in `CHOSEN_*` shell variables; phase four's `configure` only writes them into `.env` through a new `write_env_value`. Questions read from one terminal descriptor opened once, which the tests replace with a file of answers through `LOXMATTER_TTY`. Host hardware is read from `SERIAL_BY_ID_DIR`, `SERIAL_DEV_DIR` and `BT_SYS_DIR`, overridable the way `RFKILL_DIR` already is.

**Tech Stack:** POSIX sh (dash on Raspberry Pi OS and in CI, bash-as-sh on macOS), pytest with the stub harness in `tests/test_install_script.py`.

**Spec:** `docs/superpowers/specs/2026-09-21-installer-guided-questions-design.md`

## Global Constraints

- Everything written in this repository is English: code, comments, test names, commit messages (`CLAUDE.md`). The installer's user-facing text is English too - it runs before the bridge's i18n exists (spec section 3, principle 5).
- `install.sh` is POSIX sh, not bash: no `[[`, no arrays, no `local`, no `readlink -f`, no process substitution. The one-liner ends in `| sh`.
- Only the tools in `SYSTEM_TOOLS` of `tests/test_install_script.py` may be used by the script (sh, cat, grep, sed, awk, tr, od, mkdir, rm, mv, sleep, chmod, cp, printf, true, false, env, tail, head, mktemp) plus shell builtins (`cd`, `pwd -P`, `read`, `case`, `test`). Adding a tool means adding it to that tuple, and it must exist on a minimal Debian.
- The non-interactive path does not change: without a terminal every value comes from the environment or the run stops with an explanation. No new check may turn into a new abort (spec principle 4).
- The menu never labels a stick "Thread" or "Zigbee" (spec principle 2). No serial port is ever opened.
- `RADIO_BAUDRATE` default is exactly `460800`.
- The Miniserver check is exactly `curl -fsS -m 3 http://<address>/jdev/cfg/api`.
- Commit messages: Conventional Commits, `feat(install): ...`, `test(install): ...`, `docs(...): ...`, each ending with the line `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- Test runs happen in the foreground. Never use `run_in_background`, never use `Monitor`, never end a turn waiting for a notification. `tests/test_install_script.py` alone takes about 60 s.
- Work happens on the branch `claude/installer-sudo-hints` (it already carries the sudo-hint fix and the spec). Do not push, do not merge.

## File Structure

| File | Responsibility | Tasks |
|---|---|---|
| `install.sh` | The installer. Gains the terminal plumbing, `choose`, the hardware listings, the four `decide_*` functions, `check_miniserver`, `announce_questions`, `write_env_value`; loses `detect_radio_device`, `detect_bt_adapter`, `ensure_env_value`, `ask_miniserver`, `DETECTED_RADIO` | 2-7 |
| `tests/test_install_script.py` | Harness gains `answers=`, default env for the new directories, a Miniserver branch in the `curl` stub; new tests per task; three existing tests change | 2-7 |
| `tests/fixtures/miniserver/jdev_cfg_api.json` | The body a real Miniserver returns for `/jdev/cfg/api`, captured, not written by hand | 1 |
| `README.md` | Quick start describes the questions | 7 |
| `CHANGELOG.md` | `[Unreleased]` entries for the sudo hint and the guided questions | 7 |

`deploy/testhost/README.md` and `docs/SETUP.md` were checked: neither quotes the installer's prompts, so neither changes (the spec's file table named the testhost README as a maybe).

---

### Task 1: Capture the Miniserver's `/jdev/cfg/api` answer

The spec rests on `/jdev/cfg/api` answering without authentication (spec section 9). Nothing in this plan's parser may be written from memory; it is written against this capture.

**Files:**
- Create: `tests/fixtures/miniserver/jdev_cfg_api.json`

**Interfaces:**
- Produces: the fixture file, read by the test harness from Task 6 on as `MINISERVER_API_FIXTURE`.

- [ ] **Step 1: Find the Miniserver's address**

The test Pi's `.env` names it:

```bash
ssh -o ConnectTimeout=10 pi@10.0.1.56 'grep ^MINISERVER_IP= ~/loxmatter/deploy/testhost/.env'
```

Expected: `MINISERVER_IP=<address>`. If the test Pi is not reachable (it was not on 21 September 2026 from the maintainer's Mac: `No route to host`), STOP and ask Lucien for the Miniserver's address and a host on its network. Do not continue with an invented body.

- [ ] **Step 2: Capture the answer without credentials**

Replace `<address>` with the value from step 1:

```bash
ssh pi@10.0.1.56 'curl -sS -m 3 -w "\nHTTP %{http_code}\n" http://<address>/jdev/cfg/api'
```

Expected: HTTP 200 and a JSON body containing `snr` and `version`, in the documented shape `{"LL": { "control": "dev/cfg/api", "value": "{'snr': '50:4F:94:..', 'version':'15.x.x.x', ...}", "Code": "200"}}`.

If the answer is HTTP 401, STOP and report it: the spec's fallback (section 9, "something answers HTTP on port 80") then applies, and Task 6's parser and messages change. Ask Lucien before going on.

- [ ] **Step 3: Store the body exactly as received**

```bash
mkdir -p tests/fixtures/miniserver
ssh pi@10.0.1.56 'curl -sS -m 3 http://<address>/jdev/cfg/api' > tests/fixtures/miniserver/jdev_cfg_api.json
cat tests/fixtures/miniserver/jdev_cfg_api.json
```

Check by eye that it holds `'snr': '` and `'version':` (with or without a space after the colon). Note in the commit message which of the two spellings the capture uses and the firmware version it reports.

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/miniserver/jdev_cfg_api.json
git commit -m "test(install): capture a Miniserver's /jdev/cfg/api answer

Taken without credentials from the Miniserver on the test network, firmware
<version from the capture>. The installer's address check (design
2026-09-21-installer-guided-questions) parses this shape.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

`scripts/check_language.py` exempts `tests/fixtures/` as captured data, so the file needs no review for language.

---

### Task 2: One terminal descriptor, and answers from a file

**Files:**
- Modify: `install.sh` (globals near line 58, `check_tty` at 174-182, `ask` at 184-204, `ensure_env_value` at 594-619, `ask_miniserver` at 621-638)
- Test: `tests/test_install_script.py` (fixture `installer` at 220-326, new section at the end of the file)

**Interfaces:**
- Produces: `ask PROMPT DEFAULT` - unchanged call shape; echoes the answer; now **returns 1 when the terminal gives no more input** (EOF), instead of echoing the default. Every caller must handle that with `|| die "..."`.
- Produces: `TTY_PATH` global; descriptor 3 open on it when `HAVE_TTY=1`.
- Produces (tests): `installer(..., answers=[...])` - writes one answer per line to a file and sets `LOXMATTER_TTY` to it. `answers=[]` means "a terminal that answers nothing": any question aborts the run.

- [ ] **Step 1: Give the harness an `answers` parameter**

In `tests/test_install_script.py`, change `build_env`, `run` and `start` inside the `installer` fixture:

```python
    def build_env(env, omit, stubs, answers):
```

and, directly before `full_env.update(env or {})` at the end of `build_env`:

```python
        # Every run has no controlling terminal (start_new_session=True), so
        # every question takes the non-interactive branch - except when a
        # test hands in answers. LOXMATTER_TTY points the installer at a
        # file instead of /dev/tty; one answer per line, an empty string
        # takes the default. An empty list is a terminal that answers
        # nothing, so any question at all aborts the run.
        if answers is not None:
            answer_file = tmp_path / "answers"
            answer_file.write_text("".join(f"{answer}\n" for answer in answers))
            full_env["LOXMATTER_TTY"] = str(answer_file)
```

```python
    def run(*args, env=None, omit=(), stubs=None, answers=None):
        full_env = build_env(env, omit, stubs, answers)
```

```python
    def start(*args, env=None, omit=(), stubs=None):
        full_env = build_env(env, omit, stubs, None)
```

- [ ] **Step 2: Write the failing tests**

Append to the end of `tests/test_install_script.py`:

```python
# ------------------------------------------------------------ questions --


def test_answers_are_read_one_after_another(installer):
    # ask() runs inside $(...). Reopening the terminal on every call would
    # read the first line of an answers file again and again; one
    # descriptor opened once is what makes the second answer arrive.
    result = installer(
        env={"LOXMATTER_MODE": "wifi", "BLUETOOTH_ADAPTER": "0", "MINISERVER_IP": ""},
        answers=["not-an-ip", "10.0.1.42"],
    )
    assert result.returncode == 0
    assert _env(result)["MINISERVER_IP"] == "10.0.1.42"


def test_running_out_of_answers_aborts_instead_of_looping(installer):
    # A question that rejects its own default (the Miniserver address has
    # none) used to spin forever on a closed terminal: read failed, the
    # empty default came back, was rejected, and was asked again.
    result = installer(
        env={"LOXMATTER_MODE": "wifi", "BLUETOOTH_ADAPTER": "0", "MINISERVER_IP": ""},
        answers=["not-an-ip"],
    )
    assert result.returncode == 2
    assert "terminal closed" in result.output
```

- [ ] **Step 3: Run them to see them fail**

Run: `uv run pytest tests/test_install_script.py -k "answers" -v`

Expected: `test_answers_are_read_one_after_another` FAILS with return code 2 and `MINISERVER_IP is not set and there is no terminal to ask on` in the output (the script still opens `/dev/tty`). `test_running_out_of_answers_aborts_instead_of_looping` FAILS the same way.

- [ ] **Step 4: Implement the descriptor and the new `ask`**

In `install.sh`, after the `RFKILL_DIR=` line (line 59), add:

```sh
# Where questions are read from. LOXMATTER_TTY exists for the tests only:
# they point it at a file of answers, one per line. Not in --help on purpose.
TTY_PATH="${LOXMATTER_TTY:-/dev/tty}"
```

Replace `check_tty` (lines 174-182) with:

```sh
check_tty() {
  if ( exec <"$TTY_PATH" ) 2>/dev/null; then
    HAVE_TTY=1
    # Opened once and kept open: ask() runs inside $(...), and a subshell
    # shares this descriptor's offset with its parent - so a file of answers
    # is read line after line, instead of its first line on every question.
    exec 3<"$TTY_PATH"
  else
    HAVE_TTY=0
    note "No terminal available; every value has to come from the environment."
  fi
}

# Prompts go to the terminal the answers come from - except in the tests,
# where that is a file of answers that must not be written to.
tty_prompt() {
  if [ -n "${LOXMATTER_TTY:-}" ]; then
    printf '%s' "$1" >&2
  else
    printf '%s' "$1" >/dev/tty
  fi
}
```

Keep the comment block above `check_tty` ("stdin is the pipe when this runs as ...") as it is.

Replace `ask` (lines 184-204, including its comment) with:

```sh
# Asks on the terminal and echoes the answer. Without a terminal, or in a dry
# run, it echoes the default and asks nothing. Returns 1 when the terminal
# gives no more input: echoing the default there would let a caller that
# rejects the default ask again forever.
ask() {
  ask_prompt="$1"
  ask_default="$2"
  if [ "$HAVE_TTY" -eq 0 ] || [ "$DRY_RUN" -eq 1 ]; then
    printf '%s' "$ask_default"
    return 0
  fi
  if [ -n "$ask_default" ]; then
    tty_prompt "$ask_prompt [$ask_default]: "
  else
    tty_prompt "$ask_prompt: "
  fi
  if ! read -r ask_answer <&3; then
    return 1
  fi
  if [ -z "$ask_answer" ]; then
    ask_answer="$ask_default"
  fi
  printf '%s' "$ask_answer"
}
```

- [ ] **Step 5: Make both existing callers handle the closed terminal**

In `ensure_env_value`, replace

```sh
    value_new="$(ask "$value_prompt" "$value_default")"
```

with

```sh
    value_new="$(ask "$value_prompt" "$value_default")" ||
      die "The terminal closed before $value_key was given.$(config_written_note)"
```

In `ask_miniserver`, replace

```sh
    ms_value="$(ask "IPv4 address of the Loxone Miniserver" "")"
```

with

```sh
    ms_value="$(ask "IPv4 address of the Loxone Miniserver" "")" ||
      die "The terminal closed before the Miniserver's address was given.$(config_written_note)"
```

- [ ] **Step 6: Run the new tests, then the whole file**

Run: `uv run pytest tests/test_install_script.py -k "answers" -v`
Expected: both PASS.

Run: `uv run pytest tests/test_install_script.py -q`
Expected: all pass (66 passed, 2 skipped, or more).

- [ ] **Step 7: Commit**

```bash
git add install.sh tests/test_install_script.py
git commit -m "feat(install): read every answer from one terminal descriptor

The terminal is opened once as descriptor 3 instead of on every question,
and a closed terminal now aborts instead of handing back a default the
Miniserver question rejects forever. LOXMATTER_TTY lets the tests feed a
file of answers - the first time the interactive branch can be tested.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: The Thread stick menu

**Files:**
- Modify: `install.sh` (globals; `detect_radio_device` and `decide_mode` at 264-292; `check_config_source` at 340-362; `configure_mode` at 672-705; `configure` at 707-756; `main` at 990-1020)
- Test: `tests/test_install_script.py`

**Interfaces:**
- Consumes: `ask` (Task 2), `answers=` (Task 2).
- Produces: `choose PROMPT DEFAULT MAX ALLOW_ZERO` - asks for a number between 1 and MAX (0 too when ALLOW_ZERO is 1), repeats on anything else, leaves the number in the global `CHOICE`. Must be called directly, never inside `$(...)`, or `CHOICE` is lost.
- Produces: `count_lines TEXT` (echoes the number of non-empty lines), `nth_line TEXT N` (echoes line N).
- Produces: `write_env_value KEY VALUE REQUIRED` - writes VALUE into `.env` unless an existing `.env` already holds a non-empty value for KEY (then prints `KEY=... (kept)`); with REQUIRED=1 an empty VALUE aborts.
- Produces: `ask_questions` - the phase-one function all later `decide_*` functions are added to.
- Produces: globals `CHOSEN_RADIO`, `SERIAL_CANDIDATES`, `SERIAL_BY_ID_DIR`, `SERIAL_DEV_DIR`.

- [ ] **Step 1: Point the harness at empty hardware by default**

In `build_env`'s `full_env` dict, after the `RFKILL_DIR` entry, add:

```python
            # The installer lists USB sticks and Bluetooth adapters from
            # these. Pointing them nowhere by default keeps every test
            # independent of what the machine running pytest has plugged
            # in; tests about the menus build a directory and override them.
            "SERIAL_BY_ID_DIR": str(tmp_path / "no-by-id-here"),
            "SERIAL_DEV_DIR": str(tmp_path / "no-dev-here"),
            "BT_SYS_DIR": str(tmp_path / "no-bluetooth-here"),
```

Add these helpers directly above the `# ---- questions --` section added in Task 2:

```python
STICK_A = "usb-ITead_Sonoff_Zigbee_3.0_USB_Dongle_Plus_V2_1a2b3c-if00-port0"
STICK_B = "usb-SONOFF_SONOFF_Dongle_Plus_MG24_d86e1106-if00-port0"

# Keeps the backbone and Bluetooth questions out of a test about the Thread
# stick. Both are skipped when their environment variable is set.
_ONLY_THE_STICK = {"BACKBONE_IF": "eth0", "BLUETOOTH_ADAPTER": "0"}


def _serial(tmp_path, *names):
    """Fabricates /dev with one ttyUSB node per name and a serial/by-id link
    to each, and returns the env that points the installer at it."""
    dev = tmp_path / "hw" / "dev"
    by_id = dev / "serial" / "by-id"
    by_id.mkdir(parents=True)
    for index, name in enumerate(names):
        node = dev / f"ttyUSB{index}"
        node.write_text("")
        (by_id / name).symlink_to(node)
    return {"SERIAL_BY_ID_DIR": str(by_id), "SERIAL_DEV_DIR": str(dev)}
```

- [ ] **Step 2: Write the failing tests**

Append to the questions section:

```python
def test_two_sticks_are_offered_by_their_by_id_names(installer, tmp_path):
    hw = _serial(tmp_path, STICK_A, STICK_B)
    result = installer(env={**hw, **_ONLY_THE_STICK}, answers=["2"])
    assert result.returncode == 0
    assert f"1) {STICK_A}" in result.output
    assert f"2) {STICK_B}" in result.output
    assert "A Zigbee stick does not belong here" in result.output
    values = _env(result)
    assert values["COMPOSE_PROFILES"] == "thread"
    assert values["RADIO_DEVICE"] == f"{hw['SERIAL_BY_ID_DIR']}/{STICK_B}"


def test_with_two_sticks_the_default_is_none(installer, tmp_path):
    # Two candidates and no way to tell them apart by name: the default
    # must never be a silent pick of one of them.
    hw = _serial(tmp_path, STICK_A, STICK_B)
    result = installer(env={**hw, **_ONLY_THE_STICK}, answers=[""])
    assert result.returncode == 0
    assert "Which one is the Thread stick? [0]" in result.output
    assert "Operating mode: wifi" in result.output
    assert _env(result)["COMPOSE_PROFILES"] == ""


def test_with_one_stick_the_default_is_that_stick(installer, tmp_path):
    hw = _serial(tmp_path, STICK_B)
    result = installer(env={**hw, **_ONLY_THE_STICK}, answers=[""])
    assert result.returncode == 0
    assert "Which one is the Thread stick? [1]" in result.output
    assert _env(result)["RADIO_DEVICE"] == f"{hw['SERIAL_BY_ID_DIR']}/{STICK_B}"


def test_without_by_id_the_tty_nodes_are_offered(installer, tmp_path):
    dev = tmp_path / "hw" / "dev"
    dev.mkdir(parents=True)
    (dev / "ttyUSB0").write_text("")
    env = {
        "SERIAL_BY_ID_DIR": str(dev / "serial" / "by-id"),
        "SERIAL_DEV_DIR": str(dev),
        **_ONLY_THE_STICK,
    }
    result = installer(env=env, answers=["1"])
    assert result.returncode == 0
    assert f"1) {dev}/ttyUSB0" in result.output
    assert _env(result)["RADIO_DEVICE"] == f"{dev}/ttyUSB0"


def test_a_dangling_by_id_link_is_not_offered(installer, tmp_path):
    # A stick pulled after boot can leave its by-id link behind for a moment.
    hw = _serial(tmp_path, STICK_B)
    (Path(hw["SERIAL_BY_ID_DIR"]) / "usb-Gone_Stick-if00-port0").symlink_to(
        tmp_path / "hw" / "dev" / "ttyUSB9"
    )
    result = installer(env={**hw, **_ONLY_THE_STICK}, answers=[""])
    assert result.returncode == 0
    assert "usb-Gone_Stick" not in result.output
    assert "Which one is the Thread stick? [1]" in result.output


def test_an_invalid_answer_is_asked_again(installer, tmp_path):
    hw = _serial(tmp_path, STICK_A, STICK_B)
    result = installer(env={**hw, **_ONLY_THE_STICK}, answers=["7", "1"])
    assert result.returncode == 0
    assert "one of the numbers shown" in result.output
    assert _env(result)["RADIO_DEVICE"] == f"{hw['SERIAL_BY_ID_DIR']}/{STICK_A}"


def test_the_baud_rate_is_not_asked(installer, tmp_path):
    # answers=["1"] and nothing more: a baud rate question would hit the
    # end of the answers and abort the run.
    hw = _serial(tmp_path, STICK_B)
    result = installer(env={**hw, **_ONLY_THE_STICK}, answers=["1"])
    assert result.returncode == 0
    assert _env(result)["RADIO_BAUDRATE"] == "460800"


def test_the_baud_rate_from_the_environment_wins(installer, tmp_path):
    hw = _serial(tmp_path, STICK_B)
    env = {**hw, **_ONLY_THE_STICK, "RADIO_BAUDRATE": "115200"}
    result = installer(env=env, answers=["1"])
    assert result.returncode == 0
    assert _env(result)["RADIO_BAUDRATE"] == "115200"


def test_without_a_terminal_a_single_stick_is_taken(installer, tmp_path):
    hw = _serial(tmp_path, STICK_B)
    result = installer(env={**hw, **_ONLY_THE_STICK})
    assert result.returncode == 0
    assert "Taking 1" in result.output
    assert _env(result)["RADIO_DEVICE"] == f"{hw['SERIAL_BY_ID_DIR']}/{STICK_B}"


def test_without_a_terminal_two_sticks_mean_wifi(installer, tmp_path):
    hw = _serial(tmp_path, STICK_A, STICK_B)
    result = installer(env={**hw, **_ONLY_THE_STICK})
    assert result.returncode == 0
    assert _env(result)["COMPOSE_PROFILES"] == ""


def test_a_radio_device_from_the_environment_means_thread(installer, tmp_path):
    hw = _serial(tmp_path, STICK_A, STICK_B)
    env = {**hw, **_ONLY_THE_STICK, "RADIO_DEVICE": "/dev/ttyUSB3"}
    result = installer(env=env, answers=[])
    assert result.returncode == 0
    assert "Which one is the Thread stick" not in result.output
    values = _env(result)
    assert values["COMPOSE_PROFILES"] == "thread"
    assert values["RADIO_DEVICE"] == "/dev/ttyUSB3"


def test_thread_mode_from_the_environment_offers_no_none(installer, tmp_path):
    hw = _serial(tmp_path, STICK_A, STICK_B)
    env = {**hw, **_ONLY_THE_STICK, "LOXMATTER_MODE": "thread"}
    result = installer(env=env, answers=["0", "2"])
    assert result.returncode == 0
    assert "None - WiFi and Ethernet only" not in result.output
    assert _env(result)["RADIO_DEVICE"] == f"{hw['SERIAL_BY_ID_DIR']}/{STICK_B}"


def test_a_second_run_does_not_show_the_thread_menu(installer, tmp_path):
    # configure_mode lets the existing .env win anyway; asking first and
    # overruling the answer with a warning asked a question for nothing.
    hw = {**_serial(tmp_path, STICK_A, STICK_B), **_ONLY_THE_STICK}
    first = installer(env=hw, answers=["2"])
    assert first.returncode == 0
    second = installer(env=hw, answers=[])
    assert second.returncode == 0
    assert "Which one is the Thread stick" not in second.output
    assert "kept from" in second.output
    assert "wins over the requested" not in second.output
    assert _env(second)["RADIO_DEVICE"] == _env(first)["RADIO_DEVICE"]
```

Also update the existing `test_thread_without_a_device_and_without_a_terminal_aborts` (around line 398): replace `assert "no radio" in result.output` with `assert "no USB stick" in result.output`.

- [ ] **Step 3: Run them to see them fail**

Run: `uv run pytest tests/test_install_script.py -k "stick or baud or radio_device or thread_mode or dangling or invalid_answer or tty_nodes or second_run_does_not_show or thread_without_a_device" -v`

Expected: the new tests FAIL (the menu does not exist; `Operating mode -` prompt appears instead). `test_thread_without_a_device_and_without_a_terminal_aborts` FAILS on `"no USB stick"`.

- [ ] **Step 4: Add the globals and helpers**

In `install.sh`, remove the line `DETECTED_RADIO=""` (line 46) and add after the `TTY_PATH=` line from Task 2:

```sh
# Overridable for the same reason as RFKILL_DIR: the tests point these at
# fabricated directories instead of the host's real /dev and /sys.
SERIAL_BY_ID_DIR="${SERIAL_BY_ID_DIR:-/dev/serial/by-id}"
SERIAL_DEV_DIR="${SERIAL_DEV_DIR:-/dev}"
SERIAL_CANDIDATES=""
CHOICE=""
# What phase one decided, for phase four to write. Empty means "nothing to
# write": either an existing .env keeps its value, or the mode does not use it.
CHOSEN_RADIO=""
```

Directly after `ask` (and `tty_prompt`), add:

```sh
# Asks for a number from a menu the caller has already printed, repeats on
# anything else, and leaves the number in CHOICE. Called directly, never in
# $(...) - a subshell would take CHOICE with it. $4 is 1 when 0 is a valid
# answer. At most two digits: a menu never has a hundred lines, and a longer
# number would reach `[ -le ]` as something the shell cannot compare.
choose() {
  choose_prompt="$1"
  choose_default="$2"
  choose_max="$3"
  choose_zero="$4"
  if [ "$HAVE_TTY" -eq 0 ] || [ "$DRY_RUN" -eq 1 ]; then
    note "Taking $choose_default - nothing is asked without a terminal or in a dry run."
  fi
  while :; do
    CHOICE="$(ask "$choose_prompt" "$choose_default")" ||
      die "The terminal closed before this was answered: $choose_prompt"
    case "$CHOICE" in
      [0-9]|[0-9][0-9])
        if [ "$CHOICE" -le "$choose_max" ]; then
          if [ "$CHOICE" -ge 1 ] || [ "$choose_zero" -eq 1 ]; then
            return 0
          fi
        fi
        ;;
    esac
    warn "Please answer with one of the numbers shown."
  done
}

count_lines() { printf '%s' "$1" | grep -c . || true; }

nth_line() { printf '%s' "$1" | sed -n "${2}p"; }
```

- [ ] **Step 5: Replace `detect_radio_device` and `decide_mode`**

Replace lines 264-292 (`detect_radio_device` and `decide_mode`) with:

```sh
# Sets SERIAL_CANDIDATES to the USB serial devices a Thread stick could be,
# one path per line. /dev/serial/by-id comes first: its names say what the
# stick is, and they survive a reboot that swaps ttyUSB0 and ttyUSB1 - the
# same reason the Radios card offers them. `-e` follows the link, so a
# by-id link left behind by a pulled stick is skipped.
list_serial_candidates() {
  SERIAL_CANDIDATES=""
  for candidate in "$SERIAL_BY_ID_DIR"/*; do
    if [ -e "$candidate" ]; then
      SERIAL_CANDIDATES="$SERIAL_CANDIDATES$candidate
"
    fi
  done
  if [ -n "$SERIAL_CANDIDATES" ]; then
    return 0
  fi
  for candidate in "$SERIAL_DEV_DIR"/ttyUSB* "$SERIAL_DEV_DIR"/ttyACM*; do
    if [ -e "$candidate" ]; then
      SERIAL_CANDIDATES="$SERIAL_CANDIDATES$candidate
"
    fi
  done
}

serial_label() {
  case "$1" in
    "$SERIAL_BY_ID_DIR"/*) printf '%s' "${1##*/}" ;;
    *) printf '%s' "$1" ;;
  esac
}

# No "Thread" or "Zigbee" label next to a stick: the firmware cannot be seen
# from the name. The maintainer's own Thread stick is a SONOFF Dongle Plus
# MG24 - a name radios/fingerprints.py knows as a Zigbee coordinator.
# $1 is 1 when "None" is offered; without it the default is the first stick.
show_thread_menu() {
  say "Thread stick"
  note "Thread devices need a USB stick running OpenThread RCP firmware."
  note "A Zigbee stick does not belong here - set that up on the Radios card"
  note "of the web interface after the installation."
  printf '\n'
  tm_count=0
  while IFS= read -r tm_path; do
    if [ -z "$tm_path" ]; then
      continue
    fi
    tm_count=$((tm_count + 1))
    note "  $tm_count) $(serial_label "$tm_path")"
  done <<EOF
$SERIAL_CANDIDATES
EOF
  tm_default=1
  if [ "$1" -eq 1 ]; then
    note "  0) None - WiFi and Ethernet only (Thread can be switched on later"
    note "     on the Radios card)"
    # Two sticks cannot be told apart by name, so the default never picks
    # one of them silently.
    if [ "$tm_count" -ne 1 ]; then
      tm_default=0
    fi
  fi
  printf '\n'
  choose "Which one is the Thread stick?" "$tm_default" "$tm_count" "$1"
}

decide_mode() {
  step "choosing the Thread stick"
  if [ -n "${LOXMATTER_MODE:-}" ]; then
    MODE="$LOXMATTER_MODE"
    case "$MODE" in
      thread|wifi) : ;;
      *) die "Operating mode must be 'thread' or 'wifi' (got: $MODE)" ;;
    esac
  elif [ -f "$TARGET_DIR/deploy/testhost/.env" ]; then
    # A second run keeps what is configured. configure_mode reads the mode
    # from COMPOSE_PROFILES, or from a running otbr container when that line
    # is missing, and would overrule an answer given here anyway.
    if [ -n "$(env_file_value COMPOSE_PROFILES)" ]; then
      MODE="thread"
    else
      MODE="wifi"
    fi
    note "Thread: kept from $TARGET_DIR/deploy/testhost/.env."
    note "Change the Thread stick on the Radios card of the web interface."
    note "Operating mode: $MODE"
    return 0
  elif [ -n "${RADIO_DEVICE:-}" ]; then
    MODE="thread"
  else
    list_serial_candidates
    if [ -z "$SERIAL_CANDIDATES" ]; then
      note "No USB stick found - installing for WiFi and Ethernet only."
      note "Thread can be switched on later on the Radios card."
      MODE="wifi"
    else
      show_thread_menu 1
      if [ "$CHOICE" -eq 0 ]; then
        MODE="wifi"
      else
        MODE="thread"
        CHOSEN_RADIO="$(nth_line "$SERIAL_CANDIDATES" "$CHOICE")"
      fi
    fi
  fi
  if [ "$MODE" = "thread" ] && [ -z "$CHOSEN_RADIO" ]; then
    if [ -n "${RADIO_DEVICE:-}" ]; then
      CHOSEN_RADIO="$RADIO_DEVICE"
    elif [ -z "$(env_file_value RADIO_DEVICE)" ]; then
      list_serial_candidates
      if [ -z "$SERIAL_CANDIDATES" ]; then
        die "Thread mode was requested, but no USB stick was found under
$SERIAL_BY_ID_DIR, $SERIAL_DEV_DIR/ttyUSB* or $SERIAL_DEV_DIR/ttyACM*.
Plug the stick in, pass RADIO_DEVICE=/dev/serial/by-id/..., or use
LOXMATTER_MODE=wifi."
      fi
      show_thread_menu 0
      CHOSEN_RADIO="$(nth_line "$SERIAL_CANDIDATES" "$CHOICE")"
    fi
  fi
  note "Operating mode: $MODE"
}

# Every question, asked before anything is installed: answering once and
# walking away beats being called back to the keyboard minutes later, after
# the package and Docker installation.
ask_questions() {
  decide_mode
}
```

`env_file_value` is defined further down (line 333); that is fine - shell functions are looked up when called, not when defined.

- [ ] **Step 6: Drop the Thread clause from `check_config_source`**

Delete the last `if` block of `check_config_source` (the one starting `if [ "$MODE" = "thread" ] && [ -z "${RADIO_DEVICE:-}" ]`, lines 355-361). `decide_mode` now stops that case itself, before anything is written.

- [ ] **Step 7: Warn about a conflicting mode only when one was requested**

In `configure_mode`, both places that read

```sh
    if [ "$MODE" != "$cm_requested_mode" ]; then
```

become

```sh
    if [ -n "${LOXMATTER_MODE:-}" ] && [ "$MODE" != "$cm_requested_mode" ]; then
```

and add this comment above the first of them:

```sh
    # Only LOXMATTER_MODE is a request. A mode decide_mode read out of this
    # same .env, or a provisional wifi for an old .env without the line, is
    # not something the user asked for and must not be "overruled" loudly.
```

- [ ] **Step 8: Add `write_env_value` and use it for the stick and the baud rate**

After `ensure_env_value`, add:

```sh
# Writes what phase one decided. A second run keeps whatever the existing
# .env already holds, like ensure_env_value always did. With $3 = 1 an empty
# value stops the run instead of writing a line otbr cannot start with.
write_env_value() {
  wv_key="$1"
  wv_value="$2"
  wv_required="$3"
  if [ "$ENV_IS_NEW" -eq 0 ]; then
    wv_kept="$(env_file_value "$wv_key")"
    if [ -n "$wv_kept" ]; then
      note "$wv_key=$wv_kept (kept)"
      return 0
    fi
  fi
  if [ -z "$wv_value" ] && [ "$wv_required" -eq 1 ]; then
    die "$wv_key needs a value and none could be obtained. Pass $wv_key=... to
this script.$(config_written_note)"
  fi
  env_set "$wv_key" "$wv_value"
  note "$wv_key=$wv_value"
}
```

In `configure`, replace

```sh
    ensure_env_value RADIO_DEVICE "Thread radio device" "$DETECTED_RADIO" 1
    ensure_env_value RADIO_BAUDRATE "Thread radio baud rate" "460800" 1
```

with

```sh
    write_env_value RADIO_DEVICE "$CHOSEN_RADIO" 1
    write_env_value RADIO_BAUDRATE "${RADIO_BAUDRATE:-460800}" 1
    if [ "$ENV_IS_NEW" -eq 1 ] && [ -z "${RADIO_BAUDRATE:-}" ]; then
      note "  (what the bundled border router image expects; set RADIO_BAUDRATE"
      note "  before running this script to use a different one)"
    fi
```

- [ ] **Step 9: Reorder `main`**

In `main`, replace

```sh
  decide_mode
  check_config_source
```

with

```sh
  check_config_source
  ask_questions
```

`check_config_source` no longer reads `MODE`, and it has to stop a run with neither a Miniserver address nor a terminal before any question is shown.

- [ ] **Step 10: Run the Thread tests, then the whole file**

Run: `uv run pytest tests/test_install_script.py -k "stick or baud or radio_device or thread_mode or dangling or invalid_answer or tty_nodes or second_run_does_not_show or thread_without_a_device" -v`
Expected: all PASS.

Run: `uv run pytest tests/test_install_script.py -q`
Expected: all pass. `test_a_conflicting_mode_is_reported_loudly` still passes: it sets `LOXMATTER_MODE=thread` against an existing wifi `.env`.

- [ ] **Step 11: Commit**

```bash
git add install.sh tests/test_install_script.py
git commit -m "feat(install): choose the Thread stick from a numbered menu

The installer used to take the first /dev/ttyUSB* as the Thread radio -
a Zigbee coordinator, on a host that has one - and asked for the mode as a
word. It now lists /dev/serial/by-id, proposes none when two sticks cannot
be told apart, stops asking for the baud rate, and leaves the Thread
choice of an existing .env alone instead of asking and then overruling it.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: The backbone interface moves to phase one

**Files:**
- Modify: `install.sh` (`configure` thread block and token block; `ask_questions`)
- Test: `tests/test_install_script.py` (replace `test_an_abort_in_configure_names_the_touched_env` around line 785; extend `test_an_empty_urandom_fallback_aborts_loudly` around line 683)

**Interfaces:**
- Consumes: `ask`, `write_env_value`, `ask_questions`, `detect_backbone_if` (existing, line 640).
- Produces: `decide_backbone`; global `CHOSEN_BACKBONE`.

- [ ] **Step 1: Write the failing tests and rewrite the old abort test**

Append to the questions section:

```python
def test_a_detected_backbone_is_not_asked(installer, tmp_path):
    # answers=["1"] only: a backbone question would hit the end of the
    # answers and abort.
    hw = _serial(tmp_path, STICK_B)
    result = installer(env={**hw, "BLUETOOTH_ADAPTER": "0"}, answers=["1"])
    assert result.returncode == 0
    assert "Border router network interface: eth0" in result.output
    assert _env(result)["BACKBONE_IF"] == "eth0"


def test_an_undetectable_backbone_is_asked_until_answered(installer, tmp_path):
    hw = _serial(tmp_path, STICK_B)
    result = installer(
        env={**hw, "BLUETOOTH_ADAPTER": "0"},
        stubs={"ip": "echo\n"},
        answers=["1", "", "eth1"],
    )
    assert result.returncode == 0
    assert _env(result)["BACKBONE_IF"] == "eth1"
```

Replace the whole of `test_an_abort_in_configure_names_the_touched_env` with:

```python
def test_an_undetectable_backbone_without_a_terminal_aborts_before_cloning(installer):
    # This used to abort inside configure, with COMPOSE_PROFILES and the
    # radio already written to .env. Asked in phase one, it now stops
    # before a single file exists.
    result = installer(
        env={"LOXMATTER_MODE": "thread", "RADIO_DEVICE": "/dev/ttyUSB0"},
        stubs={"ip": "echo\n"},
    )
    assert result.returncode == 2
    assert "BACKBONE_IF" in result.output
    assert not result.called("git clone")
    assert not result.env_file.exists()
```

In `test_an_empty_urandom_fallback_aborts_loudly`, add after the existing asserts:

```python
    # COMPOSE_PROFILES and the rest are already in .env at that point; the
    # abort has to say so rather than look like a clean stop.
    assert "partially written" in result.output
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_install_script.py -k "backbone or urandom" -v`
Expected: `test_a_detected_backbone_is_not_asked` FAILS (return code 2, the terminal closed at the backbone question). `test_an_undetectable_backbone_is_asked_until_answered` FAILS (the empty answer takes the empty default and the run aborts). `..._aborts_before_cloning` FAILS (`git clone` was called). `test_an_empty_urandom_fallback_aborts_loudly` FAILS on `partially written`.

- [ ] **Step 3: Implement `decide_backbone`**

Add a global next to `CHOSEN_RADIO`:

```sh
CHOSEN_BACKBONE=""
```

Add after `detect_backbone_if` (it may stay where it is; move `detect_backbone_if` above `decide_backbone` only if you prefer reading order - behaviour does not depend on it):

```sh
decide_backbone() {
  if [ "$MODE" != "thread" ]; then
    return 0
  fi
  step "choosing the border router's network interface"
  if [ -n "$(env_file_value BACKBONE_IF)" ]; then
    return 0
  fi
  if [ -n "${BACKBONE_IF:-}" ]; then
    CHOSEN_BACKBONE="$BACKBONE_IF"
    return 0
  fi
  CHOSEN_BACKBONE="$(detect_backbone_if)"
  if [ -n "$CHOSEN_BACKBONE" ]; then
    note "Border router network interface: $CHOSEN_BACKBONE (from the default route)."
    note "The Thread border router reaches the rest of your network over it."
    return 0
  fi
  if [ "$DRY_RUN" -eq 1 ]; then
    note "would ask for the border router's network interface"
    return 0
  fi
  if [ "$HAVE_TTY" -eq 0 ]; then
    die "BACKBONE_IF: there is no default route to read the border router's
network interface from, and no terminal to ask on. Pass it in instead:
  curl -fsSL $RAW_URL | BACKBONE_IF=eth0 sh"
  fi
  say "Border router network interface"
  note "No default route was found, so the interface the Thread border router"
  note "should use could not be read. Usually eth0 (cable) or wlan0 (WiFi)."
  while [ -z "$CHOSEN_BACKBONE" ]; do
    CHOSEN_BACKBONE="$(ask "Network interface" "")" ||
      die "The terminal closed before the network interface was given."
  done
}
```

Extend `ask_questions`:

```sh
ask_questions() {
  decide_mode
  decide_backbone
}
```

- [ ] **Step 4: Write it in `configure`, and name the half-written .env on a token failure**

In `configure`, replace

```sh
    ensure_env_value BACKBONE_IF "Network interface for the border router" \
      "$(detect_backbone_if)" 1
```

with

```sh
    write_env_value BACKBONE_IF "$CHOSEN_BACKBONE" 1
```

In the token block of `configure`, change both `die` lines to append the note:

```sh
      die "Could not generate LOXMATTER_API_TOKEN: expected 64 hex characters, got ${#token_value}.$(config_written_note)"
```

```sh
      *[!0-9a-f]*) die "Could not generate LOXMATTER_API_TOKEN: got non-hex output.$(config_written_note)" ;;
```

- [ ] **Step 5: Run the tests, then the whole file**

Run: `uv run pytest tests/test_install_script.py -k "backbone or urandom" -v`
Expected: all PASS.

Run: `uv run pytest tests/test_install_script.py -q`
Expected: all pass. `test_a_wifi_run_writes_the_detected_backbone_for_a_later_thread_switch` is untouched: the wifi branch of `configure` still writes the detected interface itself.

- [ ] **Step 6: Commit**

```bash
git add install.sh tests/test_install_script.py
git commit -m "feat(install): decide the border router's interface before installing

The backbone interface was asked in phase four, after minutes of package
and Docker installation, and asked even when the default route already
named it. It is now read up front and asked only when there is no default
route; without a terminal that case stops before anything is written.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: The Bluetooth adapter

**Files:**
- Modify: `install.sh` (globals; `detect_bt_adapter` at 645-653 is replaced; `ensure_env_value` is removed; `configure`; `ask_questions`)
- Test: `tests/test_install_script.py`

**Interfaces:**
- Consumes: `choose`, `count_lines`, `nth_line`, `write_env_value`, `ask_questions`.
- Produces: `list_bt_adapters` (sets `BT_ADAPTERS`: one line per adapter, `<index><TAB><label>`), `usb_product PATH`, `decide_bluetooth`; globals `BT_SYS_DIR`, `BT_ADAPTERS`, `CHOSEN_BT`, `TAB`.

- [ ] **Step 1: Add the sysfs helper to the tests**

Next to `_serial`:

```python
def _bluetooth(tmp_path, *adapters):
    """Fabricates /sys/class/bluetooth. Each adapter is (index, product):
    product None is a built-in UART adapter, a string a USB adapter of that
    name. The device links resolve below a directory called devices/, as in
    sysfs; the installer only looks at the path after it, so the pytest
    directory above - whose name may contain anything - cannot mislabel one.
    Measured on a Pi 3B: hci0 -> .../3f201000.serial/.../serial0/serial0-0."""
    sys_root = tmp_path / "hw" / "sys"
    klass = sys_root / "class" / "bluetooth"
    klass.mkdir(parents=True)
    soc = sys_root / "devices" / "platform" / "soc"
    for index, product in adapters:
        if product is None:
            device = soc / "3f201000.serial" / "serial0" / f"serial0-{index}"
            device.mkdir(parents=True)
        else:
            usb_device = soc / "3f980000.usb" / "usb1" / f"1-1.{index}"
            device = usb_device / f"1-1.{index}:1.0"
            device.mkdir(parents=True)
            (usb_device / "product").write_text(f"{product}\n")
        entry = klass / f"hci{index}"
        entry.mkdir()
        (entry / "device").symlink_to(device)
    return {"BT_SYS_DIR": str(klass)}
```

- [ ] **Step 2: Write the failing tests**

```python
def test_a_single_adapter_is_used_without_a_question(installer, tmp_path):
    result = installer(env=_bluetooth(tmp_path, (0, None)), answers=[])
    assert result.returncode == 0
    assert "Bluetooth: hci0 - built in (UART)" in result.output
    assert _env(result)["BLUETOOTH_ADAPTER"] == "0"


def test_two_adapters_are_offered_by_name(installer, tmp_path):
    hw = _bluetooth(tmp_path, (0, None), (1, "TP-Link UB500 Adapter"))
    result = installer(env=hw, answers=["2"])
    assert result.returncode == 0
    assert "1) hci0 - built in (UART)" in result.output
    assert "2) hci1 - USB: TP-Link UB500 Adapter" in result.output
    assert _env(result)["BLUETOOTH_ADAPTER"] == "1"


def test_the_adapter_index_is_written_not_the_menu_position(installer, tmp_path):
    hw = _bluetooth(tmp_path, (0, None), (3, "TP-Link UB500 Adapter"))
    result = installer(env=hw, answers=["2"])
    assert result.returncode == 0
    assert _env(result)["BLUETOOTH_ADAPTER"] == "3"


def test_no_adapter_is_a_warning_not_a_question(installer):
    result = installer(answers=[])
    assert result.returncode == 0
    assert "No Bluetooth adapter found" in result.output
    assert _env(result)["BLUETOOTH_ADAPTER"] == "0"


def test_without_a_terminal_the_first_adapter_is_taken(installer, tmp_path):
    hw = _bluetooth(tmp_path, (0, None), (1, "TP-Link UB500 Adapter"))
    result = installer(env=hw)
    assert result.returncode == 0
    assert _env(result)["BLUETOOTH_ADAPTER"] == "0"


def test_the_adapter_from_the_environment_skips_the_menu(installer, tmp_path):
    hw = _bluetooth(tmp_path, (0, None), (1, "TP-Link UB500 Adapter"))
    result = installer(env={**hw, "BLUETOOTH_ADAPTER": "5"}, answers=[])
    assert result.returncode == 0
    assert "Which adapter" not in result.output
    assert _env(result)["BLUETOOTH_ADAPTER"] == "5"
```

- [ ] **Step 3: Run them to see them fail**

Run: `uv run pytest tests/test_install_script.py -k "adapter" -v`
Expected: the new tests FAIL - `ensure_env_value` asks `Bluetooth adapter id for BLE commissioning`, which hits the empty answers file and aborts (or, without a terminal, writes the value from the real `/sys`).

- [ ] **Step 4: Implement the listing and `decide_bluetooth`**

Add globals next to the other `CHOSEN_*`:

```sh
BT_SYS_DIR="${BT_SYS_DIR:-/sys/class/bluetooth}"
BT_ADAPTERS=""
CHOSEN_BT=""
TAB="$(printf '\t')"
```

Replace `detect_bt_adapter` (lines 645-653) with:

```sh
# The product string of the USB device a sysfs path belongs to. An adapter's
# device link points at a USB interface; the product file sits on the device
# above it. Walked up a few levels, like radios/inventory.py's _usb_device.
usb_product() {
  up_dir="$1"
  up_steps=0
  while [ "$up_steps" -le 4 ] && [ -n "$up_dir" ]; do
    if [ -r "$up_dir/product" ]; then
      cat "$up_dir/product"
      return 0
    fi
    up_dir="${up_dir%/*}"
    up_steps=$((up_steps + 1))
  done
}

# Sets BT_ADAPTERS to one line per adapter: its hci index, a tab, a label.
# Classified like radios/inventory.py's scan_bluetooth: a USB adapter's
# device link resolves below a usb bus, a built-in one below a serial port.
# Only the part after the last /devices/ is looked at, so a directory above
# sysfs whose name contains "usb" or "serial" cannot mislabel one.
# `pwd -P` resolves the link: readlink -f is not POSIX.
list_bt_adapters() {
  BT_ADAPTERS=""
  for bt_entry in "$BT_SYS_DIR"/hci*; do
    bt_index="${bt_entry##*/hci}"
    case "$bt_index" in
      ''|*[!0-9]*) continue ;;
    esac
    bt_target="$(cd "$bt_entry/device" 2>/dev/null && pwd -P)" || bt_target=""
    case "${bt_target##*/devices/}" in
      usb*|*/usb*)
        bt_product="$(usb_product "$bt_target")"
        bt_label="USB: ${bt_product:-unknown adapter}"
        ;;
      *serial*) bt_label="built in (UART)" ;;
      *) bt_label="other" ;;
    esac
    BT_ADAPTERS="$BT_ADAPTERS$bt_index$TAB$bt_label
"
  done
}

decide_bluetooth() {
  step "choosing the Bluetooth adapter"
  if [ -n "${BLUETOOTH_ADAPTER:-}" ]; then
    CHOSEN_BT="$BLUETOOTH_ADAPTER"
    return 0
  fi
  if [ -n "$(env_file_value BLUETOOTH_ADAPTER)" ]; then
    return 0
  fi
  list_bt_adapters
  bt_count="$(count_lines "$BT_ADAPTERS")"
  if [ "$bt_count" -eq 0 ]; then
    warn "No Bluetooth adapter found. New Matter devices can then only be"
    warn "commissioned if they are already on your network (for example"
    warn "through the manufacturer's app). An adapter can be chosen later on"
    warn "the Radios card of the web interface."
    CHOSEN_BT=0
    return 0
  fi
  if [ "$bt_count" -eq 1 ]; then
    bt_line="$(nth_line "$BT_ADAPTERS" 1)"
    CHOSEN_BT="${bt_line%%"$TAB"*}"
    note "Bluetooth: hci$CHOSEN_BT - ${bt_line#*"$TAB"}, used to commission Matter devices."
    return 0
  fi
  say "Bluetooth adapter"
  note "Most Matter devices are commissioned over Bluetooth. The built-in"
  note "adapter is usually enough; a USB adapter reaches further."
  printf '\n'
  bt_position=0
  while IFS="$TAB" read -r bt_index bt_label; do
    if [ -z "$bt_index" ]; then
      continue
    fi
    bt_position=$((bt_position + 1))
    note "  $bt_position) hci$bt_index - $bt_label"
  done <<EOF
$BT_ADAPTERS
EOF
  printf '\n'
  choose "Which adapter should be used?" 1 "$bt_count" 0
  bt_line="$(nth_line "$BT_ADAPTERS" "$CHOICE")"
  CHOSEN_BT="${bt_line%%"$TAB"*}"
}
```

Extend `ask_questions`:

```sh
ask_questions() {
  decide_mode
  decide_backbone
  decide_bluetooth
}
```

- [ ] **Step 5: Write it in `configure` and remove `ensure_env_value`**

In `configure`, replace

```sh
  ensure_env_value BLUETOOTH_ADAPTER "Bluetooth adapter id for BLE commissioning" \
    "$(detect_bt_adapter)" 0
```

with

```sh
  write_env_value BLUETOOTH_ADAPTER "$CHOSEN_BT" 0
```

`ensure_env_value` now has no caller. Confirm with `grep -n ensure_env_value install.sh` (expected: only its own definition), then delete the function.

- [ ] **Step 6: Run the tests, then the whole file**

Run: `uv run pytest tests/test_install_script.py -k "adapter" -v`
Expected: all PASS.

Run: `uv run pytest tests/test_install_script.py -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add install.sh tests/test_install_script.py
git commit -m "feat(install): name the Bluetooth adapters instead of asking for an id

\"Bluetooth adapter id [0]\" asked for a number nobody could look up. One
adapter is now used without a question, several are listed as built in or
by their USB product name, and a host without one gets a warning about
what that means for commissioning.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: The Miniserver address, explained and checked

**Files:**
- Modify: `install.sh` (`ask_miniserver` at 621-638 is replaced; `configure`; `ask_questions`)
- Test: `tests/test_install_script.py` (`_CURL` stub at 147-177; fixture env; new tests)

**Interfaces:**
- Consumes: the fixture from Task 1; `ask`, `choose`, `valid_ipv4`, `add_finding`, `write_env_value`, `ask_questions`.
- Produces: `check_miniserver ADDRESS` (returns 1 when no Miniserver answered, leaving the reason in `MS_PROBLEM`), `add_miniserver_finding ADDRESS`, `decide_miniserver`; global `CHOSEN_MS`.

- [ ] **Step 1: Teach the `curl` stub and the fixture about the Miniserver**

Below `INSTALLER = ...` at the top of the test file:

```python
# Captured from a real Miniserver (Task 1 of the plan for design
# 2026-09-21-installer-guided-questions), never written by hand.
MINISERVER_API_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "miniserver" / "jdev_cfg_api.json"
```

In `_CURL`, add a branch to the `case "$url" in` block, after the `*health*)` line:

```sh
  *jdev/cfg/api*)
    for dead in ${FAKE_MS_DEAD-}; do
      case "$url" in
        "http://$dead/"*) exit 28 ;;
      esac
    done
    body="$MINISERVER_API_BODY"
    ;;
```

(`exit 28` is curl's own status for a timeout.) Also update the comment above `_CURL` with one sentence: the Miniserver check reaches `/jdev/cfg/api`; addresses in `FAKE_MS_DEAD` time out, `MINISERVER_API_BODY` is what the others answer.

In `build_env`'s `full_env` dict, after the `MINISERVER_IP` entry:

```python
            # What a Miniserver answers on /jdev/cfg/api. A test replaces it
            # to have something that is not a Miniserver answer instead.
            "MINISERVER_API_BODY": MINISERVER_API_FIXTURE.read_text(),
```

Add `import re` to the imports at the top.

Three existing tests replace the whole `curl` stub, and the Miniserver check now calls `curl` in phase one, before the Docker download they are about. Two of them (`exit 6`, and the truncated-download stub) still abort where they should - the check merely adds a finding on the way. `test_sigint_cleans_up_the_temporary_file` does not: its `sleep 30` stub would hang in the Miniserver check, and its wait loop would fire on that call instead of the download. Change its stub and its wait condition:

```python
    # The Miniserver check calls curl first, in phase one; only the Docker
    # download may hang, or the signal would land before any temporary
    # file exists and the test would prove nothing.
    hanging_download = 'case "$*" in *get.docker.com*) sleep 30 ;; esac\nexit 0\n'
    proc = installer.start(omit=("docker",), stubs={"curl": hanging_download})
    deadline = time.time() + 10
    while True:
        if installer.log.exists() and "get.docker.com" in installer.log.read_text():
            break
```

(the rest of the test stays as it is). It is skipped on macOS; it runs in CI.

- [ ] **Step 2: Write the failing tests**

```python
def _fixture_serial():
    body = MINISERVER_API_FIXTURE.read_text()
    return re.search(r"'snr': *'([^']*)'", body).group(1)


def test_a_miniserver_that_answers_is_named(installer):
    result = installer()
    assert result.returncode == 0
    assert f"Miniserver found at 10.0.1.99 (serial {_fixture_serial()}" in result.output
    assert "Findings" not in result.output


def test_an_unreachable_miniserver_without_a_terminal_becomes_a_finding(installer):
    # It may simply be switched off during the installation - that must
    # not stop the run.
    result = installer(env={"FAKE_MS_DEAD": "10.0.1.99"})
    assert result.returncode == 0
    assert "No Miniserver answers at 10.0.1.99 (timeout)" in result.output
    assert "Findings" in result.output
    assert _env(result)["MINISERVER_IP"] == "10.0.1.99"


def test_something_else_answering_is_not_taken_for_a_miniserver(installer):
    result = installer(env={"MINISERVER_API_BODY": "<html>router login</html>"})
    assert result.returncode == 0
    assert "but it is not a Miniserver" in result.output
    assert "Findings" in result.output


def test_the_miniserver_question_says_where_to_find_the_address(installer):
    result = installer(env={"MINISERVER_IP": ""}, answers=["10.0.1.43"])
    assert result.returncode == 0
    assert "Loxone Config" in result.output
    assert _env(result)["MINISERVER_IP"] == "10.0.1.43"


def test_an_unreachable_miniserver_can_be_used_anyway(installer):
    result = installer(
        env={"MINISERVER_IP": "", "FAKE_MS_DEAD": "10.0.1.42"},
        answers=["10.0.1.42", "2"],
    )
    assert result.returncode == 0
    assert _env(result)["MINISERVER_IP"] == "10.0.1.42"
    assert "Findings" in result.output


def test_a_different_address_can_be_entered_after_a_failed_check(installer):
    result = installer(
        env={"MINISERVER_IP": "", "FAKE_MS_DEAD": "10.0.1.42"},
        answers=["10.0.1.42", "1", "10.0.1.43"],
    )
    assert result.returncode == 0
    assert _env(result)["MINISERVER_IP"] == "10.0.1.43"
    assert "Findings" not in result.output


def test_a_malformed_address_is_asked_again(installer):
    result = installer(env={"MINISERVER_IP": ""}, answers=["10.0.1", "10.0.1.43"])
    assert result.returncode == 0
    assert "is not an IPv4 address like" in result.output
    assert _env(result)["MINISERVER_IP"] == "10.0.1.43"


def test_a_dry_run_does_not_contact_the_miniserver(installer):
    result = installer("--dry-run")
    assert result.returncode == 0
    assert "would check http://10.0.1.99/jdev/cfg/api" in result.output
    assert not any("jdev/cfg/api" in call for call in result.calls)


def test_a_second_run_does_not_check_the_miniserver_again(installer):
    first = installer()
    assert first.returncode == 0
    second = installer()
    assert second.returncode == 0
    assert not any("jdev/cfg/api" in call for call in second.calls)


def test_without_curl_the_miniserver_is_not_checked(installer):
    # curl is installed in phase two, after the questions.
    result = installer(omit=("curl",))
    assert result.returncode == 0
    assert "10.0.1.99 is not checked" in result.output
```

- [ ] **Step 3: Run them to see them fail**

Run: `uv run pytest tests/test_install_script.py -k "miniserver" -v`
Expected: the new tests FAIL (no check exists; no `Miniserver found` line). The existing `test_an_invalid_miniserver_ip_aborts_before_cloning`, `test_without_a_miniserver_ip_and_without_a_terminal_it_aborts` and `test_the_miniserver_ip_comes_from_the_environment` still PASS.

- [ ] **Step 4: Implement the check and `decide_miniserver`**

Add a global next to the other `CHOSEN_*`:

```sh
CHOSEN_MS=""
MS_PROBLEM=""
```

Replace `ask_miniserver` (lines 621-638) with:

```sh
# Asks the address for /jdev/cfg/api, which a Miniserver answers without
# signing in, with its serial number and firmware version. Returns 1 when no
# Miniserver answered and leaves the reason in MS_PROBLEM. The shape parsed
# here is the one captured in tests/fixtures/miniserver/jdev_cfg_api.json.
check_miniserver() {
  cm_ip="$1"
  MS_PROBLEM=""
  if [ "$DRY_RUN" -eq 1 ]; then
    note "would check http://$cm_ip/jdev/cfg/api"
    return 0
  fi
  if ! have curl; then
    note "curl is not installed yet, so $cm_ip is not checked."
    return 0
  fi
  note "Checking $cm_ip ..."
  cm_status=0
  cm_body="$(curl -fsS -m 3 "http://$cm_ip/jdev/cfg/api" 2>/dev/null)" || cm_status=$?
  if [ "$cm_status" -ne 0 ]; then
    case "$cm_status" in
      28) cm_reason="timeout" ;;
      7) cm_reason="connection refused" ;;
      22) cm_reason="HTTP error" ;;
      *) cm_reason="curl exit status $cm_status" ;;
    esac
    MS_PROBLEM="No Miniserver answers at $cm_ip ($cm_reason)."
    warn "$MS_PROBLEM"
    return 1
  fi
  cm_snr="$(printf '%s\n' "$cm_body" | sed -n "s/.*'snr': *'\([^']*\)'.*/\1/p" | head -n 1)"
  cm_version="$(printf '%s\n' "$cm_body" | sed -n "s/.*'version': *'\([^']*\)'.*/\1/p" | head -n 1)"
  if [ -z "$cm_snr" ] && [ -z "$cm_version" ]; then
    MS_PROBLEM="Something answers at $cm_ip, but it is not a Miniserver."
    warn "$MS_PROBLEM"
    return 1
  fi
  note "Miniserver found at $cm_ip (serial ${cm_snr:-unknown}, firmware ${cm_version:-unknown})."
}

add_miniserver_finding() {
  add_finding "The Miniserver address $1 was written although no Miniserver answered
there. $MS_PROBLEM
Once the Miniserver is reachable, check the address in the web interface under
Settings -> Miniserver connection."
}

decide_miniserver() {
  step "asking for the Miniserver"
  # A second run keeps the address and does not check it again.
  if [ -n "$(env_file_value MINISERVER_IP)" ]; then
    return 0
  fi
  if [ -n "${MINISERVER_IP:-}" ]; then
    # check_config_source has already refused a malformed one.
    CHOSEN_MS="$MINISERVER_IP"
    if ! check_miniserver "$CHOSEN_MS"; then
      add_miniserver_finding "$CHOSEN_MS"
    fi
    return 0
  fi
  if [ "$DRY_RUN" -eq 1 ]; then
    note "would ask for the Miniserver's address and check it"
    return 0
  fi
  # check_config_source has already stopped a run with neither an address
  # nor a terminal, so from here on there is one to ask on.
  say "Loxone Miniserver"
  note "loxmatter signs in to the Miniserver and creates the devices there."
  note "You find its address in Loxone Config under the Miniserver's"
  note "properties, or in the Loxone app under Settings -> Miniserver."
  printf '\n'
  while :; do
    CHOSEN_MS="$(ask "IPv4 address of the Miniserver" "")" ||
      die "The terminal closed before the Miniserver's address was given."
    if ! valid_ipv4 "$CHOSEN_MS"; then
      warn "'$CHOSEN_MS' is not an IPv4 address like 192.168.1.10."
      continue
    fi
    if check_miniserver "$CHOSEN_MS"; then
      return 0
    fi
    note "  1) Enter a different address"
    note "  2) Use it anyway - the Miniserver is not reachable right now"
    choose "Choice" 1 2 0
    if [ "$CHOICE" -eq 2 ]; then
      add_miniserver_finding "$CHOSEN_MS"
      return 0
    fi
  done
}
```

`add_finding` is defined in phase six, further down; it is called only at run time, so that is fine. The findings collected here are printed by `run_checks` like every other finding.

If Task 1's capture used a different spelling (for example double quotes inside `value`), adjust the two `sed` expressions to it and say so in the commit message. `test_a_miniserver_that_answers_is_named` is the test that proves the parser fits the capture.

Extend `ask_questions`:

```sh
ask_questions() {
  decide_mode
  decide_backbone
  decide_bluetooth
  decide_miniserver
}
```

- [ ] **Step 5: Write it in `configure`**

In `configure`, replace the line `  ask_miniserver` with:

```sh
  write_env_value MINISERVER_IP "$CHOSEN_MS" 1
```

- [ ] **Step 6: Run the tests, then the whole file**

Run: `uv run pytest tests/test_install_script.py -k "miniserver or answers" -v`
Expected: all PASS, including Task 2's two tests.

Run: `uv run pytest tests/test_install_script.py -q`
Expected: all pass. `test_with_no_findings_there_is_no_reference_to_findings` still passes, because the stub answers as a Miniserver by default.

- [ ] **Step 7: Commit**

```bash
git add install.sh tests/test_install_script.py
git commit -m "feat(install): explain and check the Miniserver address

The address used to be accepted as soon as it was well formed, so a typo
showed up hours later as a bridge that sends nothing. The question now
says where to find the address, and /jdev/cfg/api confirms a Miniserver
answers there. A failed check offers another try or keeps the address
with a finding; without a terminal it never stops the run.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: Announce the questions, prove they come first, update the docs

**Files:**
- Modify: `install.sh` (`usage` at 101-125; new `announce_questions` and predicates; `ask_questions`)
- Modify: `README.md` (quick start, lines 192-195 and 212-216)
- Modify: `CHANGELOG.md` (`[Unreleased]`)
- Test: `tests/test_install_script.py`

**Interfaces:**
- Consumes: `list_serial_candidates`, `list_bt_adapters`, `count_lines`, `env_file_value`, `ask_questions`.
- Produces: `announce_questions`, `thread_menu_expected`, `bluetooth_menu_expected`, `miniserver_question_expected`.

- [ ] **Step 1: Write the tests**

```python
def test_the_questions_are_announced(installer, tmp_path):
    env = {
        **_serial(tmp_path, STICK_A, STICK_B),
        **_bluetooth(tmp_path, (0, None), (1, "TP-Link UB500 Adapter")),
        "MINISERVER_IP": "",
    }
    result = installer(env=env, answers=["0", "1", "10.0.1.43"])
    assert result.returncode == 0
    assert (
        "Three questions follow: the Thread stick, the Bluetooth adapter, "
        "and the address of your Loxone Miniserver." in result.output
    )


def test_a_single_question_is_announced_as_one(installer):
    result = installer(env={"MINISERVER_IP": ""}, answers=["10.0.1.43"])
    assert result.returncode == 0
    assert "One question follows: the address of your Loxone Miniserver." in result.output


def test_without_a_terminal_nothing_is_announced(installer):
    result = installer()
    assert result.returncode == 0
    assert "question follows" not in result.output
    assert "questions follow" not in result.output


def test_every_question_comes_before_anything_is_installed(installer, tmp_path):
    # The Miniserver check is the last question's last step, and it is the
    # one that leaves a trace in the stub log. It has to come before the
    # first package and before Docker - otherwise the user is called back
    # to the keyboard minutes into the installation.
    env = {
        **_serial(tmp_path, STICK_A, STICK_B),
        **_bluetooth(tmp_path, (0, None), (1, "TP-Link UB500 Adapter")),
        "MINISERVER_IP": "",
    }
    result = installer(env=env, omit=("git", "docker"), answers=["2", "2", "10.0.1.43"])
    assert result.returncode == 0
    calls = result.calls
    check = next(i for i, call in enumerate(calls) if "jdev/cfg/api" in call)
    apt = next(i for i, call in enumerate(calls) if call.startswith("apt-get install"))
    docker = next(i for i, call in enumerate(calls) if "get.docker.com" in call)
    assert check < apt
    assert check < docker
```

In `test_help_exits_successfully`, add:

```python
    assert "/dev/serial/by-id" in result.output
```

- [ ] **Step 2: Run them**

Run: `uv run pytest tests/test_install_script.py -k "announced or nothing_is_announced or comes_before or help_exits" -v`
Expected: the two announcement tests and `test_help_exits_successfully` FAIL. `test_without_a_terminal_nothing_is_announced` and `test_every_question_comes_before_anything_is_installed` already PASS - the ordering has held since Task 3 put `ask_questions` before `install_packages`; this test keeps it that way.

- [ ] **Step 3: Implement the announcement**

Add before `ask_questions`:

```sh
# Each predicate mirrors the early returns of its decide_* function: true
# when that function will show its question. The backbone question is left
# out - it appears only without a default route, and whether it is needed
# depends on the Thread answer that has not been given yet.
thread_menu_expected() {
  if [ -f "$TARGET_DIR/deploy/testhost/.env" ] || [ -n "${RADIO_DEVICE:-}" ]; then
    return 1
  fi
  case "${LOXMATTER_MODE:-}" in
    ""|thread) : ;;
    *) return 1 ;;
  esac
  list_serial_candidates
  [ -n "$SERIAL_CANDIDATES" ]
}

bluetooth_menu_expected() {
  if [ -n "${BLUETOOTH_ADAPTER:-}" ] || [ -n "$(env_file_value BLUETOOTH_ADAPTER)" ]; then
    return 1
  fi
  list_bt_adapters
  [ "$(count_lines "$BT_ADAPTERS")" -gt 1 ]
}

miniserver_question_expected() {
  [ -z "${MINISERVER_IP:-}" ] && [ -z "$(env_file_value MINISERVER_IP)" ]
}

# Says up front what is coming, so the user knows how long to stay at the
# keyboard before the installation runs on its own.
announce_questions() {
  if [ "$HAVE_TTY" -eq 0 ] || [ "$DRY_RUN" -eq 1 ]; then
    return 0
  fi
  aq_count=0
  aq_1=""
  aq_2=""
  aq_3=""
  if thread_menu_expected; then
    aq_count=$((aq_count + 1))
    eval "aq_$aq_count=\"the Thread stick\""
  fi
  if bluetooth_menu_expected; then
    aq_count=$((aq_count + 1))
    eval "aq_$aq_count=\"the Bluetooth adapter\""
  fi
  if miniserver_question_expected; then
    aq_count=$((aq_count + 1))
    eval "aq_$aq_count=\"the address of your Loxone Miniserver\""
  fi
  case "$aq_count" in
    0) return 0 ;;
    1) aq_text="One question follows: $aq_1." ;;
    2) aq_text="Two questions follow: $aq_1 and $aq_2." ;;
    *) aq_text="Three questions follow: $aq_1, $aq_2, and $aq_3." ;;
  esac
  say "Questions"
  note "$aq_text"
}
```

Extend `ask_questions`:

```sh
ask_questions() {
  announce_questions
  decide_mode
  decide_backbone
  decide_bluetooth
  decide_miniserver
}
```

- [ ] **Step 4: Update `--help`**

In `usage`, replace the environment block's lines for `LOXMATTER_MODE`, `RADIO_DEVICE`, `RADIO_BAUDRATE` and `BLUETOOTH_ADAPTER` with:

```
  LOXMATTER_MODE      thread | wifi (skips the Thread stick menu)
  MINISERVER_IP       address of the Loxone Miniserver
  RADIO_DEVICE        Thread stick, e.g. /dev/serial/by-id/usb-... (means thread mode)
  RADIO_BAUDRATE      Thread stick baud rate; 460800 when unset, never asked
  BACKBONE_IF         network interface for the border router (thread mode only)
  BLUETOOTH_ADAPTER   Bluetooth adapter, e.g. 0 for hci0
```

(`MINISERVER_IP`, `BACKBONE_IF`, `LOXMATTER_DIR` and `LOXMATTER_API_TOKEN` keep their lines; keep the order of the block.)

- [ ] **Step 5: Run the tests, then the whole file**

Run: `uv run pytest tests/test_install_script.py -k "announced or nothing_is_announced or comes_before or help_exits" -v`
Expected: all PASS.

Run: `uv run pytest tests/test_install_script.py -q`
Expected: all pass.

- [ ] **Step 6: Update the README quick start**

In `README.md`, replace

```
It asks for your Miniserver's IP address, detects the rest — network interface,
Thread radio, Bluetooth adapter — and starts the containers. When it finishes it
prints the address of the web interface. **Open it and set a password**: until you
do, no `/api` route answers.
```

with

```
It asks up to three things before it installs anything: which USB stick is your
Thread stick (or none), which Bluetooth adapter to use when there is more than
one, and your Miniserver's IP address, which it checks right away. After that you
can walk away — it installs what is missing and starts the containers. When it
finishes it prints the address of the web interface. **Open it and set a
password**: until you do, no `/api` route answers.
```

and in the paragraph starting `**No Thread radio? That is fine.**`, replace its first sentence

```
With no USB radio the installer sets up
WiFi/Ethernet-only mode:
```

with

```
With no USB radio — or when you answer
"None" because the stick you have is a Zigbee stick — the installer sets up
WiFi/Ethernet-only mode:
```

Keep the rest of that paragraph as it is.

- [ ] **Step 7: Add the changelog entries**

In `CHANGELOG.md`, under `## [Unreleased]`, add:

```markdown
### Changed

- **The installer asks clearer questions, and all of them up front.** It lists
  the USB sticks it finds by name and asks which one is the Thread stick — or
  none, which is also the default when there are two it cannot tell apart. It
  names the Bluetooth adapters instead of asking for an id, no longer asks for
  a baud rate, and says where to find the Miniserver's address. Every question
  comes before the first package is installed, so you can walk away after
  answering.
- **The installer checks the Miniserver address.** It asks the Miniserver for
  its serial number right away. If nothing answers, you can enter another
  address or keep this one; the summary at the end then reminds you to check it.

### Fixed

- **The installer's hints work right after it installed Docker.** Until you
  log out and back in, your shell is not in the `docker` group; the commands
  the installer suggests for looking at the logs now say `sudo docker` in
  that case, instead of failing with "permission denied".
```

- [ ] **Step 8: Run every check CI runs, except the full suite**

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run python scripts/check_language.py
uv run pytest tests/test_install_script.py -q
```

Expected: `All checks passed!`, `... files already formatted`, mypy `Success`, `No German found.`, all installer tests pass.

- [ ] **Step 9: Commit**

```bash
git add install.sh tests/test_install_script.py README.md CHANGELOG.md
git commit -m "feat(install): announce the questions and describe them in the README

The installer now says up front how many questions follow, --help names
the by-id form of RADIO_DEVICE and that the baud rate is never asked, and
the README and changelog describe the guided questions and the Miniserver
check. A test pins every question before the first package install.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: Full verification

**Files:** none changed unless a check fails.

- [ ] **Step 1: The whole test suite, in three foreground parts**

The full suite takes about 11 minutes and does not fit one 10-minute command. Run the three parts one after another, each in the foreground (no `run_in_background`, no `Monitor`):

```bash
uv run pytest tests/*/ -q
```

```bash
uv run pytest $(ls tests/test_*.py | head -n 10) -q
```

```bash
uv run pytest $(ls tests/test_*.py | tail -n +11) -q
```

Expected: every part passes. Report the three pass/skip counts.

- [ ] **Step 2: Read the installer once as a user would**

The installer refuses macOS, so this runs on the test Pi, where `ssh -t` gives it a real terminal and the real `/dev/serial/by-id` and `/sys/class/bluetooth`. A dry run changes nothing there:

```bash
scp install.sh pi@10.0.1.56:/tmp/install.sh
ssh -t pi@10.0.1.56 'sh /tmp/install.sh --dry-run --dir /tmp/lm-checkout; rm -f /tmp/install.sh'
```

Read the output from top to bottom for wording, line breaks and the order of the sections. In a dry run the menus show but take their defaults. If the test Pi is not reachable, say so in the report instead of skipping silently. Fix wording only if something reads wrong, and re-run Task 7 step 8 after any change.

- [ ] **Step 3: Report**

List the commits on the branch since `312c133` (`git log --oneline 312c133..HEAD`), the test counts from step 1, and anything from step 2 that still looks off. Do not push and do not merge.
