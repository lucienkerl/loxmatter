# The radios card says when a rollback left Thread off

Date: 14 September 2026. Follows "Radios in the Web UI" (2026-09-11).

## 1. What happened

On 13 September three requests to switch Thread on failed on the test Pi.
`radios-once.sh` restored the `.env` from before each request, and in that
state Thread was off. The card then showed:

- the result line "Failed ({reason}), previous setting restored.", which reads as
  "nothing bad happened";
- a Thread select that had resynced itself to "No Thread stick (Thread off)", so
  the draft equalled the current state and the Apply button disappeared.

Nothing on the card said that Thread was now off, and nothing invited a second
attempt. Thread stayed down for about ten hours.

## 2. Goal

After a failed request that wanted Thread on, a user who only looks at the card
knows that Thread is off, that Thread devices are unreachable because of it,
and can try again with one button. The card also always states the Thread
state, job or no job.

Everything here ships with the bridge image. Nothing changes in the updater
image, `radios-once.sh`, `.env` or the crontab, so it reaches every
installation through the ordinary update in the web UI.

## 3. The bridge remembers the request it sent

`radios-state.json` (written by the sidecar) has no record of what was
requested, and the page forgets it on reload or on another device. The bridge
therefore keeps its own copy.

- `request_radios()` (`src/loxmatter/radios/sidecar.py`) writes, after the
  request file, `radios-last-request.json` in the same directory, with the same
  body (`id`, `thread`, `bluetooth`, `requested_at`), atomically through a
  `.tmp` file and `os.replace`. A failure to write this second file does not
  fail the request: the request is already on its way and the copy only
  improves a later message.
- `read_last_request(update_dir)` returns `RadiosRequestRecord(id, thread,
  bluetooth)` with `thread: ThreadRequest | None` and `bluetooth:
  BluetoothRequest | None`, or `None` for a missing, unreadable or malformed
  file. A half with the wrong shape reads as `None` for that half.
- `GET /api/radios` adds `requested` to `job`: `{"thread": {"enabled",
  "device"} | null, "bluetooth": {"adapter"} | null}` when the record's `id`
  equals the job's `id`, otherwise `null`. A job started from another bridge
  version or with no copy has `requested: null`, and the card falls back to
  today's text.

The sidecar never reads this file, and `_pending()` is not affected: it keys on
`radios-request.json` only.

## 4. The result line

A new `radiosThreadLeftOff()` in `app.js` is true when all hold:

- the job is terminal with `phase === "failed"` and `error !== "interrupted"`;
- `job.healthy !== false` (an unhealthy rollback keeps its own, stronger text);
- `job.requested?.thread?.enabled === true`;
- `radios.current?.thread_enabled === false`.

`radiosResultKey()` then returns `web.radios.result_failed_thread_off`:

```yaml
web.radios.result_failed_thread_off:
  en: "Thread could not be switched on ({reason}). The previous setting is back, and in it Thread is off: your Thread devices stay unreachable until Thread is running. Try again, or pick a different stick."
  de: "Thread ließ sich nicht einschalten ({reason}). Die vorherige Einstellung ist wieder aktiv, und darin ist Thread aus: Ihre Thread-Geräte bleiben unerreichbar, bis Thread läuft. Versuchen Sie es erneut oder wählen Sie einen anderen Stick."
```

It renders in the existing `banner danger` result paragraph.

## 5. Try again

Inside the result block, under the banner, a button `web.radios.retry`
("Try again" / "Erneut versuchen"), shown when `radiosThreadLeftOff()` and
`radios.sidecar === 'ready'` and no job is running and `!radiosBusy`.

`retryRadios()` sets `radiosDraft.threadDevice` to `job.requested.thread.device`,
and `radiosDraft.bluetoothAdapter` to `job.requested.bluetooth.adapter` when
that half is not `null`, sets `radiosDirty = true`, and calls
`askApplyRadios()`. The ordinary confirmation dialog follows, with its
"Thread on" paragraph, and the ordinary POST. A stick that has since
disappeared is refused by `POST /api/radios` as today.

## 6. The Thread state line

Directly under the Thread select, a line from `radiosThreadStatus()`, shown only
when `radios.current` is present and no job is running (during a job the border
router is being recreated on purpose):

| `thread_enabled` | `otbr_running` | key | style |
|---|---|---|---|
| false | any | `web.radios.thread_status_off` | `hint` |
| true | true | `web.radios.thread_status_running` | `hint` |
| true | false | `web.radios.thread_status_not_running` | `banner warn` |

```yaml
web.radios.thread_status_off:
  en: "Thread is off."
  de: "Thread ist aus."
web.radios.thread_status_running:
  en: "Thread is on; the border router is running."
  de: "Thread ist an; der Border Router läuft."
web.radios.thread_status_not_running:
  en: "Thread is switched on, but the border router is not running. Thread devices are unreachable until it runs again."
  de: "Thread ist eingeschaltet, aber der Border Router läuft nicht. Thread-Geräte sind unerreichbar, bis er wieder läuft."
```

`otbr_running` is the container state the sidecar reports on every pass, not a
Thread network check; the texts say "border router is running" and no more.

## 7. Tests

- `tests/radios/test_sidecar.py`: the copy is written with the request's body;
  `read_last_request` round-trips, and returns `None` for missing, non-JSON and
  non-object files; a failing copy write still returns a job id.
- `tests/api/test_radios_api.py`: `job.requested` is the record when ids match,
  `null` when they differ or the file is absent.
- `tests/api/test_web.py`: `radiosResultKey()` picks the new key exactly under
  §4's conditions (each condition flipped once keeps the old key);
  `retryRadios()` fills the draft and opens the confirmation;
  `radiosThreadStatus()` follows §6's table; the strings exist in en and de.
- Every protective test is fault-injected once.
- The Alpine bindings are run once in a throwaway browser harness against the
  served markup: banner, button and state line appear and the button opens the
  confirmation.

## 8. Out of scope

- A warning outside the settings page (sidebar, diagnostics).
- Any change to `radios-once.sh`, its rollback or its timings.
