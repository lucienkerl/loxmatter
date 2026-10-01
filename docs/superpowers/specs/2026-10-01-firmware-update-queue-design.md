# Updating every device at once: the firmware update queue

Design, October 1, 2026. Builds on
[firmware updates, stage 1](2026-09-30-device-firmware-updates-design.md)
and changes nothing in it except what section 6 lists.

## 1. The problem

The "Device updates" card installs one device per click. With five IKEA
devices on offer, an operator has to come back after each transfer — which
over Thread can take a long time — and start the next one. The install
itself already runs on the bridge (`FirmwareJobs`, a background task that
survives a restart of loxmatter through `resume_all`); what is missing is
that, once one device is done, the next starts without anybody there.

## 2. Decisions

Taken with Lucien on October 1, 2026:

- **One after another, never in parallel.** Almost every device with an
  offer is a Thread device. Transfers compete for the same mesh, the otbr
  container has a history of crashes, and whether matter-server serves
  several OTA transfers at once has not been measured. The existing
  one-install-at-a-time lock stays; the queue feeds it.
- **A failed device does not stop the queue.** It keeps its `failed` state
  and the next device starts. **Exception:** when the job ends
  `interrupted` (matter-server link lost) or the source no longer reports
  the firmware capability, the queue halts. Every further device would fail
  the same way.
- **A confirmation dialog with a checklist.** "Update all" lists every
  device in state `available`, all ticked; the operator can untick any.
- **Offline devices are offered anyway.** Whether a device is online is
  decided when its turn comes, not when the operator clicks: a sleeping
  battery device is not dropped up front.
- **The queue survives a restart of loxmatter** (a bridge update through the
  web UI, a container restart, a reboot of the Pi). It lives in the store.
- **The offer installed is the one current at the device's turn.** If the
  daily check finds a newer one in between, the newer one is installed; if
  the device already has the version, it is skipped. The single install's
  409 on a changed offer (stage 1, 7.1) stays as it is — it guards a stale
  browser tab, which a queue does not have.

## 3. Store

Additive migration to schema 16:

```sql
ALTER TABLE firmware_status ADD COLUMN queued_at TEXT;
```

- A device is queued while `queued_at` is set. Order: `queued_at`, then
  `device_id` (one enqueue writes the same timestamp for every device).
- A deleted device leaves the queue through the existing
  `ON DELETE CASCADE`.
- Why the queue halted is the key `firmware.queue_halted_reason` in the
  existing `setting` table, read and written next to
  `firmware.daily_check_enabled` in `FirmwareSettingsStore`. Missing means
  not halted. The value is the i18n key of the reason, so the UI shows it
  in the language chosen when it is read, not when it was written.
- No column is ever dropped (the updater's rollback runs without a database
  restore).

Schema 16 may collide with another branch that also adds a migration; the
number is checked against `main` before merging.

## 4. `firmware/queue.py`: `FirmwareQueue`

One background task, started by `start()`, cancelled by `stop()`. It owns
no state of its own beyond the task and an `asyncio.Event` that wakes it;
everything that must survive a restart is in the store.

- **`enqueue(device_ids) -> int`**: queues every listed device that has an
  offer and is not queued yet, all with the same `queued_at`. Clears the
  halt reason and wakes the task. Returns how many devices it queued.
- **`resume()`**: clears the halt reason and wakes the task.
- **`clear()`**: clears `queued_at` on every row and the halt reason. An install already running
  goes on; a transfer between matter-server and a device cannot be
  cancelled, today as before.
- **The loop**, while devices are queued and the queue is not halted:
  1. If an install is running (resumed after a restart, or started with a
     single click), wait for it with `jobs.wait()`.
  2. Look at the first device. It leaves the queue (its `queued_at` is
     cleared) when it is skipped, marked offline, or its install starts —
     not before, so a device whose start is refused stays first.
  3. No offer, or the installed `0/40/9` has reached it: skip, record
     nothing.
  4. Offline: end its job `failed` with the error
     `api.firmware.queue_offline_at_turn` ("Offline when its turn came").
     Go on.
  5. Otherwise `jobs.start(device_id, offer.software_version)`, then
     `jobs.wait()`. `FirmwareBusyError` (a single install started in the
     same instant) leaves the device first; the loop waits for that install.
  6. Read the job's end state. `interrupted` halts the queue with
     `api.firmware.queue_halted_disconnected`. `FirmwareUnsupportedError`
     from `start` halts it with `api.firmware.queue_halted_unsupported`;
     the device stays first. Anything else: next device.
- **Errors in the loop itself** are logged and halt the queue with
  `api.firmware.queue_halted_error`, rather than ending the task silently
  with devices still queued.

**Start and shutdown.** `cli` calls `queue.start()` right after
`jobs.resume_all()`. A device `resume_all` resumes is waited for in step 1;
the queue then goes on where it stopped. On shutdown the task is cancelled
before `jobs.stop()`; `queued_at` stays in the store.

`FirmwareJobs` is unchanged. A single install while the queue runs gets the
existing 409 "running on device X".

**Not included:** a pause between two devices. It would be a guess. If the
first real installs show that the mesh needs time after a device restarts,
it is added then, with the measurement.

## 5. API and UI

### 5.1 Routes

All under `/api`, behind the existing guard.

| Route | Answer |
| --- | --- |
| `POST /firmware/queue` with `{"device_ids": [...]}` | 202 with the overview; 409 when the capability is missing or none of the devices has an offer |
| `POST /firmware/queue/resume` | 200 with the overview |
| `DELETE /firmware/queue` | 200 with the overview |

`GET /firmware` additionally carries
`queue: {device_ids: [...], halted_reason: str | null}` (ids in order,
reason already translated) and, per device, `queue_position: int | null`
(1-based). Every `detail` a user can see goes through `i18n.t(...)`, with an
`en` and a `de` value.

### 5.2 Screens

- **Card "Device updates".** A button **"Update all (N)"** next to "Check
  for updates now"; N counts the devices in state `available`. Disabled at
  N = 0 and while the queue is not empty.
- **Dialog.** A list with a checkbox per device, all ticked: label, room,
  installed → offered. An offline device carries "offline — skipped if it
  still is when its turn comes". Below, the warning box of the single
  dialog and one more sentence: the updates run one after another on the
  bridge, the browser can be closed. Buttons "Cancel" and
  **"Install N updates"**, N following the ticks.
- **While the queue runs**, a band above the table: "Updating <label> ·
  43 % · 3 more queued" and a button "Cancel the rest" (calls
  `DELETE /firmware/queue`). It names what is left, not "2 of 5": a browser
  opened halfway through cannot know how many the run started with, and the
  server keeps no run counter.
- **When halted**, a warning band with the reason and the buttons
  "Continue" and "Clear the queue".
- **Table.** The state column reads "Queued (3rd)" for a queued device.
  The per-row install buttons are disabled while the queue is not empty,
  as they are today while an install runs.
- **Device tile.** The copper band reads "Update queued" instead of
  "Update available".
- **Polling.** The 5 s interval of stage 1 (9.2, 7.2) also applies while
  the queue is not empty.

## 6. What changes in stage 1's design

- Section 7.1 gains a sibling: the queue starts installs through the same
  `FirmwareJobs.start()`.
- Section 8, store: schema 16 adds `queued_at`.
- Section 9.1 gains the routes of 5.1.

## 7. Testing

### 7.1 Automated

- `FirmwareQueue` against a fake `FirmwareJobs` and a fake source: order;
  skip without offer and when the version is already reached; offline ends
  `failed` and goes on; `failed` goes on; `interrupted` halts with the rest
  still queued; unsupported halts and keeps the device; `resume` and
  `clear`; picking the queue up after a restart from the store; waiting for
  an install that already runs.
- Migration 15 → 16 is additive.
- API answers, the 409s, and their `en`/`de` strings.
- WebUI: the dialog, the running band, and the halted band run in the
  browser harness, not only asserted as delivered HTML.
- New test files get names no other test module has. Before merging:
  `--collect-only` over everything, then the suite in two halves.

### 7.2 On `pi@10.0.1.56`

Queue two devices — node 22 (KAJPLATS E14, 1.1.0 → 1.2.0) and one with a
small update — close the browser, and come back after the transfers. Both
must report the new version, the overview must show both as done, and
Loxone must still receive their values. This is also the first real install
that stage 1's release gate (10.3, point 3) still waits for; its
measurements go into that document.
