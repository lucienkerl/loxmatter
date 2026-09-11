/*
 * loxmatter - connects Matter devices to a Loxone Miniserver.
 * Copyright (C) 2026 Lucien Kerl
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program.  If not, see <https://www.gnu.org/licenses/>.
 */

// State and behavior of the loxmatter UI (Task 7, Phase 5).
//
// Identifiers are English, like the rest of this project's code - only
// text that actually reaches a person on screen or in an error case is
// German (see the task statement: "German in the UI, in prose and error
// messages, English in identifiers - in JavaScript too").
//
// No classes, no module system, no bundler: a single `app()` function
// that Alpine.js calls via `x-data="app()"` in `index.html`, whose
// returned object carries the entire state. That fits the rest of this
// file - a flat, easily readable structure instead of a class hierarchy
// for a single page.

// Time span between two reconnection attempts of the live WebSocket,
// doubled after a drop up to this maximum (Spec 8.3: a lost connection
// must recover on its own, without flooding the line with attempts every
// second).
const RECONNECT_DELAY_INITIAL_MS = 1000;
const RECONNECT_DELAY_MAX_MS = 15000;

// After how many unsuccessful attempts of the VERY FIRST connection
// (before the first ever successful one) the header switches from the
// neutral "Connecting…" wording to a clearer text (Review-Fix Minor #4,
// 2026-09-02). Without this, the header would stay on "Connecting…"
// indefinitely for a bridge that is unreachable from the start, while it
// keeps quietly retrying in the background - no data risk (there are no
// live values yet that could falsely look current), but a weaker
// diagnostic signal than the case of the LOST connection, which already
// gets a red banner and "Connection lost".
const INITIAL_CONNECT_FAILURES_BEFORE_GIVING_UP_ON_SILENCE = 3;

// The bridge's heartbeat key (see loxone/runtime.py, HEARTBEAT_KEY). It
// does not belong to any device and arrives even when nothing on any
// device changes - which makes it the only reliable sign of life this UI
// has.
const HEARTBEAT_KEY = "bridge_alive";

// How long a freshly arrived value stays highlighted. A bit more than two
// seconds: `nowTick` runs on a one-second cadence, so the threshold is
// accurate to within one second anyway, and anything shorter than two
// ticks would risk missing the highlight if you happen to be looking
// elsewhere.
const VALUE_FRESH_MS = 2500;

// How long `applyUpdate()` keeps polling after a successful POST before it
// gives up on ever seeing the sidecar collect the request - see
// `updateAwaitingPickup()`/`updateNeverCollected()` and the comment on
// `applyUpdate()` itself for the full reasoning. Derived from the sidecar's
// own loop (deploy/updater/entrypoint.sh): a fresh request.json can be
// written just after a pass has already checked for one and found nothing
// (that pass then runs to completion - near-instant when idle, since
// update-once.sh's very first move on no request is `exit 0`) - so the
// worst case is roughly one full loop period (worker time, negligible when
// idle, plus the 2s `sleep`) before the NEXT pass reads it and writes
// `phase: queued`. Call that ~4s to allow for a slow pass. Five times that
// - 20s - is generous enough to absorb scheduling jitter on constrained Pi
// hardware while still being a bounded wait, not the "poll forever" this
// fix explicitly must not become (a two-second poll is real load for a
// value that changes maybe ten times a year, see `updateTimer`'s own
// comment).
const UPDATE_APPLY_GRACE_MS = 20000;

// --- Live diagnostics (Task 6, Spec 10.5) -----------------------------------
//
// Upper bound on the lines kept per stream (logs, UDP capture, command
// log). Without it each of the three rings would keep growing without
// bound during a long session - unlike the server (whose three rings have
// a fixed size from the start, see DATAGRAM_LOG_SIZE, COMMAND_LOG_SIZE,
// LOG_BUFFER_SIZE), the browser tab would otherwise keep every line since
// the view was opened in memory. The same value as the server-side
// snapshot limit per stream would be too tight (SNAPSHOT_LIMIT = 50 only
// applies to the ONE-TIME burst on connect) - 500 instead matches the
// full ring size per stream and is thus enough for a longer session
// without letting the tab grow without bound.
const DIAGNOSTICS_LINE_LIMIT = 500;

// What marks a datagram as "noise" for `hideNoise` to hide: `message.forced`
// (`api/diagnostics_live.py`, filled from `DatagramLogEntry.forced`, see
// there) - NO LONGER the arrival rate in the browser (follow-up fix Task 6,
// 2026-09-03). The earlier heuristic here assumed "no device in this
// project regularly changes several signals within the same millisecond" -
// `Runtime.on_event` (`loxone/runtime.py`) itself disproves that: a pulse
// and its counter go out there back to back with no delay in between, a
// few microseconds apart, and were therefore ALWAYS flagged as a burst.
// The server, by contrast, already knows reliably why a datagram was
// sent - `force=True` stands for EXACTLY two callers, `Runtime.resend_all()`
// (full resend) and the heartbeat, never for a real value change. Taking
// over that information instead of guessing it in the browser from the
// time gap is the actual fix - an arrival rate cannot structurally tell
// two real, closely spaced changes apart from a burst.

// Order of the Python log levels as `logging` knows them - for the
// comparison against `logLevel` below ("show from level X up"). An entry
// with a level unknown here is NOT filtered out (see
// `visibleDiagnosticsLogs`): better to show an unexpected level than to
// silently swallow a line that might matter.
const LOG_LEVEL_ORDER = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"];

// Running number for toast messages. Module-wide instead of in the
// state, because it only needs to tell the messages apart and no one
// else cares about it.
let toastCounter = 0;

/**
 * Error for a call without a valid session - its own class so the UI can
 * tell this case apart from any other failure without checking a message
 * text.
 */
class UnauthorizedError extends Error {
  constructor() {
    super(t("web.auth.session_expired"));
    this.name = "UnauthorizedError";
  }
}

/**
 * Reads the error text from a FastAPI error response (`{"detail": "..."}"`)
 * - or returns a generic text if the response was not JSON (e.g. a network
 * error with no response from the server at all).
 */
async function readErrorDetail(response) {
  try {
    const body = await response.json();
    if (body && typeof body.detail === "string") {
      return body.detail;
    }
  } catch {
    // Response was not JSON - the generic text below is then enough.
  }
  return t("web.errors.http_status", { status: response.status });
}

/**
 * Calls a JSON endpoint and, on an error response, throws an `Error` whose
 * message is the backend's `detail` text - the same message a server log
 * would also see, not just "HTTP 400". A diagnostics UI that swallows or
 * waters down a failure would be exactly the opposite of its purpose
 * (Spec 8.1).
 */
async function requestJson(method, path, body) {
  let response;
  try {
    response = await fetch(path, {
      method,
      // The session cookie instead of a token in the header: `same-origin`
      // sends it only to the exact origin this page was loaded from, and
      // to no other. An `Authorization` header is no longer set here - the
      // token-based path still exists, but for scripts, not for this
      // browser (see api/auth.py).
      credentials: "same-origin",
      headers: body !== undefined ? { "Content-Type": "application/json" } : {},
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    // `fetch()` itself only throws on a network-level error - connection
    // refused, bridge process down, network unreachable - never on an
    // error response from the server (that is caught by `readErrorDetail`
    // above, further down in this function). Without this `catch`, the raw
    // browser text for it ("Failed to fetch" or similar) would pass
    // through unchanged all the way into the UI - English and browser
    // jargon, in a tool whose very purpose is to show a failure honestly
    // AND understandably (Spec 8.1). Review-Fix Important #2, 2026-09-02.
    throw new Error(t("web.errors.bridge_unreachable"));
  }
  if (response.status === 401 && !path.startsWith("/auth/")) {
    // Not the raw server text: a 401 mid-operation means the session has
    // expired, and the dedicated error class routes the UI back to the
    // login screen (see `noteAuthError` below). This does NOT apply to
    // `/auth/` paths themselves: there, a 401 simply means "wrong
    // password", and that exact server text should reach the login
    // screen, not the message pre-formulated here about an expired
    // session, which did not exist yet on the very first attempt.
    throw new UnauthorizedError();
  }
  if (!response.ok) {
    const error = new Error(await readErrorDetail(response));
    // `status` is attached here, not just the text: `submitPassword`
    // below needs to be able to tell a 409 on `/auth/setup` apart from
    // any other failure, and the message text for that is not a reliable
    // anchor (it could change independently of the status code).
    error.status = response.status;
    throw error;
  }
  if (response.status === 204) {
    return null;
  }
  return response.json();
}

/**
 * Downloads a file from `/api`. Via `fetch` and not via an `<a href>`,
 * because a 401 would otherwise land as raw error text in the browser
 * window instead of in the UI - and because the blob download can set
 * the file name this way.
 */
async function requestDownload(path, filename) {
  let response;
  try {
    response = await fetch(path, { credentials: "same-origin" });
  } catch {
    throw new Error(t("web.errors.bridge_unreachable"));
  }
  if (response.status === 401) {
    throw new UnauthorizedError();
  }
  if (!response.ok) {
    throw new Error(await readErrorDetail(response));
  }
  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = filename;
  link.click();
  // Without this release, the browser keeps the complete blob in memory
  // until the page is left - for a Fabric backup or an export ZIP that is
  // not pocket change. But NOT immediately: some browsers only start the
  // download of an object URL after the current call stack, and an
  // already-released URL then makes the download silently fail. The
  // `setTimeout` releases it one round later - unnecessary for Chrome, not
  // for Firefox (Review-Fix Minor #3, 2026-09-03).
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
}

// Module-global, deliberately NOT on the app() object (see the
// implementation plan, Task 8: "t() must be callable globally") - every
// function in this file can reach it, including requestJson/
// requestDownload/requestUpload, which have no access to the Alpine
// component's `this`. Not reactive, because it does not need to be: a
// language change reloads the whole page.
let translationStrings = {};

/** Translation helper - returns the text for `key` in the current
 * language, with {placeholder} replaced from values. If the key is
 * missing (e.g. a page not yet reloaded after a deployment with new
 * keys), t() returns the key itself instead of crashing - visibly wrong
 * instead of a broken page, the same stance as everywhere else in this
 * project ("a click that does nothing must arrive as a clear refusal"). */
function t(key, values = {}) {
  const template = translationStrings[key];
  if (template === undefined) {
    return key;
  }
  return template.replace(/\{(\w+)\}/g, (match, name) =>
    Object.prototype.hasOwnProperty.call(values, name) ? String(values[name]) : match
  );
}

/**
 * Uploads a file via multipart/form-data and expects JSON back - a
 * dedicated function instead of `requestJson`, because a file upload is
 * not a `JSON.stringify` body and `Content-Type` must be left to the
 * browser (it sets the multipart boundary itself, including the
 * separator sequence that `JSON.stringify` does not know at all).
 */
async function requestUpload(path, formData) {
  let response;
  try {
    response = await fetch(path, {
      method: "POST",
      credentials: "same-origin",
      body: formData,
    });
  } catch {
    throw new Error(t("web.errors.bridge_unreachable"));
  }
  if (response.status === 401) {
    throw new UnauthorizedError();
  }
  if (!response.ok) {
    const error = new Error(await readErrorDetail(response));
    error.status = response.status;
    throw error;
  }
  return response.json();
}

/**
 * Decodes a base64 string into a blob - for downloading the patched
 * project file from the JSON response of `/api/export/project-sync` (the
 * file arrives embedded in the plan response, not via a dedicated
 * download call, see `downloadPatchedProject` below).
 */
function blobFromBase64(base64, mimeType) {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return new Blob([bytes], { type: mimeType });
}

// ---------------------------------------------------------------------------
// Navigation via the address bar
// ---------------------------------------------------------------------------
//
// Every view has its own fragment: `#/devices`, `#/export`, `#/system`,
// `#/settings`. The tab bar in `index.html` therefore consists of real
// `<a href="#/...">` links instead of buttons - the selected tab thus
// survives a page reload (the reason for this change: reloading on
// "Settings" used to land back on the device dashboard), can be
// bookmarked and shared, and the back button leads to the previous tab
// instead of out of the application.
//
// A fragment and not a path (`/settings`): the server serves exactly one
// file under `/` (see loxone/server.py); a path would need a catch-all
// route there that falls back to `index.html` for every unknown path.
// The fragment never reaches the server anyway.

// --- Pairing code (design of 2026-09-07) ----------------------------------
//
// On the device the numeric code appears grouped: `1234-567-8901`. That is
// exactly how everyone types it - so the field accepts it the same way and
// writes the hyphens itself while typing.

// The rule exists TWICE: here and as `_strip_separators` in
// `api/models.py`. That is deliberate - the UI formats, the backend
// normalizes for EVERY caller of the route. Whoever changes one of the two
// versions changes the other.

// Anything except digits, whitespace, and a hyphen makes the value a
// QR payload. The check thus kicks in at the first typed `M` of `MT:`,
// not only at the colon: a rule that waits for `MT:` would treat the
// two characters before it as digit input and discard them.
const PAIRING_QR_PAYLOAD = /[^0-9\s-]/;

// The documented spelling only exists for the eleven digits. There is
// none for the 21-digit code - a made-up grouping would look different
// from what is printed, so the field would format the code AWAY from the
// original instead of toward it. From the twelfth digit on it therefore
// stays ungrouped.
const PAIRING_GROUPS = [4, 7, 11];

function isPairingQrCode(raw) {
  return PAIRING_QR_PAYLOAD.test(raw);
}

function formatPairingCode(raw) {
  if (isPairingQrCode(raw)) {
    return raw;
  }
  const digits = raw.replace(/\D/g, "");
  const parts = [];
  let start = 0;
  for (let i = 0; i < PAIRING_GROUPS.length; i++) {
    const end = PAIRING_GROUPS[i];
    if (digits.length <= start) {
      break;
    }
    // For the last group: include all remaining digits,
    // from the twelfth digit on there is no further grouping.
    const sliceEnd = i === PAIRING_GROUPS.length - 1 ? digits.length : end;
    parts.push(digits.slice(start, sliceEnd));
    start = end;
  }
  return parts.join("-");
}

function normalizePairingCode(raw) {
  const text = raw.trim();
  // Must match `_COMMISSION_CODE_SEPARATORS` in api/models.py: there
  // `re.compile(r"[\s-]")` is used to remove whitespace and hyphens.
  // This here is the counterpart - not /\D/, but exactly
  // these characters. Until one of the QR checks is changed
  // independently, both versions produce the same normalization for every
  // reachable input, but that equality was previously only inferred via a
  // silent invariant.
  return isPairingQrCode(text) ? text : text.replace(/[\s-]/g, "");
}

// What the chip in the field says. Returns a key instead of text, so
// this function stays testable without a loaded string table -
// translation happens only when displayed.
//
// The chip DESCRIBES, it does not forbid: even with `bad` the commission
// button stays operable and the value goes to the route unchanged.
// The same stance as the validator in the backend - the bridge says what
// it sees, and lets the Matter stack decide.
function describePairingCode(raw) {
  const text = raw.trim();
  if (!text) {
    return { key: "", values: {}, tone: "idle" };
  }
  if (isPairingQrCode(text)) {
    return /^MT:/i.test(text)
      ? { key: "web.devices.code_detect_qr", values: {}, tone: "ok" }
      : { key: "web.devices.code_detect_invalid", values: {}, tone: "bad" };
  }
  const count = text.replace(/\D/g, "").length;
  if (count === 11) {
    return { key: "web.devices.code_detect_manual", values: {}, tone: "ok" };
  }
  if (count === 21) {
    return { key: "web.devices.code_detect_manual_long", values: {}, tone: "ok" };
  }
  if (count > 21) {
    return { key: "web.devices.code_detect_too_long", values: {}, tone: "bad" };
  }
  // Counted against the next valid length - first 11, then 21.
  const missing = count < 11 ? 11 - count : 21 - count;
  return missing === 1
    ? { key: "web.devices.code_detect_remaining_one", values: {}, tone: "warn" }
    : { key: "web.devices.code_detect_remaining_many", values: { n: missing }, tone: "warn" };
}

const VIEWS = ["devices", "export", "system", "settings"];
const DEFAULT_VIEW = "devices";

/** The fragment that belongs to a view. */
function hashForView(view) {
  return `#/${view}`;
}

/**
 * The view from the current fragment - `null` if nothing valid is there:
 * on a call without a fragment, after a bookmark to a view that has since
 * been removed (`#/signals`, see `openSignalsModal`), or after a typo in
 * the address bar. Callers then fall back to `DEFAULT_VIEW` or the
 * currently shown view instead of showing a blank page.
 */
function viewFromHash() {
  const name = window.location.hash.replace(/^#\/?/, "");
  return VIEWS.includes(name) ? name : null;
}

/**
 * Writes the view into the address bar.
 *
 * If a valid view was already there, the change is an ordinary history
 * entry - the back button then leads to the previous tab. If nothing
 * valid was there (the most common case: the first call with no
 * fragment), the entry replaces the existing one: otherwise the back
 * button would lead to the same page without a fragment, which would
 * immediately add the fragment back on - a button that visibly does
 * nothing.
 *
 * Only sets `location.hash` on an actual change: an identical value would
 * not trigger a `hashchange`, but `replaceState` would write a history
 * entry for nothing.
 */
function writeHash(view) {
  const target = hashForView(view);
  if (window.location.hash === target) {
    return;
  }
  if (viewFromHash() === null) {
    window.history.replaceState(null, "", target);
  } else {
    window.location.hash = target;
  }
}

// Control kinds ("none", "percent", "kelvin", "hue_sat") for which
// the control modal builds its own control - everything outside
// this list (including "unknown" itself, and any future value that
// `profiles/clusters.yaml` might one day carry) falls back to the plain
// number field. ONE place for this list instead of a comparison
// against the literal wording "unknown" alone (finding I-2, closing
// review 2026-09-08): otherwise a new `control` value unknown to the UI
// vanishes without a trace, even though `hasAdjustableControls` already
// shows the "Control" button. The counterpart is in `profiles/table.py`,
// `command_control` - and `tests/profiles/test_table.py` makes sure that
// no entry of the table carries a value outside this list.
const KNOWN_CONTROL_KINDS = ["none", "percent", "kelvin", "hue_sat"];

// Control kind (`command.control`) -> field(s) in `controlDrafts`
// that hold its last SENT value. ONE place for this mapping instead of
// its own case distinction at every slider binding
// (finding I-1, closing review 2026-09-08): the control kind decides
// here exactly as with `controlsByKind`, never `command.slug`.
// `hue_sat` carries two fields, because the color area delivers hue AND
// saturation in one click - see `recordControlDraft` below.
const CONTROL_DRAFT_FIELDS = {
  percent: ["percent"],
  kelvin: ["kelvin"],
  hue_sat: ["hue", "saturation"],
};

function app() {
  return {
    // --- View --------------------------------------------------------------
    // The initial value only holds until `init()` has read the address
    // bar's fragment (see `viewFromHash` above).
    view: DEFAULT_VIEW,

    // --- Access -------------------------------------------------------------
    // `authReady` prevents a flash of the wrong screen: until `/auth-info`
    // has answered, the page does not know whether it must show setup,
    // login, or the app, and therefore shows none of them.
    authReady: false,
    passwordSet: false,
    authenticated: false,
    passwordDraft: "",
    passwordRepeatDraft: "",
    authBusy: false,
    authError: null,

    // --- Translation ---------------------------------------------------------
    // Same pattern as authReady: until GET /api/i18n has answered, the
    // page shows nothing - see stringsReady in both auth-screen templates
    // in index.html. The actual table lives NOT here but in the
    // module-global translationStrings (see t() above) - this field only
    // exists for x-if="stringsReady && ...".
    stringsReady: false,
    language: "en",

    // --- Devices -------------------------------------------------------------
    devices: [],
    devicesError: null,
    // Controls by SUBJECT, not only by device: a device's subject is its
    // numeric id, a group's is the string "g3" (see `groupSubject`). One
    // map rather than two, because the two kinds of key cannot collide (a
    // number against a "g..." string) and because every reader below - the
    // tile's command bar, the control modal - asks both the same question.
    // That mirrors the server, where a group's command key (`g3_on`) lives
    // in the same namespace as a device's (`d12_1_on`) and is sent through
    // the very same `POST /api/commands/{key}` (design 2026-09-10,
    // section 5).
    controlsBySubject: {},
    commandValueDrafts: {},
    commandBusyKey: null,
    // Draft storage for the sliders in the control modal (task 7): filled
    // once on open from `readStartValues`, see there.
    controlDrafts: {},
    // The control modal's active tab. Set on open from the device's
    // `colormode` signal (0 = hue/sat, 2 = mired, checked against the
    // SDK) - so the modal does not guess the mode, it reads it.
    controlTab: "white",
    // Short messages as an overlay instead of in the text flow
    // (2026-09-03): a line faded in within the flow shifts everything
    // below it, and whoever is about to click a second command misses
    // it.
    toasts: [],
    // When a key last came in over the live connection. Makes the
    // difference visible between "nothing is changing" and "nothing is
    // arriving" - for a plug socket with no load, both look the same.
    liveSeenAt: {},
    // When something last arrived from a device, by device id. The
    // per-signal `liveSeenAt` above cannot answer this: asking "when did
    // I last hear from this DEVICE" would mean scanning every one of its
    // ~170 signal keys on every redraw, once a second, per tile.
    //
    // Never reset, exactly like `liveSeenAt` - a reconnect of the live
    // socket does not unmake the fact that something arrived earlier.
    deviceHeardAt: {},
    lastHeartbeatAt: null,
    // Ticks every second so the "... ago" labels keep up. Without this
    // field, Alpine would see no reason to redraw them.
    nowTick: Date.now(),
    labelDrafts: {},
    deviceActionError: null,

    // --- Groups (design 2026-09-10, section 6) -----------------------------
    //
    // A separate list, deliberately NOT mixed into `devices`: a group is a
    // named sender without a node, so every field the device tile reads
    // from a node (online, last heard, signals) is absent on it, and one
    // merged list would need a guard at each of them. The tile markup in
    // index.html is copied for the same reason - it shows the few things a
    // group has and none of the things only a node has.
    //
    // BEWARE of a name collision in this file: `deviceGroups()` further
    // down has nothing to do with these groups - it is the older helper
    // that buckets the device TILES by room. `groups`/`visibleGroups()` is
    // always a device group, `deviceGroups()` is always a room section.
    groups: [],
    groupsError: null,
    groupLabelDrafts: {},
    groupActionError: null,
    // The create/edit dialog. `id` is null while a group is being created
    // and the group's id while its member list is being edited - one
    // dialog for both, because the member list is the bulk of it either
    // way. `roomTouched` keeps the room prefill from overwriting a name
    // the user typed (design 6: prefilled when the members agree, the
    // user's decision from then on).
    groupDraft: { id: null, label: "", room: "", memberIds: [], roomTouched: false },
    groupDialogError: null,
    groupDialogBusy: false,
    // Like `signalsModalBackdropMousedown`, only for the group dialog - see
    // the comment on the `<dialog>` in index.html.
    groupDialogBackdropMousedown: false,

    // --- Rooms, filter, search (device tab design, 2026-09-05) -------------
    //
    // THREE states, not two, and the difference between the last two is
    // the reason for this encoding:
    //   null  = "All"
    //   ""    = "No room" (the devices whose `device.room` is NULL)
    //   "Bad" = this one room
    // "No room" is a real selection and must stay distinguishable from
    // "All" - using `null` for both would have been the obvious and wrong
    // way to go, because `device.room` itself is `null`. The empty string
    // cannot collide with any real room: `set_room` trims and turns an
    // empty name into NULL, so a room named "" can never come into
    // existence. It is also exactly the value the API expects for
    // "remove room" - the same encoding on both sides, not two.
    //
    // Deliberately not remembered persistently in the browser (no web
    // storage): a remembered filter would otherwise create the moment
    // where, two weeks later, three out of twelve devices show up and no
    // one remembers why. After a reload the view is back on "All".
    roomFilter: null,
    deviceSearch: "",
    // Which tile currently shows the text field for a new room name
    // (device id or null). A single global scalar, not a set per tile:
    // that means only ONE text field can ever be open. Choosing
    // "+ New room ..." in a second tile's menu therefore closes the first
    // one too - intentional, two simultaneously open text fields would be
    // confusing anyway.
    newRoomFor: null,
    newRoomDraft: "",
    // Which room is currently being renamed inline (room name or null).
    renamingRoom: null,
    renameDraft: "",

    // Commissioning (Spec 7.1).
    commissionCode: "",
    commissionThreadDataset: "",
    commissionRoom: "",
    commissionNewRoom: "",
    commissionBusy: false,
    commissionMessage: null,
    commissionMessageIsError: false,
    // The commissioning card's progress display (design from 2026-09-07).
    // `commissionStep` is NULL as long as the form is visible, and after
    // that the index of the step currently running - 0 during the POST to
    // /api/devices/commission, 1 while signals and commands are being
    // (re)loaded, 2 once everything is done. `commissionFailed` colors
    // the step it got stuck on; the index stays put so you can see WHERE
    // it stopped.
    //
    // Deliberately separate from `commissionBusy`: busy locks the button
    // and is true while a run is in progress, `commissionStep` stays set
    // afterwards and carries the display until `resetCommission()` clears
    // it.
    commissionStep: null,
    commissionFailed: false,
    // Code and room of the current attempt. The code in the input field
    // is cleared after a success (it has been used) - without this copy,
    // the progress display would end up with no code to show at the end.
    commissionRunCode: "",
    commissionRunRoom: "",

    // --- Signals (shared with the device view: the same list serves
    // there as the short list of functional signals) ------------------------
    signalsByDevice: {},
    signalsError: null,
    titleDrafts: {},
    rawWriteDrafts: {},
    rawWriteBusyKey: null,
    rawWriteMessages: {},
    // Which signal row has its disclosure open, or null.
    //
    // Unlike the tile menu and the signal groups, this state lives in
    // Alpine instead of in the DOM, and the difference has a reason:
    // there is an open/closed state PER ELEMENT, here exactly ONE value
    // for the whole modal. At most one section is open - with 173 rows,
    // several open disclosures would again be the wall this rework
    // abolishes. This field is reset on the `@close` of the `<dialog>`
    // in index.html, together with `signalsModalDevice` (finding 3 of the
    // 2026-09-07 follow-up review) - otherwise the disclosure would
    // immediately be open again the next time the same device is opened.
    expandedSignalKey: null,
    // The signal modal holds the device ID, NOT the device object:
    // `loadDevices` replaces `devices` entirely, a held-onto
    // object would afterward be a corpse with a stale name and room.
    // `signalsModalDeviceObject()` resolves the ID against the current
    // list each time. This field is reset at EXACTLY ONE place, the
    // `@close` of the `<dialog>` in index.html - see the comment there.
    signalsModalDevice: null,
    // Pure presentation bookkeeping for the modal's backdrop click, NOT
    // modal state like `signalsModalDevice` above - see the
    // `@mousedown.self`/`@click.self` comment on the `<dialog>` in
    // index.html.
    signalsModalBackdropMousedown: false,

    // Like `signalsModalDevice`: only the ID, not the object - see the
    // comment there and `controlModalDeviceObject()`. This field is
    // reset by the same rule at EXACTLY ONE place, the `@close`
    // of the control modal in index.html.
    //
    // Since groups it holds a SUBJECT (see `controlsBySubject`): a number
    // for a device, "g3" for a group. One modal for both, because a group's
    // controls answer in the device shape and are sent with the same call
    // (design 5) - a second dialog would have been the same 140 lines of
    // markup with its own slow drift.
    controlModalDevice: null,
    // Like `signalsModalBackdropMousedown`, only for the control modal.
    controlModalBackdropMousedown: false,

    // --- Settings ---------------------------------------------------
    // `bridgeSettings` is the state last loaded from the server (also read
    // by task 7 and task 9); `settingsDraft` are the three input fields
    // on this tab, adopted only after "Save".
    bridgeSettings: { bridge_ip: null, udp_port: 7000, listen_port: 8080, saved_at: null },
    settingsDraft: { bridge_ip: "", udp_port: 7000, listen_port: 8080 },
    settingsBusy: false,
    settingsError: null,
    resendInterval: { interval_seconds: 300 },
    resendIntervalDraft: 300,
    resendIntervalBusy: false,
    resendIntervalError: null,

    // --- Export --------------------------------------------------------
    exportIncludeSystem: false,
    exportOnlyPending: false,
    exportPreview: null,
    exportStatusByDevice: {},
    exportBusy: false,
    exportError: null,

    // --- System ----------------------------------------------------------
    // Build identity (GET /api/version). `null` as long as the System tab
    // hasn't been opened - the card then shows nothing instead of "undefined".
    versionInfo: null,
    // The update state, exactly as the sidecar writes it to state.json -
    // see api/update.py's `_status()` for the exact shape. `null` until
    // the first `GET /api/update/status` answers.
    updateStatus: null,
    // `GET /api/update/check`'s answer - `target: null` means "up to
    // date" or "checking is disabled" (the two are told apart by
    // `updateStatus.check_enabled`, not by this object's `error` text).
    updateAvailable: null,
    updateConfirming: false,
    updateError: null,
    // The timer runs ONLY while a job is in progress (see
    // `updateRunning()`). Polling continuously would mean a request every
    // two seconds on a Pi for a value that changes maybe ten times a
    // year - the rest of the System tab already gets its live data from
    // the diagnostics socket instead, and this follows the same
    // restraint. Cleared both when the job ends (`loadUpdateStatus`) and
    // when the System tab itself is left (`selectView`) - a timer that
    // only stopped on the first path would keep polling from a tab
    // nobody is looking at.
    updateTimer: null,
    // Set together, by `applyUpdate()` alone, the moment its POST
    // succeeds; cleared together, by `loadUpdateStatus()`, the instant
    // `state.json`'s own `id` finally matches `updateApplyJobId` (see
    // `UPDATE_APPLY_GRACE_MS` for why a match can take a few seconds) -
    // or, if `updateApplyDeadline` passes without a match, by that same
    // function's own deadline check, which is also where `updateApplyMissed`
    // below gets set. `null` means "no apply is currently awaiting pickup":
    // either none was ever made, or the last one was already resolved one
    // way or the other.
    updateApplyJobId: null,

    // The job whose RESULT this page is entitled to announce. Set when
    // this page starts an update, and when a poll first catches one
    // already in flight; compared against `state.json`'s own id before
    // the green "now running" banner renders.
    //
    // Deliberately page-local and deliberately NOT persisted. Nothing
    // ever returns `phase` to `idle` after `done` - the sidecar has no
    // reason to, the file is its record of what last happened - so a
    // banner keyed on the phase alone stands on the System tab forever,
    // across reloads and reboots, until the next update. That is a
    // notice about something that just happened, still being shown
    // weeks later to someone who did not do it.
    //
    // A reload clears this and the banner goes with it, which is the
    // whole point. A socket reconnect does NOT (the bridge restarting
    // mid-update is exactly when the banner must survive), because the
    // page itself never went away.
    //
    // Only the success banner is gated this way. A FAILURE is a standing
    // condition someone still has to deal with, and should still be
    // there after a reload; a success is transient news.
    updateWatchedJobId: null,
    updateApplyDeadline: null,
    // The reactive half of `updateNeverCollected()` (see that method's own
    // comment for why a plain `Date.now()` comparison cannot drive an
    // `x-show` on its own). Written exactly once, by `loadUpdateStatus()`,
    // the instant it notices `updateApplyDeadline` has passed with the job
    // still unclaimed - and cleared by `applyUpdate()` at the start of the
    // NEXT attempt, so a retry does not inherit the previous attempt's
    // banner before its own outcome is known.
    updateApplyMissed: false,
    systemChecks: [],
    systemError: null,
    diagnosticsBusy: false,
    backupError: null,
    // The resync button locks itself while it runs: `resend_all` sends a
    // whole batch of datagrams for many devices, and a second click next
    // to it just triggered a second batch, without the UI showing
    // anything different.
    resyncBusy: false,
    resyncError: null,

    // --- Project file sync (Task 12) ---------------------------------------
    // `plan` carries the complete response from `/api/export/project-sync`
    // unchanged (entries AND the two fully patched files as base64) -
    // `downloadPatchedProject` reads from it instead of making a second
    // call to the bridge for the "Also create new device containers"
    // checkbox. As long as `plan` is `null`, there was no response to see
    // yet - and exactly that is what the download button in `index.html`
    // (`x-show="projectSync.plan"`) hangs on: nothing to download before
    // the plan has been seen.
    // `plan.patched_with_new_devices_base64` is `null` if the uploaded
    // file has no `VirtualInCaption`/`VirtualOutCaption` section
    // (Review-Fix Important #4) - `plan.new_devices_unavailable_reason`
    // then carries the reason. Both stay, like the rest of `plan`,
    // unchanged from the API response, with no dedicated camelCase copy.
    projectSync: {
      // The actual `File` object, not just its name (user request after
      // the review: a selection field instead of typing an IP by hand) -
      // this lets `confirmProjectSyncMiniserver` upload the same file a
      // second time once the user has chosen the Miniserver, with no
      // repeated file dialog.
      file: null,
      plan: null,
      includeNewDevices: false,
      busy: false,
      error: "",
      // If the file carries more than one Miniserver, `/api/export/
      // project-sync` responds to the first attempt (without
      // `miniserver_ip`) with `needs_miniserver_selection=true` instead of
      // a plan - `index.html` then shows this selection field instead of
      // the plan view.
      needsMiniserverSelection: false,
      availableMiniservers: [],
      selectedMiniserverIp: "",
    },

    // --- Live diagnostics (Task 6, Spec 10.5) -------------------------------
    // Three streams, filled by exactly ONE WebSocket
    // (`/api/diagnostics/live`) instead of a one-time GET as before - see
    // `connectDiagnosticsLive`. `datagrams` and `commandLog` were already
    // named this before (previously filled once by `loadSystem()`); the
    // new third kind (`kind: "log"`) is added with this task.
    datagrams: [],
    commandLog: [],
    diagnosticsLogs: [],
    diagnosticsSocket: null,
    diagnosticsConnected: false,
    // Only holds the APPENDING of new lines - not the connection itself
    // (see `handleDiagnosticsMessage`). Deliberately NOT a display filter
    // like `hideNoise`/`logLevel` (design 4 applies to those two, not to
    // this pause): lines arriving during the pause are not caught up on
    // afterwards, like a paused `tail -f`.
    diagnosticsPaused: false,
    diagnosticsReconnectDelayMs: RECONNECT_DELAY_INITIAL_MS,
    diagnosticsReconnectTimer: null,
    // Display filters (design 4): only affect what these two `visible...`
    // functions return - the held lines themselves (`datagrams`,
    // `diagnosticsLogs`) stay unchanged. Turning the filter off therefore
    // shows the existing lines immediately, instead of waiting for new
    // ones.
    hideNoise: true,
    logLevel: "INFO",

    // --- Live connection (Spec 8.3) -----------------------------------------
    liveValues: {},
    socket: null,
    socketConnected: false,
    socketEverConnected: false,
    // Counts unsuccessful attempts of the VERY FIRST connection - stays
    // unused from the first successful connection onward (Review-Fix
    // Minor #4, see `INITIAL_CONNECT_FAILURES_BEFORE_GIVING_UP_ON_SILENCE`
    // above).
    initialConnectFailures: 0,
    reconnectDelayMs: RECONNECT_DELAY_INITIAL_MS,
    reconnectTimer: null,

    // Has Alpine call this exactly ONCE on its own, as soon as
    // `x-data="app()"` is evaluated. `index.html` therefore deliberately
    // carries no `x-init="init()"` (Review-Fix Fix 2, 2026-09-03) - that
    // called the method a second time, and with it (after a logged-in
    // session) `startApp()`: every open tab then held two live
    // connections, of which only the most recently opened one landed in
    // `this.socket`. The other stayed invisible and kept running until
    // the tab was closed.
    async init() {
      // The one-second heartbeat display tick lives here and NOT in
      // `startApp()`: `startApp()` also runs again after a re-login, and
      // a second `setInterval` afterwards could no longer be stopped by
      // anything - exactly the trap this project has already fallen into
      // twice with duplicate live connections. `init()` is guaranteed to
      // call Alpine exactly once. The tick costs nothing as long as no
      // one is logged in: it writes to a field that only the app's header
      // reads.
      //
      // Correction (final review, 2026-09-09): the last sentence is no
      // longer true. Since the "Last heard ..." line, `nowTick` is read
      // by every device tile as well, once a second (`lastHeardText` ->
      // `sinceTextCoarse`). The conclusion holds anyway, for a different
      // reason than the one given: `sinceTextCoarse` returns the SAME
      // string for a whole minute, so Alpine re-evaluates the expression
      // but rewrites no text - which is precisely why that helper exists
      // (see its docstring). The cheap part is no longer "nobody reads
      // it", it is "nothing changes".
      window.setInterval(() => {
        this.nowTick = Date.now();
      }, 1000);
      // The view lives in the address bar - read BEFORE the first load, so
      // `startApp()` below builds the right view straight away instead of
      // first the dashboard and then the desired one.
      this.view = viewFromHash() ?? DEFAULT_VIEW;
      // From here on, every path to a different view goes through the
      // fragment: clicking a tab, the back button, hand-editing the
      // address bar. Alpine calls `init()` exactly once, so the listener
      // is also attached to the window exactly once.
      window.addEventListener("hashchange", () => {
        this.applyHash();
      });
      await Promise.all([this.loadI18n(), this.loadAuthInfo()]);
      if (this.authenticated) {
        await this.startApp();
      }
    },

    // ---------------------------------------------------------------------
    // Access
    // ---------------------------------------------------------------------

    /**
     * The only path this UI takes to `/api`. Passes `requestJson` through
     * unchanged and, along the way, only remembers the one case every
     * caller would otherwise have to handle individually: a 401. It then
     * sets the note above and opens up the input field, instead of
     * burdening the user with fifteen different error messages that all
     * mean the same thing.
     */
    async request(method, path, body) {
      try {
        return await requestJson(method, path, body);
      } catch (error) {
        this.noteAuthError(error);
        throw error;
      }
    },

    /** Like `request`, but for the two file downloads. */
    async download(path, filename) {
      try {
        await requestDownload(path, filename);
      } catch (error) {
        this.noteAuthError(error);
        throw error;
      }
    },

    /** Like `request`, but for the file upload (project file sync). */
    async upload(path, formData) {
      try {
        return await requestUpload(path, formData);
      } catch (error) {
        this.noteAuthError(error);
        throw error;
      }
    },

    /**
     * A 401 mid-operation means: the session has expired or was ended
     * elsewhere. Then back to the login screen - an error message that
     * points at an input field that no longer exists would be worse than
     * none at all.
     */
    noteAuthError(error) {
      if (error instanceof UnauthorizedError) {
        this.authenticated = false;
        this.authError = error.message;
        // This 401 can come from a modal action (saveTitle, toggleExported,
        // toggleResend, writeRaw). Without this call the signal modal
        // would stay open while Alpine switches behind it to the login
        // screen - everything outside the <dialog> would then be inert
        // and neither the password field nor the error banner would be
        // reachable.
        this.closeSignalsModal();
      }
    },

    /** Queries the access state - the first call on every page. */
    async loadAuthInfo() {
      try {
        const info = await requestJson("GET", "/auth-info");
        this.passwordSet = info.password_set;
        this.authenticated = info.authenticated;
        if (!this.authenticated) {
          // If the session drops out here (e.g. a bridge restart while
          // `handleLiveDisconnect` calls this function again), Alpine
          // immediately switches the app to the login screen - but the
          // <dialog> lives outside <template x-if="... && authenticated">
          // (see there) and therefore stays open unless we close it
          // ourselves. An open dialog in front of the login screen makes
          // the password field and error banner unreachable, because
          // everything outside it is inert. closeSignalsModal() is
          // harmless here even when no modal is open at all: close() on
          // an already-closed <dialog> is a no-op.
          this.closeSignalsModal();
        }
        // Clears an older error banner ("The bridge is unreachable" or
        // similar) on success - this function used to run only once per
        // page build, but since `handleLiveDisconnect` it runs again on
        // EVERY connection drop: without this line, a banner from a brief
        // network outage would stay up even after the next request had
        // long since succeeded again (review finding, 2026-09-03).
        this.authError = null;
      } catch (error) {
        this.authError = error.message;
      } finally {
        this.authReady = true;
      }
    },

    /** Loads the current language and the web.* translation table - the
     * first call on every page, like loadAuthInfo(), but independent of
     * it (see init(), which starts both in parallel): GET /api/i18n is
     * unprotected, since the initial-setup/login page needs these texts
     * before anyone has logged in. */
    async loadI18n() {
      try {
        const info = await requestJson("GET", "/api/i18n");
        this.language = info.language;
        translationStrings = info.strings;
        document.documentElement.lang = info.language;
      } catch (error) {
        // The one deliberate exception to "no console.* calls in this
        // file" (see the header comment): there is no UI slot for
        // "Translations could not be loaded" the way there is for
        // authError - without this log, a failure here would be
        // completely invisible. stringsReady is still set regardless (see
        // finally): t() itself falls back to the raw key text for any
        // not-yet-loaded key, instead of blocking the page forever.
        console.error("Translations could not be loaded:", error);
      } finally {
        this.stringsReady = true;
      }
    },

    /**
     * Everything that requires a logged-in session. Separate from `init`,
     * because it has to run a second time after login - then without
     * reloading the page.
     */
    async startApp() {
      // Clear caches and error messages BEFORE anything is reloaded. This
      // method does not only run on the initial build, but also after a
      // re-login - and at that point these fields still hold the state
      // from before the session ended.
      //
      // The per-device caches are the tricky part here (finding from
      // Phase 6, adopted here): on a 401, `loadControls`/`loadSignals`
      // create no entry at all - the cache stays empty, and an empty
      // entry cannot be told apart from "this device has no commands"
      // (see `controlsLoaded`). Without this clearing, a tile would keep
      // showing "no known commands" permanently after a re-login, even
      // though the device does have some, and only reloading the page
      // helped. Exactly the kind of silently wrong state Spec 8.1 wants
      // to rule out.
      this.backupError = null;
      this.resyncError = null;
      this.exportError = null;
      this.deviceActionError = null;
      this.signalsError = null;
      this.settingsError = null;
      this.controlsBySubject = {};
      this.signalsByDevice = {};
      this.groupsError = null;
      this.groupActionError = null;
      // Groups before the fan-out below, because the group controls are
      // loaded per group and that list has to exist first. Sequential with
      // the device list for no deeper reason than that both are cheap.
      await this.loadDevices();
      await this.loadGroups();
      // Every card shows values and controls immediately, with no click
      // needed (device dashboard design, section 3) - that is why
      // startApp() loads both for EVERY device, not just for one after an
      // expand (which no longer exists since this design).
      await Promise.all([
        ...this.devices.map((device) => this.loadControls(device.id)),
        ...this.devices.map((device) => this.loadSignals(device.id)),
        // A group has no signals to load - it has no node to get them
        // from (design 2). Only its commands.
        ...this.groups.map((group) => this.loadGroupControls(group)),
        this.loadExportStatus(),
        this.loadSettings(),
        this.loadResendInterval(),
      ]);
      this.connectLive();
      await this.selectView(this.view);
    },

    async submitSetup() {
      if (this.passwordDraft !== this.passwordRepeatDraft) {
        this.authError = t("web.auth.password_mismatch");
        return;
      }
      await this.submitPassword("/auth/setup");
    },

    async submitLogin() {
      await this.submitPassword("/auth/login");
    },

    /**
     * The shared part of setup and login: submit, show errors, start the
     * app on success. The server sets the cookie, this page never touches
     * it (it is `HttpOnly`).
     *
     * Calls `requestJson` directly, not `this.request`: its 401 case is
     * meant for the currently-logged-in session expiring, not for a
     * failed login itself - `requestJson` knows this (see its path check)
     * and throws the server text ("Wrong password.") here unchanged as an
     * ordinary `Error`.
     */
    async submitPassword(path) {
      this.authBusy = true;
      this.authError = null;
      try {
        await requestJson("POST", path, { password: this.passwordDraft });
      } catch (error) {
        this.authError = error.message;
        if (path === "/auth/setup" && error.status === 409) {
          // This bridge already has a password long since - the screen
          // showed setup anyway, typically because `/auth-info` failed to
          // load and `passwordSet` therefore stayed `false` (see
          // `loadAuthInfo`). Without this switch the page would stay on
          // the setup screen with its takeover warning, and an operator
          // who clicks "Set password" there repeatedly would lock
          // themselves out of LOGIN too via the shared `LoginThrottle` -
          // without ever having entered a wrong password (review finding,
          // 2026-09-03; the second part of the fix is the 409 branch in
          // `api/auth.py`, which since then no longer books a failed
          // attempt for this).
          this.passwordSet = true;
        }
        return;
      } finally {
        this.authBusy = false;
        // In every case: a password does not stay in the page's memory,
        // not even after a failed attempt.
        this.passwordDraft = "";
        this.passwordRepeatDraft = "";
      }
      this.passwordSet = true;
      this.authenticated = true;
      await this.startApp();
    },

    async logout() {
      try {
        await requestJson("POST", "/auth/logout");
      } catch {
        // A failed logout should still log out: the reload below discards
        // any loaded state, and without a valid session the page only
        // gets as far as the login screen anyway.
      }
      window.location.reload();
    },

    // ---------------------------------------------------------------------
    // Navigation
    // ---------------------------------------------------------------------

    /**
     * The way back from the address bar into the application: attached to
     * `hashchange` (see `init`), and thus runs on every tab click, every
     * back button, and every hand-edited address.
     */
    async applyHash() {
      const view = viewFromHash();
      if (view === null) {
        // Nothing valid in the address bar: the shown view stays put and
        // is only added back in, instead of silently jumping to the
        // dashboard.
        writeHash(this.view);
        return;
      }
      if (!this.authenticated) {
        // There is nothing to load before login - every request would run
        // into a 401. Just remember the view; `startApp()` builds it after
        // login.
        this.view = view;
        return;
      }
      if (view === this.view) {
        // `selectView` writes the fragment itself; the `hashchange` it
        // triggers lands here and must not load the view a second time.
        return;
      }
      await this.selectView(view);
    },

    async selectView(view) {
      this.view = view;
      writeHash(view);
      // Exactly ONE diagnostics connection, open only while "System" is
      // actually the active view (trap 3, Task 6): it opens on switching
      // TO "system" and closes on every other value - the same pattern as
      // `connectLive()`/its cleanup for the value channel, just tied to
      // the view instead of the login.
      if (view === "system") {
        this.connectDiagnosticsLive();
      } else {
        this.disconnectDiagnosticsLive();
        // Same reasoning as the diagnostics socket right above: a job in
        // progress is followed only while "System" is the open tab. Without
        // this, leaving the tab mid-update would keep polling every two
        // seconds from a card nobody can see - the exact leak this task's
        // self-review calls out. Re-entering the tab restarts it, since
        // `loadSystem()` (below) calls `loadUpdateStatus()` again.
        this.stopUpdateTimer();
      }
      if (view === "export") {
        await this.loadExportStatus();
      } else if (view === "system") {
        await this.loadSystem();
      } else if (view === "settings") {
        await this.loadSettings();
      }
    },

    // ---------------------------------------------------------------------
    // Devices
    // ---------------------------------------------------------------------

    async loadDevices() {
      this.devicesError = null;
      try {
        this.devices = await this.request("GET", "/api/devices");
      } catch (error) {
        this.devicesError = t("web.devices.list_load_error", { message: error.message });
      }
    },

    // Online status preferably from the live WebSocket (its value may
    // have changed since the list was last loaded, see Spec 8.3) - if it
    // is missing (no message for this device has arrived yet), the state
    // most recently loaded from `GET /api/devices` applies.
    // NO `hasOwnProperty` here (2026-09-03). Alpine's reactivity only
    // captures a dependency on a genuine property ACCESS;
    // `Object.prototype.hasOwnProperty.call(obj, key)` bypasses that.
    // Consequence in the shipped version: the first message for a key
    // `liveValues` did not yet know did NOT change the display - only a
    // later redraw for some other reason caught it up. From the outside
    // this looked as if nothing was arriving over the live connection,
    // even though the value had long since been in the state. A direct
    // access with `=== undefined`, by contrast, is captured.
    isOnline(device) {
      const liveKey = `d${device.id}_online`;
      const live = this.liveValues[liveKey];
      if (live !== undefined) {
        return Boolean(live);
      }
      return device.online;
    },

    async loadControls(deviceId) {
      try {
        this.controlsBySubject[deviceId] = await this.request(
          "GET",
          `/api/devices/${deviceId}/controls`,
        );
      } catch (error) {
        this.deviceActionError = t("web.devices.controls_load_error", { message: error.message });
      }
    },

    // --- Groups: loading ----------------------------------------------------

    /** A group's key into `controlsBySubject`. Deliberately the very
     * prefix the server builds its group command keys from (`g3_on`,
     * design 4.1), so the two never need translating into each other -
     * and so it can never be mistaken for a device id, which is a
     * number. */
    groupSubject(group) {
      return `g${group.id}`;
    },

    async loadGroups() {
      this.groupsError = null;
      try {
        this.groups = await this.request("GET", "/api/groups");
      } catch (error) {
        this.groupsError = t("web.groups.list_load_error", { message: error.message });
      }
    },

    /** Reloaded after every membership change, not only on startup: the
     * command list of a group is the INTERSECTION of its members'
     * commands and is recomputed server-side on every change (design
     * 4.3), so a tile that kept the old list would offer a command the
     * group no longer has - and that key now answers 404. */
    async loadGroupControls(group) {
      try {
        this.controlsBySubject[this.groupSubject(group)] = await this.request(
          "GET",
          `/api/groups/${group.id}/controls`,
        );
      } catch (error) {
        this.groupActionError = t("web.devices.controls_load_error", { message: error.message });
      }
    },

    async loadAllGroupControls() {
      await Promise.all(this.groups.map((group) => this.loadGroupControls(group)));
    },

    // --- Reading controls, for a device and for a group alike ---------------
    //
    // Every helper from here down to `hasColourTabs` takes a SUBJECT, not
    // a device: a numeric device id, or a group's `"g3"` (see
    // `groupSubject`). None of them had to change for groups - they only
    // ever read `commands`, `hidden_raw_commands` and `control`, and
    // `GET /api/groups/{id}/controls` answers with exactly the shape
    // `GET /api/devices/{id}/controls` does (design 5). A second set of
    // group-only copies would have been two lists of command kinds to keep
    // in step.

    controlsFor(subject) {
      return this.controlsBySubject[subject] || null;
    },

    /**
     * Whether this subject's controls could be loaded at all. Without this
     * distinction the UI renders a failed (or still running) fetch as
     * "no known commands" - a statement about the device, when what is
     * really due is one about the connection (Spec 8.1: a failure must
     * not appear as a harmless state). `index.html` uses this to tell
     * "not loaded yet/failed" (`web.devices.controls_loading`) apart from
     * "loaded, but genuinely no commands"
     * (`web.devices.no_known_commands`) - the error text itself still
     * runs via `deviceActionError` (see `loadControls`); this is only
     * about avoiding the wrong tile display.
     */
    controlsLoaded(subject) {
      // Direct access, no `hasOwnProperty` - see `isOnline`.
      return this.controlsBySubject[subject] !== undefined;
    },

    // The following three helpers exist solely so `index.html` does not
    // need an optional chaining operator (`?.`) in an Alpine expression to
    // handle an entry that has not been loaded yet - an ordinary function
    // is more readable here than an expression with a built-in existence
    // check in the middle of the markup.
    commandsFor(subject) {
      const controls = this.controlsBySubject[subject];
      return controls ? controls.commands : [];
    },

    hiddenRawCommandsFor(subject) {
      const controls = this.controlsBySubject[subject];
      return controls ? controls.hidden_raw_commands : 0;
    },

    /** All commands of a device or group with exactly this control kind
     * (`CommandOut.control`: "none", "percent", "kelvin", "hue_sat",
     * "unknown"). Decides WHICH control gets built - see the design
     * rule for that in the control modal in index.html: `command.slug`
     * only serves as a label there, never as a case distinction. */
    controlsByKind(subject, kind) {
      return this.commandsFor(subject).filter((command) => command.control === kind);
    },

    /** Commands for which the shipped UI knows NO control of its
     * own - the fallback to the plain number field in the modal. Catches
     * not only the literal wording "unknown" (that was the bug in
     * finding I-2), but every `control` value outside
     * `KNOWN_CONTROL_KINDS`: if someone later enters e.g. `control: xy` in
     * `clusters.yaml`, the API passes `control: "xy"` through unchanged,
     * and without this catch-all the command in the modal would simply
     * not be drawn at all - no slider, no number field, no
     * hint. */
    unhandledControls(subject) {
      return this.commandsFor(subject).filter(
        (command) => !KNOWN_CONTROL_KINDS.includes(command.control),
      );
    },

    /** Whether this device or group can do anything value-carrying at all
     * - only then does the tile get the "Control" button to the control
     * modal. */
    hasAdjustableControls(subject) {
      return this.commandsFor(subject).some((command) => command.control !== "none");
    },

    /** Tabs only if the subject can do BOTH paths. A CCT light thereby
     * gets no tab bar - without a single check on device type or
     * model (design 2026-09-07, section 6.3). */
    hasColourTabs(subject) {
      return (
        this.controlsByKind(subject, "kelvin").length > 0 &&
        this.controlsByKind(subject, "hue_sat").length > 0
      );
    },

    exportedAtFor(deviceId) {
      const status = this.exportStatusFor(deviceId);
      return status ? status.exported_at : null;
    },

    // Like `ExportStatusOut.changed_since_export` server-side: without a
    // loaded status (e.g. a device that was just commissioned, before the
    // next `loadExportStatus` round has gone through), "changed" applies -
    // the same cautious assumption as on the server (see api/export.py,
    // `_changed_since_export`).
    changedSinceExport(deviceId) {
      const status = this.exportStatusFor(deviceId);
      return status ? status.changed_since_export : true;
    },

    exportHintFor(deviceId) {
      const status = this.exportStatusFor(deviceId);
      if (!status || !status.exported_at) {
        return t("web.devices.export_never");
      }
      return t("web.devices.export_last", { timestamp: this.formatTimestamp(status.exported_at) });
    },

    // Classes for the tile's color stripe (style.css, `.device-card`) - a
    // function instead of an inline expression in index.html, because two
    // conditions (online AND changed) come together here.
    deviceCardClass(device) {
      return {
        "is-offline": !this.isOnline(device),
        "is-changed": this.isOnline(device) && this.changedSinceExport(device.id),
      };
    },

    // Short list for the device view: only the functional signals
    // (`signal.functional`, from `profiles.relevance.is_functional` -
    // Task 8), and only the first few of those - the full tree (including
    // the expert block) lives in the signal modal. The cap still applies
    // even though the functional set is small for the two devices known
    // so far (5 and 17 respectively): a device with more functional
    // signals than fit here is not excluded by this rule.
    //
    // These three helpers used to be called `exportableSignalsFor`/
    // `firstSignalsFor`/`remainingSignalCount`, filtered on `exportable`
    // instead of `functional`, with the heading next to it reading
    // "Signals (start of the list)" (Review-Fix Fix 9, 2026-09-03):
    // `exportable` only answers whether a value TECHNICALLY fits a Loxone
    // input, not whether anyone WANTS it - a plug socket has 110
    // exportable signals, including network and device details, but only
    // 5 functional ones. Since `signal.functional` answers that directly
    // (instead of a guessed order), the short list can be honestly named
    // again.
    FUNCTIONAL_PREVIEW_LIMIT: 6,

    functionalSignalsFor(deviceId) {
      const signals = this.signalsByDevice[deviceId];
      return signals ? signals.filter((signal) => signal.functional) : [];
    },

    // The cluster by which the tile recognizes the battery level. The
    // number stands here instead of a title check: the title can be
    // freely renamed by the user ("Battery", "Juice"), the cluster cannot.
    POWER_SOURCE_CLUSTER: 47,

    // The device's battery level, or null. Since the cluster rank list
    // (design 2026-09-07, section 6) it gets its own footer line: with
    // rank 90 it sits behind all sixteen other functional signals of the
    // button and would thereby drop out of the six preview rows - it
    // would no longer be visible on the tile at all. That is the price of
    // the rank list, and this is the offsetting entry.
    //
    // With several PowerSource signals (a composite device, or a bridge
    // with two batteries under one record), `.find()` deliberately
    // returns only ONE - the top-ranked one, deterministically, because
    // the list arrives sorted. The tile shows only one footer line
    // anyway, more would be no gain there. `previewSignalsFor` does NOT
    // rely on this one signal for that, though, but excludes the whole
    // cluster - otherwise a second PowerSource signal would sneak back
    // into the value grid through the back door.
    batterySignalFor(deviceId) {
      const signals = this.signalsByDevice[deviceId];
      if (!signals) {
        return null;
      }
      return (
        signals.find(
          (signal) => signal.functional && signal.cluster_id === this.POWER_SOURCE_CLUSTER,
        ) || null
      );
    },

    // The functional signals WITHOUT the battery level - the set from
    // which the preview rows and the "+ N more" counter are built.
    //
    // **The reasoning changed during the merge, the rule did not.**
    // Originally this filter kept the battery out of the PRIMARY SIGNAL
    // (design 2026-09-07): with rank 90 it stood at the front in the
    // header line, even though a button is not named after its battery
    // level. The primary signal no longer exists since the design
    // "device tile without a primary signal" - all signals sit at equal
    // rank in the value grid. The filter is nonetheless still needed, for
    // the opposite reason: the rank list pushes the battery to the END,
    // and on a device with more than six functional signals (the button
    // has seventeen) it thereby drops out of the preview rows. Without
    // its own footer line the battery level would no longer be visible on
    // the tile at all - on a device that dies on exactly that.
    //
    // That the preview and the counter come from the SAME set remains
    // the trick: the counter can never count the battery twice (it is
    // missing from both terms). A special rule in two places would be the
    // same statement twice - and in the first design one of them was
    // forgotten ("+ 11 more" on a tile that showed seven of 17 signals).
    //
    // Filtering happens by cluster, not by the key from
    // `batterySignalFor`: with two PowerSource signals that returns only
    // the first, a key comparison would leave the second in the value
    // grid - and at its very start, because the rank list ranks both the
    // same.
    previewSignalsFor(deviceId) {
      return this.functionalSignalsFor(deviceId).filter(
        (signal) => signal.cluster_id !== this.POWER_SOURCE_CLUSTER,
      );
    },

    firstSignalsFor(deviceId) {
      return this.previewSignalsFor(deviceId).slice(0, this.FUNCTIONAL_PREVIEW_LIMIT);
    },

    remainingSignalCount(deviceId) {
      return Math.max(
        0,
        this.previewSignalsFor(deviceId).length - this.FUNCTIONAL_PREVIEW_LIMIT,
      );
    },

    // --- Category, rooms, sorting --------------------------------------------

    // The translated name of the category. The API only returns the
    // identifier ("socket"), so the search below can compare against the
    // text the operator actually sees - in German "Steckdose", in English
    // "socket".
    //
    // Serves a GROUP unchanged: a group carries `category` under the same
    // name and with the same vocabulary (`profiles.categories`), because
    // its category is the one its first member fixed (design 2).
    categoryLabel(device) {
      return t("web.devices.category." + (device.category || "other"));
    },

    // A device's room in the encoding of `roomFilter`: "" instead of
    // null/undefined. One place, so the conversion does not live
    // separately in four helpers, one of which might eventually do it
    // differently.
    //
    // Serves a GROUP unchanged too, and that is not a coincidence worth
    // papering over with a second helper: a group's room is its OWN
    // column, free text with NULL for "no room", exactly like a device's
    // (design 4.1). It is deliberately NOT derived from the members - a
    // derived room would move a group on its own the moment one lamp is
    // re-roomed, and nobody would learn why (design 6).
    roomKeyOf(device) {
      return device.room || "";
    },

    // All rooms with their count, "No room" right at the end. `key` is the
    // value `roomFilter` takes on ("" for no room), `label` the displayed
    // text.
    //
    // Groups count towards a chip just as devices do: the grid below shows
    // both, and a chip whose number disagreed with what appears under it
    // would be worse than no number at all.
    roomChips() {
      const counts = new Map();
      for (const subject of [...this.devices, ...this.groups]) {
        const key = this.roomKeyOf(subject);
        counts.set(key, (counts.get(key) || 0) + 1);
      }
      const chips = [...counts.keys()]
        .filter((key) => key !== "")
        .sort((a, b) => a.localeCompare(b))
        .map((key) => ({ key, label: key, count: counts.get(key) }));
      if (counts.has("")) {
        chips.push({ key: "", label: t("web.devices.room_none"), count: counts.get("") });
      }
      return chips;
    },

    // The bar does not show at all as long as not a single device or group
    // carries a room: with three devices and no room it would be a line of
    // noise above a list that fits in one glance anyway.
    hasAnyRoom() {
      return [...this.devices, ...this.groups].some((subject) => Boolean(subject.room));
    },

    // Does the search term match this device? Compared against name,
    // translated category name, and room name. A GROUP goes through here
    // unchanged - it carries all three fields under the same names, which
    // is exactly why `visibleGroups()` below does not reimplement the
    // predicate (design 6: same search as the device tile).
    matchesSearch(device) {
      const needle = this.deviceSearch.trim().toLocaleLowerCase();
      if (!needle) {
        return true;
      }
      const haystack = [device.label, this.categoryLabel(device), this.roomKeyOf(device)]
        .join(" ")
        .toLocaleLowerCase();
      return haystack.includes(needle);
    },

    // The visible devices: room chip and search field act TOGETHER (AND).
    // A search therefore only applies within the selected room - the case
    // of "no match here, but next door" is caught by `hitsOutsideRoom()`
    // below.
    visibleDevices() {
      return this.devices.filter(
        (device) =>
          (this.roomFilter === null || this.roomKeyOf(device) === this.roomFilter) &&
          this.matchesSearch(device),
      );
    },

    // The visible groups: the SAME predicate as `visibleDevices()`, on the
    // other list. Deliberately not a second implementation of it - the two
    // halves of one grid must not start filtering differently, and
    // `roomKeyOf`/`matchesSearch` already serve a group unchanged (see
    // their comments).
    visibleGroups() {
      return this.groups.filter(
        (group) =>
          (this.roomFilter === null || this.roomKeyOf(group) === this.roomFilter) &&
          this.matchesSearch(group),
      );
    },

    // How many devices AND groups the search term matches OUTSIDE the
    // selected room. Only relevant when nothing is left within the room
    // itself - otherwise the note would be a distraction. Groups belong in
    // this count for the same reason they belong in the chip count: the
    // "show all rooms" link it offers reveals both.
    hitsOutsideRoom() {
      if (this.roomFilter === null || !this.deviceSearch.trim()) {
        return 0;
      }
      return [...this.devices, ...this.groups].filter(
        (subject) => this.roomKeyOf(subject) !== this.roomFilter && this.matchesSearch(subject),
      ).length;
    },

    clearRoomFilter() {
      this.roomFilter = null;
    },

    // Finding 2 (2026-09-05): falls back to "All" if the room currently
    // being filtered on has disappeared through a write - the last device
    // of a room moved to another room via the tile menu, or the room
    // renamed/merged. Without this, `roomFilter` stayed on a name
    // `roomChips()` no longer returns: no tile visible anymore, no chip
    // active anymore, and the rename pencil (which only hangs off
    // `roomFilter`) still there but pointing at nothing (404 on click).
    //
    // For "All" (`null`) there is nothing to do - nothing is filtered
    // there anyway, `roomChips()` knows no chip at all for this value
    // (see there), so the `some(...)` call below would never find a match
    // and would just harmlessly set `null` again.
    //
    // "No room" (`""`) MUST run through the rest of the method (finding
    // 2, re-review 2026-09-05, tightens the original version of this fix):
    // that is the filter in which someone assigns one unsorted device
    // after another to a room, and that is exactly what makes the
    // "No room" chip disappear from `roomChips()` once the last
    // device-without-a-room has been given one - `counts.has("")` then
    // becomes false (see there). Without this fix, `roomFilter` stayed on
    // `""`: no chip active anymore, no tile visible anymore, a misleading
    // empty-state note, no way out link. The `roomChips().some(...)`
    // comparison below already handles `""` correctly, with no special
    // case at all - it simply returns `false` for `""` once no device is
    // left without a room.
    //
    // One place instead of at each write site individually (`saveRoom`,
    // `commitRenameRoom`) - both call this instead of duplicating the
    // check.
    reconcileRoomFilter() {
      if (this.roomFilter === null) {
        return;
      }
      if (!this.roomChips().some((chip) => chip.key === this.roomFilter)) {
        this.roomFilter = null;
      }
    },

    // The devices, grouped by room and sorted within a room: first by
    // category rank (all plug sockets together, then all pushbuttons),
    // then alphabetically by name within that.
    //
    // NOT related to the device GROUPS of the 2026-09-10 design, despite
    // the name: a "group" here is a room section of the device grid, and
    // the `group` in the `x-for` over this function in index.html is one
    // of those. The device groups live in `groups`/`visibleGroups()` and
    // render as tiles of their own. The two names met by accident; this
    // one is the older, and renaming it would touch every room test.
    //
    // `localeCompare` instead of `<`: otherwise "Émile" would land
    // behind "Zurich", because the code point of "É" comes after that of
    // "Z".
    //
    // With a selected room, exactly one group results, and its `title`
    // stays empty - there is nothing to distinguish, and a heading above
    // the single group would duplicate the chip bar.
    deviceGroups() {
      const byRoom = new Map();
      for (const device of this.visibleDevices()) {
        const key = this.roomKeyOf(device);
        if (!byRoom.has(key)) {
          byRoom.set(key, []);
        }
        byRoom.get(key).push(device);
      }
      const sortDevices = (devices) =>
        [...devices].sort(
          (a, b) =>
            (a.category_rank ?? 99) - (b.category_rank ?? 99) ||
            a.label.localeCompare(b.label),
        );
      const groups = [...byRoom.keys()]
        .filter((key) => key !== "")
        .sort((a, b) => a.localeCompare(b))
        .map((key) => ({ key, title: key, devices: sortDevices(byRoom.get(key)) }));
      if (byRoom.has("")) {
        groups.push({
          key: "",
          title: t("web.devices.room_none"),
          devices: sortDevices(byRoom.get("")),
        });
      }
      // With a selected room there is only one group - its heading would
      // duplicate the active chip right above it.
      if (this.roomFilter !== null) {
        return groups.map((group) => ({ ...group, title: "" }));
      }
      return groups;
    },

    // --- Change a device's room ---------------------------------------

    // Sends ONLY the room. A `label` sent along with it would run
    // `rename_device` and set `updated_at` - the device would afterwards
    // show as "changed since export" even though the room does not end up
    // in any template (design 3.3).
    //
    // `value` is already in the same encoding as `roomFilter`: "" means
    // "no room", and that is exactly what the API also expects for
    // "remove room". No conversion at this point.
    //
    // Shared with `saveGroupRoom` - the two used to be near-verbatim
    // copies (collection segment, error field, that is genuinely all that
    // differs), and the copy had already drifted once: `saveGroupRoom`
    // re-added its OWN `finally { reconcileRoomFilter() }` instead of
    // reusing this one (review finding, 2026-09-11). One implementation
    // means the next fix to this write path only has to be made here.
    // `collection` is the URL segment ("devices" or "groups"), `errorField`
    // the state property a failure is shown through (`deviceActionError`
    // or `groupActionError`) - both callers pass their own literal
    // strings, not a value computed from `entity`, so this stays a
    // two-line diff to read at each call site.
    async saveEntityRoom(entity, collection, value, errorField) {
      this[errorField] = null;
      try {
        const updated = await this.request("PATCH", `/api/${collection}/${entity.id}`, {
          room: value,
        });
        Object.assign(entity, updated);
      } catch (error) {
        this[errorField] = t("web.devices.room_save_error", { message: error.message });
      } finally {
        // Even on failure: if a failed write leaves a room empty, the
        // filter must not stay stuck on a room that no longer exists.
        // Applies to both kinds of tile - `roomChips()` counts groups too.
        this.reconcileRoomFilter();
      }
    },

    async saveRoom(device, value) {
      await this.saveEntityRoom(device, "devices", value, "deviceActionError");
    },

    // Finding 3 (review from 2026-09-05): a native `<details>` does not
    // return focus when closing - the entry the user just activated
    // disappears from the rendered tree along with its focus (only the
    // content behind `<summary>` gets hidden, see index.html), and Tab
    // afterwards starts over again at the very top of the document. `el`
    // is any element WITHIN the menu - an entry, the input field, or the
    // `<details>` itself for the outside-click/Escape guards -
    // `closest("details")` finds the same element in every case.
    // `<summary>` always stays rendered when closing, so it is always a
    // valid focus target. One function instead of a separate focus call
    // at each of the five close handlers in index.html - exactly the
    // repetition finding 1 already got wrong once for `newRoomFor`.
    //
    // Finding 1 (re-review 2026-09-05): the unconditional focus call above
    // hit not only the actual close paths, but also Alpine's `outside`
    // listener, which sits on `document` in the bubble phase - i.e. AFTER
    // the browser has already focused the clicked outside entry point on
    // mousedown. Measured in a real browser: with the tile menu open,
    // clicked into the search field, `document.activeElement` was
    // afterwards `SUMMARY` instead of the search field - the keyboard went
    // to a `<summary>` somewhere in the device grid, and the user would
    // have had to click a second time. The same applies to `.window`
    // Escape: pressing Escape while typing in a completely unrelated field
    // yanks focus to an open kebab elsewhere, and because `focus()`
    // scrolls its target node into view, the page even jumps back to that
    // tile. `hadFocus` records whether focus was even inside the menu
    // BEFORE closing (keyboard operation: an entry activated, Escape in
    // the field/menu) - only then may `closeTileMenu` redirect it. On an
    // outside click it is never inside the menu, the call is skipped, and
    // the browser leaves the targeted element alone. `preventScroll`
    // additionally prevents the jump-back for the remaining, genuinely
    // legitimate case. Do NOT simplify this guard - without it the
    // regression case (click/Escape outside) is back.
    //
    // Finding 4 (re-review 2026-09-05): `closest("details")` returns
    // `null` if `el` (contrary to the assumption above) is ever OUTSIDE a
    // `<details>` - every call today upholds this assumption, but a
    // `TypeError` on dereferencing would silently swallow the rest of the
    // inline expression that `closeTileMenu` sits in. In two places in
    // index.html, `saveRoom(...)` follows in the SAME line - a throw here
    // would discard the user's room assignment without a trace, instead of
    // just missing the (otherwise superfluous) focus call. `?.` makes the
    // failure harmless instead of dragging the sibling call down with it.
    closeTileMenu(el) {
      const menu = el.closest("details");
      const hadFocus = menu?.contains(document.activeElement);
      if (menu) menu.open = false;
      if (hadFocus) menu.querySelector("summary").focus({ preventScroll: true });
    },

    // Shared with `beginNewGroupRoom`/`commitNewGroupRoom`: `key` is a
    // device id for a device tile, a group's SUBJECT string ("g3") for a
    // group tile (see `beginNewGroupRoom`'s own comment for why a bare
    // group id would not do - `newRoomFor` is the one field both kinds of
    // tile share).
    beginNewRoomAt(key) {
      this.newRoomFor = key;
      this.newRoomDraft = "";
    },

    beginNewRoom(device) {
      this.beginNewRoomAt(device.id);
    },

    // Shared with `commitNewGroupRoom`: reads the typed name and closes
    // the field immediately either way, then saves through whichever of
    // `saveRoom`/`saveGroupRoom` the caller binds as `save` - so this
    // function does not itself need to know which kind of tile it runs
    // for.
    async commitNewRoomWith(save) {
      const name = this.newRoomDraft.trim();
      this.newRoomFor = null;
      this.newRoomDraft = "";
      if (name) {
        await save(name);
      }
    },

    async commitNewRoom(device) {
      await this.commitNewRoomWith((name) => this.saveRoom(device, name));
    },

    // Renaming happens INLINE, like every other edit in this UI (device
    // name, signal title): the pencil turns the heading into an input
    // field. A `window.prompt` would have been less markup, but would
    // look different in every browser and would be the only dialog in a
    // view that otherwise does without.
    beginRenameRoom(room) {
      this.renamingRoom = room;
      this.renameDraft = room;
    },

    cancelRenameRoom() {
      this.renamingRoom = null;
      this.renameDraft = "";
    },

    // The confirmation before merging, by contrast, stays a native
    // dialog - the one deliberate difference from renaming itself.
    // Merging is rare and irreversible: afterwards no one knows anymore
    // which device used to be in which of the two rooms. A modal dialog
    // is the honest brake for exactly this kind of action; a banner you
    // can dismiss without having read it would not be.
    //
    // Whether the target name is already taken is decided by the UI, not
    // the server: it already has the device list, a second request just
    // for this piece of information would be superfluous.
    async commitRenameRoom() {
      const room = this.renamingRoom;
      if (room === null) {
        // Enter already saved and closed the field; the subsequent `blur`
        // lands here and has nothing left to do.
        return;
      }
      const name = this.renameDraft.trim();
      if (!name || name === room) {
        this.cancelRenameRoom();
        return;
      }
      const exists = this.devices.some((device) => device.room === name);
      if (exists && !window.confirm(t("web.devices.room_rename_merge_confirm"))) {
        // Field stays open: declining the confirmation means "not like
        // this", not "forget what I typed".
        return;
      }
      this.cancelRenameRoom();
      this.deviceActionError = null;
      try {
        await this.request("POST", "/api/rooms/rename", { from: room, to: name });
        if (this.roomFilter === room) {
          this.roomFilter = name;
        }
        await this.loadDevices();
        // Normally a no-op (thanks to the line above, the filter already
        // points at `name`) - but kicks in if a merge resulted in no
        // device carrying `name` at all in the end (see
        // `reconcileRoomFilter`, finding 2).
        this.reconcileRoomFilter();
      } catch (error) {
        this.deviceActionError = t("web.devices.room_rename_error", { message: error.message });
      }
    },

    // ---------------------------------------------------------------------
    // Groups: renaming, room, deleting, and the create/edit dialog
    // (design 2026-09-10, section 6)
    // ---------------------------------------------------------------------

    /** Renamed INLINE through the tile's name field, exactly like a
     * device's (`saveLabel`) - no kebab entry and no dialog for it. The
     * group tile is the device tile, and an edit that works one way on one
     * tile and another way on its neighbour would be the gratuitous
     * difference the design warns against. Thin wrapper around the shared
     * `saveEntityLabel` (see there, next to `saveLabel`) - the group and
     * device paths differ only in the draft map, the collection segment,
     * and which field carries a failure. */
    async saveGroupLabel(group) {
      await this.saveEntityLabel(group, this.groupLabelDrafts, "groups", "groupActionError");
    },

    /** The group's own room, same encoding as a device's: "" is the value
     * the API takes for "remove the room" (design 4.1). Thin wrapper around
     * the shared `saveEntityRoom` (see there, next to `saveRoom`). */
    async saveGroupRoom(group, value) {
      await this.saveEntityRoom(group, "groups", value, "groupActionError");
    },

    /** `newRoomFor` holds a device id for a device tile and a group's
     * SUBJECT string ("g3") for a group tile - never a bare group id. Both
     * kinds of tile share the one field (only one new-room text box may be
     * open at a time, see index.html), and a group id and a device id are
     * both small integers: with bare ids, opening the box on group 4 would
     * open it on device 4 as well. The subject string cannot collide,
     * because every comparison in the markup uses `===`. Thin wrapper
     * around the shared `beginNewRoomAt` (see there, next to
     * `beginNewRoom`). */
    beginNewGroupRoom(group) {
      this.beginNewRoomAt(this.groupSubject(group));
    },

    /** Thin wrapper around the shared `commitNewRoomWith` (see there, next
     * to `commitNewRoom`), bound to `saveGroupRoom` instead of `saveRoom`. */
    async commitNewGroupRoom(group) {
      await this.commitNewRoomWith((name) => this.saveGroupRoom(group, name));
    },

    /** The confirmation names what stops resolving afterwards: the group's
     * keys go with it, so a virtual output in an already patched Loxone
     * project points at a key that answers 404 from then on (design 4.3).
     * Same stance as `removeDevice` - the text names the key prefix and the
     * template file up to the id, not the full file name, whose second
     * half comes from a normalisation rule that lives in Python
     * (`export.documents.filename_for`) and must not be reimplemented
     * here. */
    async removeGroup(group) {
      const confirmed = window.confirm(
        t("web.groups.delete_confirm", { label: group.label, id: group.id }),
      );
      if (!confirmed) {
        return;
      }
      this.groupActionError = null;
      try {
        await this.request("DELETE", `/api/groups/${group.id}`);
        this.groups = this.groups.filter((entry) => entry.id !== group.id);
        delete this.controlsBySubject[this.groupSubject(group)];
        // A control modal standing open over the group that just went away
        // would become an empty box behind the `x-if` guard - the same
        // reasoning as in `removeDevice`, and checked against this group so
        // a modal over a DIFFERENT subject does not close with it.
        if (this.controlModalDevice === this.groupSubject(group)) {
          this.closeControlModal();
        }
        this.reconcileRoomFilter();
      } catch (error) {
        // NOT `web.devices.remove_error` ("Could not remove device") - that
        // text was a copy-paste leftover from `removeDevice` and told the
        // user a DEVICE could not be removed on a failed GROUP delete, in
        // both languages (review finding, 2026-09-11).
        this.groupActionError = t("web.groups.delete_error", { message: error.message });
      }
    },

    // --- The create/edit dialog ---------------------------------------------

    openGroupCreate() {
      this.groupDraft = { id: null, label: "", room: "", memberIds: [], roomTouched: false };
      this.groupDialogError = null;
      // `$nextTick` for the same reason as in `openSignalsModal`:
      // `showModal()` puts the initial focus on the first focusable
      // element IN the dialog, and with `x-if` content that element does
      // not exist until Alpine has built it.
      this.$nextTick(() => this.$refs.groupDialog.showModal());
    },

    /** Editing touches the MEMBERS only. Name and room are edited on the
     * tile itself (the name field, the kebab's room list), so the dialog
     * does not offer a second way to do the same thing. */
    openGroupMembers(group) {
      this.groupDraft = {
        id: group.id,
        label: group.label,
        room: group.room || "",
        memberIds: [...group.member_ids],
        roomTouched: true,
      };
      this.groupDialogError = null;
      this.$nextTick(() => this.$refs.groupDialog.showModal());
    },

    /** Closes via `close()`, so the `@close` handler in index.html stays
     * the one place that resets the draft - the same rule the two modals
     * above follow. */
    closeGroupDialog() {
      this.$refs.groupDialog.close();
    },

    /** Every device, by name, regardless of the room filter and the search
     * term: a group may well span two rooms, and a candidate list that
     * silently obeyed the filter behind the dialog would hide exactly the
     * lamp someone opened the dialog to add. */
    groupCandidates() {
      return [...this.devices].sort((a, b) => a.label.localeCompare(b.label));
    },

    /**
     * The category the draft is locked to, or null while it is still open.
     *
     * When EDITING, that is the group's own stored category, not the first
     * member's: the category is stored precisely so that an empty group
     * still knows what it accepts (design 2), and the server refuses a
     * foreign member for an emptied group just as it does for a full one.
     * Reading the first draft member instead would unlock every category
     * the moment someone unticks all of them, and the refusal would then
     * arrive as a 400 from the server instead of as a greyed-out entry.
     */
    groupDraftCategory() {
      if (this.groupDraft.id !== null) {
        const group = this.groups.find((entry) => entry.id === this.groupDraft.id);
        if (group) {
          return group.category;
        }
      }
      const first = this.devices.find((device) => device.id === this.groupDraft.memberIds[0]);
      return first ? first.category : null;
    },

    /** Whether this device can be ticked, and if not, why. The first pick
     * fixes the category; everything of another kind is then disabled WITH
     * the reason on the entry, never silently (design 6) - a tick that does
     * nothing and says nothing is the failure Spec 8.1 is about. */
    groupCandidateState(device) {
      const category = this.groupDraftCategory();
      if (category === null || category === device.category) {
        return { disabled: false, reason: "" };
      }
      return {
        disabled: true,
        reason: t("web.groups.other_category", { category: this.categoryLabel(device) }),
      };
    },

    toggleGroupMember(device) {
      if (this.groupCandidateState(device).disabled) {
        return;
      }
      const ids = this.groupDraft.memberIds;
      this.groupDraft.memberIds = ids.includes(device.id)
        ? ids.filter((id) => id !== device.id)
        : [...ids, device.id];
      // Prefilled from the members while they agree on a room, and the
      // user's from the first keystroke in the field on (design 6).
      if (!this.groupDraft.roomTouched) {
        this.groupDraft.room = this.groupRoomSuggestion();
      }
    },

    /** The members' room, if they all carry the same one - otherwise
     * nothing. Guessing a majority room would put the group somewhere none
     * of its members is, and the field is right there to be filled in. */
    groupRoomSuggestion() {
      const rooms = new Set(
        this.groupDraft.memberIds
          .map((id) => this.devices.find((device) => device.id === id))
          .filter(Boolean)
          .map((device) => this.roomKeyOf(device)),
      );
      return rooms.size === 1 ? [...rooms][0] : "";
    },

    /** Create, or replace the member list of an existing group.
     *
     * The member list goes out as a WHOLE (`PUT`), not as add/remove per
     * device: the command intersection is recomputed after every change
     * anyway, and two single removals would pass through an intermediate
     * state nobody asked for, including keys that briefly vanish and come
     * back (design 5).
     */
    async saveGroupDialog() {
      this.groupDialogError = null;
      this.groupDialogBusy = true;
      try {
        if (this.groupDraft.id === null) {
          await this.request("POST", "/api/groups", {
            label: this.groupDraft.label.trim(),
            room: this.groupDraft.room.trim(),
            member_ids: this.groupDraft.memberIds,
          });
        } else {
          await this.request("PUT", `/api/groups/${this.groupDraft.id}/members`, {
            member_ids: this.groupDraft.memberIds,
          });
        }
        await this.loadGroups();
        // The intersection has changed - see `loadGroupControls`. Reloaded
        // for ALL groups, not only this one: a device that just joined here
        // may have been removed from another group's list in the same
        // breath, and on creation there is no new id at hand anyway.
        await this.loadAllGroupControls();
        this.closeGroupDialog();
      } catch (error) {
        // The server's `detail` verbatim: it already names the offending
        // device and both categories ("device 4 is a socket, the group
        // takes light"). A generic sentence in its place would throw away
        // the only part that says what to do next (Spec 8.1).
        this.groupDialogError = error.message;
      } finally {
        this.groupDialogBusy = false;
      }
    },

    // Signal modal: "Functional" shows immediately what `is_functional`
    // classifies as intentional; "Expert" stays collapsed until the user
    // expands the `<details>` in the modal (until 2026-09-05 a global
    // toggle did this for all devices at once)
    // - the same data basis as above, just unfiltered on the respective
    // opposite condition. Neither list reimplements the relevance rule
    // itself: both only read `signal.functional`, which the API already
    // delivers ready-made (`api.devices._signal_out`).
    expertSignalsFor(deviceId) {
      const signals = this.signalsByDevice[deviceId];
      return signals ? signals.filter((signal) => !signal.functional) : [];
    },

    // All groups of the signals view as one list (review fix 6,
    // phase 6 follow-up): before this, the signal row template in
    // index.html appeared twice, byte-identical except for
    // `functionalSignalsFor` versus `expertSignalsFor` - 51 duplicated
    // lines that had to be touched twice on every change, with nothing
    // to notice if they drifted apart. In the template, `collapsible` now
    // only controls the `<details>`'s initial state (endpoint groups
    // open, expert closed, see `x-init` in index.html) - the rest (row
    // markup) is identical for all groups. The first sentence above
    // has applied twice over since the modal rework: there, all groups
    // even share the same `<details>` markup, not just the same row
    // template.
    //
    // The groups of the signal modal: one per endpoint, then the
    // expert block (design 2026-09-07, section 7.4).
    //
    // The order of the endpoint groups follows the cluster rank list,
    // without any sorting happening here: `functionalSignalsFor` already
    // arrives sorted, and this loop adopts the order of each endpoint's
    // FIRST occurrence. On the button, "Device" (battery only) therefore
    // comes last, even though it is endpoint 0.
    //
    // `group.key` stays stable across redraws ("ep1", "expert") -
    // that is a precondition for the `x-init="$el.open = !group.collapsible"`
    // in the markup: if the key were unstable, Alpine would rebuild the
    // node and silently collapse an open group again.
    signalGroupsFor(deviceId) {
      const groups = [];
      const byEndpoint = new Map();
      for (const signal of this.functionalSignalsFor(deviceId)) {
        let group = byEndpoint.get(signal.endpoint);
        if (!group) {
          group = {
            key: "ep" + signal.endpoint,
            title: signal.endpoint_label,
            subtitle: t("web.signals.group_endpoint_subtitle", { endpoint: signal.endpoint }),
            collapsible: false,
            signals: [],
          };
          byEndpoint.set(signal.endpoint, group);
          groups.push(group);
        }
        group.signals.push(signal);
      }
      // Stays ONE group: breaking 156 signals up across all endpoints
      // produced only more headings, not more overview.
      groups.push({
        key: "expert",
        title: t("web.signals.group_expert"),
        subtitle: "",
        collapsible: true,
        signals: this.expertSignalsFor(deviceId),
      });
      return groups;
    },

    liveValueOf(signal) {
      // No signal, no value. `formatValue` turns this into the dash it
      // already shows for `undefined` anyway - the tile displays "-"
      // instead of the expression throwing. See `signalIsFresh` for why
      // `null` arrives here at all.
      if (!signal) {
        return undefined;
      }
      // Direct access, no `hasOwnProperty` - see `isOnline`. This is
      // exactly where the bug was most visible: the values did not move.
      const live = this.liveValues[signal.key];
      return live === undefined ? signal.value : live;
    },

    // Shared with `saveGroupLabel` - the two used to be near-verbatim
    // copies (collection segment, draft map, error field, that is all
    // that differs). `collection` is the URL segment ("devices" or
    // "groups"), `errorField` the state property a failure is shown
    // through (`deviceActionError` or `groupActionError`); both callers
    // pass their own literal strings, not a value computed from `entity`.
    //
    // `saveRoom`/`saveGroupRoom` hold a reference to exactly the entity
    // object passed in and only write into it after the `await`
    // (`Object.assign`, see the comment on `commissionDevice`'s
    // `existingIndex` branch for why that matters) - this function keeps
    // that guarantee.
    async saveEntityLabel(entity, drafts, collection, errorField) {
      const label = (drafts[entity.id] ?? entity.label).trim();
      if (!label || label === entity.label) {
        return;
      }
      this[errorField] = null;
      try {
        const updated = await this.request("PATCH", `/api/${collection}/${entity.id}`, { label });
        Object.assign(entity, updated);
      } catch (error) {
        this[errorField] = t("web.devices.label_save_error", { message: error.message });
      }
    },

    async saveLabel(device) {
      await this.saveEntityLabel(device, this.labelDrafts, "devices", "deviceActionError");
    },

    // The confirmation names the objects that get orphaned (Spec 9, line
    // "device removed and re-commissioned"; Review-Fix Fix 10,
    // 2026-09-03). It names the key prefix and the two template files -
    // but only up to the device id (`VIU_d12_….xml`), not the full file
    // name: its second half comes from `export.documents.filename_for`,
    // which normalizes the label to ASCII (umlauts, special characters,
    // repeated underscores). Reproducing that rule here in JavaScript
    // would mean maintaining it a second time - exactly the duplication
    // Fix 8 just eliminated elsewhere. The prefix with the device id is
    // unique (see `filename_for`) and is enough to find the file again in
    // Loxone Config.
    async removeDevice(device) {
      const confirmed = window.confirm(t("web.devices.remove_confirm", { label: device.label, id: device.id }));
      if (!confirmed) {
        return;
      }
      this.deviceActionError = null;
      try {
        await this.request("DELETE", `/api/devices/${device.id}`);
        this.devices = this.devices.filter((d) => d.id !== device.id);
        delete this.controlsBySubject[device.id];
        delete this.signalsByDevice[device.id];
        // Without this, a dialog would stay open over a device that no
        // longer exists - and the `x-if` guard in the modal would turn it
        // into an empty box for no discernible reason. `close()` is a
        // no-op when the dialog is not even open; the check is there
        // anyway so a modal over a DIFFERENT device does not close along
        // with it.
        if (this.signalsModalDevice === device.id) {
          this.closeSignalsModal();
        }
        // Finding 1 (re-review 2026-09-05): deleting the last device of a
        // filtered room makes its chip disappear from `roomChips()`, but
        // without this call `roomFilter` would stay stuck on the name -
        // no tile visible anymore, no chip active anymore, and (if it was
        // the very last room) even the whole chip bar gone (`hasAnyRoom()`
        // then false, see index.html), so no "All" chip left to come back
        // to either. A plain reload was the only way out.
        // `reconcileRoomFilter` covers exactly that (see there) and is
        // called here for the same reason as in `saveRoom`/
        // `commitRenameRoom`: any write site that can make the filtered
        // room disappear calls it afterward.
        this.reconcileRoomFilter();
        // Removing a device is a membership change like any other (design
        // 4.3): the server drops its rows from every group it belonged to
        // and recomputes those groups' command intersections. Without this
        // reload, such a group's tile would keep showing the old member
        // count and offer commands whose keys now answer 404.
        await this.loadGroups();
        await this.loadAllGroupControls();
      } catch (error) {
        this.deviceActionError = t("web.devices.remove_error", { message: error.message });
      }
    },

    /**
     * Sends one command. Unchanged for groups, and that is the whole point
     * of the shared key namespace: a group tile makes exactly the same
     * `POST /api/commands/{key}` call a device tile makes, with the same
     * 404/400/502 semantics, and there is deliberately no group control
     * route (design 5). `device` is only read for its `label` in the toast,
     * which a group carries too.
     */
    async executeCommand(device, command) {
      this.commandBusyKey = command.key;
      const value = command.takes_value ? this.commandValueDrafts[command.key] ?? "" : "1";
      try {
        await this.request("POST", `/api/commands/${command.key}`, { value: String(value) });
        this.showToast(t("web.devices.command_sent", { slug: command.slug, label: device.label }));
      } catch (error) {
        this.showToast(t("web.devices.command_failed", { slug: command.slug, message: error.message }), true);
      } finally {
        this.commandBusyKey = null;
      }
    },

    /**
     * Sends a slider value. Called on RELEASE (`change`), not
     * while dragging: one drag = one radio packet. Thread is slow,
     * and if a click is to prove something, the mapping between
     * input and reaction must stay unambiguous (design 2026-09-07,
     * section 6.5).
     */
    async sendControl(device, command, value) {
      this.commandBusyKey = command.key;
      try {
        await this.request("POST", `/api/commands/${command.key}`, { value: String(value) });
        this.showToast(t("web.devices.command_sent", { slug: command.slug, label: device.label }));
      } catch (error) {
        this.showToast(
          t("web.devices.command_failed", { slug: command.slug, message: error.message }),
          true,
        );
      } finally {
        this.commandBusyKey = null;
      }
    },

    /**
     * Carries the last SENT slider position forward into `controlDrafts`
     * - via `CONTROL_DRAFT_FIELDS`, keyed on `command.control`, not via a
     * case distinction at each individual binding (finding I-1,
     * closing review 2026-09-08).
     *
     * Without this carry-forward, the slider would show the start value
     * read on open again after the next redraw (e.g. triggered by a click
     * into the color area, which reassigns `controlDrafts` entirely) - a
     * slider position that contradicts the last sent command and is
     * therefore exactly the silent failure the design rules out.
     *
     * `values` carries one entry per field from
     * `CONTROL_DRAFT_FIELDS[command.control]`, in the same order
     * (one value for "percent"/"kelvin", two for "hue_sat"). A
     * control kind outside the mapping (see `unhandledControls`)
     * deliberately carries nothing forward - there is no slider for it
     * whose position could go stale.
     */
    recordControlDraft(command, ...values) {
      const fields = CONTROL_DRAFT_FIELDS[command.control];
      if (!fields) {
        return;
      }
      const patch = {};
      fields.forEach((field, index) => {
        patch[field] = values[index];
      });
      this.controlDrafts = { ...this.controlDrafts, ...patch };
    },

    /**
     * Hue (degrees) and saturation (percent) into the packed Loxone
     * number that `POST /api/commands/{key}` expects.
     *
     * Why the detour via the Loxone encoding, instead of sending hue/sat
     * directly: the WebUI and Loxone use the same translator
     * (`commands/translate.py`, spec 4.2). A click here thus travels
     * exactly the path Loxone takes later - if it works here, the
     * Loxone path is proven. The price is quantization to whole
     * percent per channel (design 2026-09-07, section 9.1).
     *
     * Brightness is NOT contained in this number - it runs via
     * LevelControl. That is why the value component here is fixed at 1.
     */
    hueSatToLoxone(hue, saturation) {
      // Textbook HSV to RGB with v fixed at 1: brightness is NOT
      // contained in this number, it runs via LevelControl.
      const h = (((hue % 360) + 360) % 360) / 60;
      const s = Math.max(0, Math.min(100, saturation)) / 100;
      const c = s;                                   // chroma at v = 1
      const x = c * (1 - Math.abs((h % 2) - 1));
      const m = 1 - c;                               // white component
      const sectors = [
        [c, x, 0], [x, c, 0], [0, c, x],
        [0, x, c], [x, 0, c], [c, 0, x],
      ];
      const [r, g, b] = sectors[Math.floor(h) % 6].map((channel) =>
        Math.round((channel + m) * 100),
      );
      return r + g * 1000 + b * 1000000;
    },

    /** Converts a click on the color area into hue and saturation
     * and sends it. The area is horizontally hue (0-360°),
     * vertically saturation (100% at the top, 0% at the bottom). */
    /**
     * Where the marker sits on the color area and which color it carries.
     *
     * The inverse of `pickColour`: there a click position becomes a
     * hue/saturation pair, here the pair becomes a position again.
     * Both must use the same axis mapping - horizontally hue
     * 0-360 degrees, vertically full saturation at the top -, otherwise
     * the marker points somewhere other than the click that set it.
     *
     * The fill color is produced via `hsl()` with a fixed lightness of
     * 50%: that is the same assumption as in `hueSatToLoxone` (value
     * fixed at 1, brightness runs via LevelControl), so the dot matches
     * the area beneath it and not the lamp's brightness.
     */
    colourMarkerStyle() {
      const hue = this.controlDrafts.hue ?? 0;
      const saturation = this.controlDrafts.saturation ?? 0;
      const left = (((hue % 360) + 360) % 360) / 3.6;
      const top = 100 - Math.max(0, Math.min(100, saturation));
      return `left: ${left}%; top: ${top}%; background: hsl(${hue} ${saturation}% 50%)`;
    },

    pickColour(event, device, command) {
      const rect = event.currentTarget.getBoundingClientRect();
      const x = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
      const y = Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height));
      const hue = x * 360;
      const saturation = (1 - y) * 100;
      this.recordControlDraft(command, hue, saturation);
      return this.sendControl(device, command, this.hueSatToLoxone(hue, saturation));
    },

    // ---------------------------------------------------------------------
    // Toasts (2026-09-03)
    // ---------------------------------------------------------------------

    /**
     * Shows a message as an overlay at the bottom edge. Deliberately NOT
     * in the text flow: the previous version faded a line in above the
     * device list, which made the whole page jump when toggling and sent
     * the next click astray.
     *
     * Errors stay up much longer than successes - a success message has
     * been seen as soon as the device reacts, an error message is meant
     * to be read.
     */
    showToast(text, isError = false) {
      const id = ++toastCounter;
      this.toasts.push({ id, text, isError });
      window.setTimeout(() => this.dismissToast(id), isError ? 12000 : 4000);
    },

    dismissToast(id) {
      this.toasts = this.toasts.filter((toast) => toast.id !== id);
    },

    // ---------------------------------------------------------------------
    // Sign of life (2026-09-03)
    // ---------------------------------------------------------------------

    /**
     * "3s ago", "12min ago" - or null if this key has never come in over
     * the live connection. Reads `nowTick`, so Alpine redraws the label
     * every second.
     */
    sinceText(timestamp) {
      if (!timestamp) {
        return null;
      }
      const seconds = Math.max(0, Math.round((this.nowTick - timestamp) / 1000));
      if (seconds < 60) {
        return t("web.header.time_ago_seconds", { seconds });
      }
      const minutes = Math.round(seconds / 60);
      if (minutes < 60) {
        return t("web.header.time_ago_minutes", { minutes });
      }
      return t("web.header.time_ago_hours", { hours: Math.round(minutes / 60) });
    },

    /**
     * Like `sinceText`, but never in seconds: "just now", "3m ago",
     * "2h ago".
     *
     * The difference is not cosmetic. `sinceText` feeds a `title`, where a
     * label that changes width every second costs nothing. This one sits
     * in the tile's text flow, and the tile has been here before: a
     * per-signal age used to stand next to the value and was moved into
     * the tooltip precisely because counting up from "7s ago" changes the
     * label's width and shoves the row back and forth, drawing the eye to
     * the motion instead of to the change that matters (see
     * `signalSeenText`).
     *
     * Under a minute this is therefore a fixed string; from there it
     * changes at most once a minute. Reads `nowTick`, so Alpine redraws
     * it on its own.
     *
     * Unlike `sinceText` it has a day branch (final review, A7). The
     * ceiling on both is the bridge's uptime - `last_heard` does not
     * survive a restart - but this label is the one place in the
     * interface built to show a LONG silence, and the incident that
     * prompted it is itself a five-day story: a button whose
     * subscription had been dead since 3 September. "120h ago" is a
     * number to convert before it is an answer.
     */
    sinceTextCoarse(timestamp) {
      if (!timestamp) {
        return null;
      }
      const seconds = Math.max(0, Math.round((this.nowTick - timestamp) / 1000));
      if (seconds < 60) {
        return t("web.header.time_ago_just_now");
      }
      const minutes = Math.round(seconds / 60);
      if (minutes < 60) {
        return t("web.header.time_ago_minutes", { minutes });
      }
      const hours = Math.round(minutes / 60);
      // Two full days, not one: "36h ago" still reads as a span someone
      // can place in their own day, "1d ago" throws that away.
      if (hours < 48) {
        return t("web.header.time_ago_hours", { hours });
      }
      return t("web.header.time_ago_days", { days: Math.round(hours / 24) });
    },

    /**
     * When this device was last heard from, in milliseconds - the LATER
     * of two sources, or `null` when neither has anything.
     *
     * `device.last_heard` comes from the server, once, with
     * `GET /api/devices`. On its own it would go stale in the tile while
     * values stream into that very tile: confidently wrong, which is
     * worse than saying nothing. `deviceHeardAt` carries the live side.
     *
     * The served value is therefore only the starting point, for the
     * window between page load and the first live message from this
     * device - which is precisely the gap it exists to fill, because the
     * live bookkeeping starts empty on every page load and the server's
     * does not.
     */
    lastHeardAt(device) {
      const live = this.deviceHeardAt[device.id];
      const served = device.last_heard ? Date.parse(device.last_heard) : NaN;
      const candidates = [];
      if (live !== undefined) {
        candidates.push(live);
      }
      if (!Number.isNaN(served)) {
        candidates.push(served);
      }
      return candidates.length ? Math.max(...candidates) : null;
    },

    /**
     * The tile's line. A fact, not a judgement.
     *
     * No threshold and no colour anywhere near this: a silent window
     * contact is normal, and so is a silent button or leak detector. Any
     * staleness rule would fire first and most often on exactly the
     * devices that prompted this line, and a warning that cries wolf on
     * healthy hardware gets the next real one ignored too.
     *
     * The `null` branch is the valuable one. "Nothing since the bridge
     * started" is unambiguous - not "offline", not "no data" - and it is
     * the sentence that would have shortened 8 September, when a window
     * contact that only reports on change looked exactly like a button
     * whose subscription had been dead for five days.
     */
    lastHeardText(device) {
      const at = this.lastHeardAt(device);
      if (at === null) {
        return t("web.devices.never_heard");
      }
      return t("web.devices.last_heard", { text: this.sinceTextCoarse(at) });
    },

    /** When ANYTHING last came in over the line - the heartbeat
     * included. This is the value that tells "nothing is changing" apart
     * from "nothing is arriving". */
    heartbeatText() {
      return this.sinceText(this.lastHeartbeatAt);
    },

    /** When this one signal last delivered a value. Now lives only in the
     * cell's `title`, no longer next to it in the text flow: a value like
     * "7s ago" that changes every second also changes its width along the
     * way and shifts the row back and forth. That drew the eye to the
     * motion instead of to the change that actually matters (2026-09-03). */
    signalSeenText(signal) {
      if (!signal) {
        return "";
      }
      return this.sinceText(this.liveSeenAt[signal.key]);
    },

    /** Whether this signal has just now received a value. Carries the
     * highlight that draws the eye to the right spot - without anything
     * in the row's layout moving at all. Reads `nowTick`, so Alpine drops
     * the class again once the time is up. */
    signalIsFresh(signal) {
      // `null` is a VALID argument here, not a programming error.
      // The caller that supplied it was `leadSignalFor` - for every
      // device whose signals were not yet loaded, i.e. between
      // `GET /api/devices` and `GET /api/devices/<id>/signals`, for
      // EVERY device, for at least one rendering pass (2026-09-06).
      //
      // This caller no longer exists since the primary signal was
      // dropped (design 2026-09-07): the value grid's `x-for` runs over
      // an empty list and evaluates nothing at all. The tolerance stays
      // in place nonetheless. Removing it because the one KNOWN caller is
      // gone would be the kind of cleanup that bites back at the next
      // caller - and the next one would rediscover the same bug, without
      // knowing the comment below.
      //
      // The `x-show` on the wrapper in `index.html` did NOT catch this: it
      // only sets `display`, it does not stop Alpine from evaluating the
      // children's expressions. A hidden element keeps computing anyway.
      // That is why the guard lives here, at the one place every caller
      // passes through - and not in three bindings in the markup.
      if (!signal) {
        return false;
      }
      const at = this.liveSeenAt[signal.key];
      return at !== undefined && this.nowTick - at < VALUE_FRESH_MS;
    },

    /** The tooltip of a value cell: when the value last arrived, or a
     * note that nothing has come in since the page was loaded. */
    signalAgeTitle(signal) {
      // With no signal there is nothing to date - an empty `title` omits
      // the tooltip, instead of writing the note from
      // `web.header.unchanged_since_load` over a cell that shows no value
      // at all. The key is deliberately used here instead of its German
      // text: `test_the_relative_time_and_header_helpers_are_translated`
      // locks exactly this literal in the body of the function, and a
      // guard against hardcoded translations should not be tripped up by
      // whether a comment quotes it.
      if (!signal) {
        return "";
      }
      const text = this.signalSeenText(signal);
      return text
        ? t("web.header.last_updated", { text })
        : t("web.header.unchanged_since_load");
    },

    // Writes the numeric code while typing exactly as it appears on the
    // device.
    //
    // `commissionCode` is EXPLICITLY carried forward here instead of
    // relying on x-model: both hang off the same `input` event, and
    // which listener runs first depends on the order of the
    // attributes in the markup. State that depends on attribute order
    // is a bug that only shows up when things get reordered.
    formatCommissionCode(input) {
      const before = input.value;
      const formatted = formatPairingCode(before);
      if (formatted !== before) {
        // Count digits to the LEFT of the cursor, not character
        // positions: otherwise every newly inserted hyphen would shift
        // the cursor by one.
        const caret = input.selectionStart ?? before.length;
        const digitsLeft = before.slice(0, caret).replace(/\D/g, "").length;
        input.value = formatted;
        let seen = 0;
        let position = 0;
        while (position < formatted.length && seen < digitsLeft) {
          if (/\d/.test(formatted[position])) {
            seen += 1;
          }
          position += 1;
        }
        input.setSelectionRange(position, position);
      }
      this.commissionCode = input.value;
    },

    // Backspace DIRECTLY behind a hyphen deletes the digit before it,
    // Delete DIRECTLY before one deletes the digit after it - removing
    // the separator along with it in each case.
    //
    // Without this special handling, the keypress only deletes the
    // separator, which `formatCommissionCode` immediately puts back
    // afterward: the value does not change, the cursor stays put, and the
    // key appears dead. That is the one point where a self-formatting
    // input usually fails - for both keys, not only backspace.
    //
    // Does NOT apply within QR content: there the hyphen carries meaning
    // (base38 alphabet), and this branch would otherwise silently delete
    // payload data along with it (see `isPairingQrCode`).
    commissionCodeKeydown(event) {
      const isBackspace = event.key === "Backspace";
      const isDelete = event.key === "Delete";
      if (!isBackspace && !isDelete) {
        return;
      }
      const input = event.target;
      if (input.selectionStart !== input.selectionEnd) {
        return;
      }
      if (isPairingQrCode(input.value)) {
        return;
      }
      const caret = input.selectionStart;
      let from;
      let to;
      if (isBackspace) {
        if (caret < 2 || input.value[caret - 1] !== "-") {
          return;
        }
        from = caret - 2;
        to = caret;
      } else {
        if (input.value[caret] !== "-") {
          return;
        }
        from = caret;
        to = caret + 2;
      }
      event.preventDefault();
      input.value = input.value.slice(0, from) + input.value.slice(to);
      input.setSelectionRange(from, from);
      this.formatCommissionCode(input);
    },

    // Text and color of the chip in the field.
    commissionCodeBadge() {
      const state = describePairingCode(this.commissionCode);
      return {
        text: state.key ? t(state.key, state.values) : "",
        tone: state.tone,
      };
    },

    async commissionDevice() {
      this.commissionMessage = null;
      // Normalized, not just trimmed: the separators that the field set
      // itself while typing do not belong in the Matter stack. The
      // backend strips them a second time anyway
      // (`CommissionRequest._strip_separators`) - here they are stripped
      // outright, so the UI does not send something other than what it
      // shows.
      const code = normalizePairingCode(this.commissionCode);
      if (!code) {
        this.commissionMessage = t("web.devices.commission_code_required");
        this.commissionMessageIsError = true;
        return;
      }
      this.commissionBusy = true;
      this.commissionStep = 0;
      this.commissionFailed = false;
      // The progress display shows the FORMATTED code, not the
      // transmitted one: whoever waits twenty to sixty seconds should
      // recognize the code they typed in.
      this.commissionRunCode = formatPairingCode(this.commissionCode.trim());
      try {
        const body = { code };
        if (this.commissionThreadDataset.trim()) {
          body.thread_dataset = this.commissionThreadDataset.trim();
        }
        // Room (design 6.7): "" means "no room" and is not sent along at
        // all; "__new__" is the selection field's special value, behind
        // which the `commissionNewRoom` text field sits.
        const room =
          this.commissionRoom === "__new__"
            ? this.commissionNewRoom.trim()
            : this.commissionRoom.trim();
        if (room) {
          body.room = room;
        }
        this.commissionRunRoom = room;
        const device = await this.request("POST", "/api/devices/commission", body);
        // Finding 4 (re-review 2026-09-05): for a device that is already
        // registered, the commissioning route returns the same
        // `device_id` instead of creating a second one (see
        // `register_device`'s early return path in `model/store.py`, as
        // well as the backend test
        // `test_recommissioning_a_known_device_applies_the_chosen_room`).
        // An unconditional `push` then stored this device a second time
        // in `this.devices`: two tiles with the same `device.id`, which
        // violates `x-for`'s `:key="device.id"` (Alpine warns about
        // duplicate keys in the console) and made the room chip count
        // twice. For an already existing card, the existing object is
        // therefore written into instead of being swapped in the array -
        // otherwise it is newly appended.
        const existingIndex = this.devices.findIndex((d) => d.id === device.id);
        if (existingIndex === -1) {
          this.devices.push(device);
        } else {
          // `saveRoom` (above) and `saveLabel` hold a reference to exactly
          // this device object before their `await` and only write into
          // it afterward (`Object.assign(device, updated)`). A new object
          // inserted here would leave that reference sitting on a copy
          // decoupled from the array - the save would report no error,
          // but neither the tile nor `this.devices` would see the result.
          // That is why the existing object is filled in instead of being
          // replaced.
          Object.assign(this.devices[existingIndex], device);
        }
        // The card is visible and always open from now on (section 3) -
        // without this reload it would show "Loading signals…"
        // permanently, until the view happened to be entered again at
        // some point.
        this.commissionStep = 1;
        await Promise.all([this.loadControls(device.id), this.loadSignals(device.id)]);
        this.commissionStep = 2;
        // The earlier sentence "live values only after a bridge restart"
        // has been dropped because the limitation itself is gone: the
        // commissioning route now calls `follow_node`, which sets up this
        // device's attribute subscriptions and seeds its values (design
        // from 2026-09-04). A note is still needed here, just a different
        // one: that the values only arrive in the Miniserver after export
        // and import into Loxone Config, since there is no virtual input
        // there until then. The sentence itself lives in strings.yaml
        // under `web.devices.commission_success`.
        this.commissionMessage = t("web.devices.commission_success", { label: device.label });
        this.commissionMessageIsError = false;
        this.commissionCode = "";
        this.commissionThreadDataset = "";
        // The room DELIBERATELY stays put (design 6.7): whoever
        // commissions four devices in the kitchen picks it once. A
        // pairing code, by contrast, is worthless after use, and a
        // leftover one would be a source of errors.
      } catch (error) {
        // Without this case distinction, this message's heading stood
        // doubled in the UI: a 422 from this route already carries a
        // fully framed sentence the server itself composed
        // (`api.errors.commissioning_failed`, set in matter/client.py) - a
        // second frame here pushed the actual information further back.
        //
        // The distinction is made on the HTTP status, not on the text. A
        // comparison against the start of the server message only ever
        // knows one of the two languages: if the bridge runs in English
        // it would never match, and the duplication would silently
        // return - quite apart from the fact that a message text may
        // change at any time. The status, on the other hand, is the same
        // regardless of which language the server answers in. It has been
        // attached to every error object since `requestJson` (see there).
        //
        // Any other failure (502, 503, a network error with no response
        // at all) brings no frame of its own and gets one here - without
        // it, the UI would show nothing but "HTTP 502".
        const message = String(error.message ?? "");
        this.commissionMessage =
          error.status === 422 ? message : t("web.devices.commission_failed", { message });
        this.commissionMessageIsError = true;
        // `commissionStep` is NOT reset: it continues to point at the
        // step it got stuck on, and `commissionStepClass` colors exactly
        // that one red. The way back to the form goes via
        // `resetCommission()` on the button below - the typed-in code
        // stays put, since a typo in it is the most likely reason to end
        // up here.
        this.commissionFailed = true;
      } finally {
        this.commissionBusy = false;
      }
    },

    /**
     * The state of a step in the progress display: "done", "running",
     * "failed", or empty (still pending).
     *
     * A pure expression on `commissionStep`/`commissionFailed` instead of
     * a third state variable holding the class names: two fields telling
     * the same story drift apart sooner or later - and the display is
     * exactly the place where no one would notice, because it shows
     * "something" regardless.
     */
    commissionStepClass(index) {
      if (this.commissionStep === null) {
        return "";
      }
      if (this.commissionFailed && index === this.commissionStep) {
        return "failed";
      }
      if (index < this.commissionStep) {
        return "done";
      }
      return index === this.commissionStep ? "running" : "";
    },

    /**
     * Back from the progress display to the form - after a success
     * ("one more device") as well as after a failure ("try again").
     *
     * The message goes along with it: it belongs to the run being left
     * behind. Leaving a success message standing above the empty form for
     * the next device would attribute it to the wrong device.
     */
    resetCommission() {
      this.commissionStep = null;
      this.commissionFailed = false;
      this.commissionMessage = null;
      this.commissionRunCode = "";
      this.commissionRunRoom = "";
      // Only on the next tick: until then `x-show` still keeps the form
      // at `display: none`, and a `focus()` on an invisible field quietly
      // does nothing at all.
      this.$nextTick(() => this.$refs.commissionCode?.focus());
    },

    // ---------------------------------------------------------------------
    // Signals
    // ---------------------------------------------------------------------

    signalsModalDeviceObject() {
      return this.devices.find((device) => device.id === this.signalsModalDevice) || null;
    },

    /**
     * Opens the signal modal for a device.
     *
     * The `$nextTick` is mandatory, not a matter of style: `showModal()`
     * sets initial focus on the first focusable element IN the dialog,
     * and that does not exist until Alpine has built the `x-if` content.
     * Without this wait the dialog opens empty, focus stays on the
     * `<dialog>` itself, and the first Tab press starts over again at the
     * top of the document.
     *
     * `$refs` is safe here even though the comment on the tile menu
     * (index.html, finding 3) explicitly advises against it: that
     * objection applies to a registration that runs PER TILE and
     * overwrites itself. This `<dialog>` exists exactly once in the
     * document - the same situation as `pinLogListToTop`, which already
     * uses `this.$refs` today for the same reason.
     */
    openSignalsModal(device) {
      // `signalsError` is page-wide, but the modal is per device: without
      // this reset, another device's error (e.g. from the parallel load
      // in `startApp`, or from `saveTitle` after an Escape-triggered blur)
      // survives the device switch and hangs unnamed over a cleanly
      // loaded list. This is NOT a second reset of `signalsModalDevice` -
      // that rule applies exclusively to that one field, `signalsError`
      // is a state of its own.
      this.signalsError = null;
      this.signalsModalDevice = device.id;
      this.$nextTick(() => this.$refs.signalsModal.showModal());
    },

    /**
     * Closes the modal via the native `close()` method instead of
     * clearing the state directly: `close()` fires the `close` event,
     * and its handler in index.html is the one place that resets
     * `signalsModalDevice` AND `expandedSignalKey` (finding 3 of the
     * 2026-09-07 follow-up review: without the second reset, the same
     * disclosure would immediately be open again the next time the same
     * device is opened, without the kebab menu having been clicked for
     * it). Writing `this.signalsModalDevice = null` here as well would
     * again mean two truths about the same state.
     */
    closeSignalsModal() {
      this.$refs.signalsModal.close();
    },

    /**
     * Whether a mouse event lies on the modal's BACKDROP - and not on the
     * dialog itself.
     *
     * A `<dialog>` in `showModal()` state has a `::backdrop` pseudo
     * element over the entire window as its backdrop; mouse events on it
     * carry the `<dialog>` as their target. `event.target === el` alone
     * therefore does NOT distinguish the backdrop from the dialog - its
     * own scrollbar also belongs to the element and produces the same
     * target.
     *
     * The reliable difference is position: the dialog occupies exactly
     * its own rectangle, the backdrop everything outside it.
     *
     * An earlier attempt instead compared `offsetX` with `clientWidth`.
     * That only separates out a scrollbar that RESERVES SPACE; with an
     * overlay one (the macOS default, measures 0 px) it ran into nothing,
     * and neither a horizontal bar nor an RTL layout were covered. The
     * rectangle comparison needs none of these three case distinctions -
     * so it replaces them instead of retrofitting them one by one.
     */
    isBackdropEvent(event, el) {
      if (event.target !== el) {
        return false;
      }
      const rect = el.getBoundingClientRect();
      return (
        event.clientX < rect.left ||
        event.clientX > rect.right ||
        event.clientY < rect.top ||
        event.clientY > rect.bottom
      );
    },

    async loadSignals(deviceId) {
      this.signalsError = null;
      try {
        this.signalsByDevice[deviceId] = await this.request(
          "GET",
          `/api/devices/${deviceId}/signals`,
        );
      } catch (error) {
        this.signalsError = t("web.signals.load_error", { message: error.message });
      }
    },

    async saveTitle(signal) {
      const title = (this.titleDrafts[signal.key] ?? signal.title).trim();
      if (!title || title === signal.title) {
        return;
      }
      try {
        const updated = await this.request("PATCH", `/api/signals/${signal.key}`, { title });
        Object.assign(signal, updated);
      } catch (error) {
        this.signalsError = t("web.signals.title_save_error", { message: error.message });
      }
    },

    async toggleExported(signal) {
      try {
        const updated = await this.request("PATCH", `/api/signals/${signal.key}`, {
          exported: !signal.exported,
        });
        Object.assign(signal, updated);
      } catch (error) {
        this.signalsError = t("web.signals.export_flag_error", { message: error.message });
      }
    },

    // How many signals of this device actually go to Loxone as an
    // input. `exported` alone is not enough: a signal whose value fits
    // no Loxone input (`exportable === false`) produces none -
    // the same distinction that `to_inputs` makes server-side.
    exportedSignalCount(deviceId) {
      const signals = this.signalsByDevice[deviceId];
      return signals ? signals.filter((s) => s.exported && s.exportable).length : 0;
    },

    signalCount(deviceId) {
      const signals = this.signalsByDevice[deviceId];
      return signals ? signals.length : 0;
    },

    // Only what is ON gets switched off. A `toggleExported` over all
    // signals would invert the selection instead of clearing it - but
    // the button is called "deselect all", not "invert".
    async deselectAllSignals(deviceId) {
      const signals = this.signalsByDevice[deviceId] || [];
      for (const signal of signals) {
        if (signal.exported) {
          await this.toggleExported(signal);
        }
      }
    },

    async toggleResend(signal) {
      try {
        const updated = await this.request("PATCH", `/api/signals/${signal.key}`, {
          resend: !signal.resend,
        });
        Object.assign(signal, updated);
      } catch (error) {
        this.signalsError = t("web.signals.resend_toggle_error", { message: error.message });
      }
    },

    async writeRaw(signal) {
      const value = this.rawWriteDrafts[signal.key];
      if (value === undefined || value === "") {
        return;
      }
      this.rawWriteBusyKey = signal.key;
      try {
        await this.request("POST", `/api/signals/${signal.key}/write`, { value: String(value) });
        this.rawWriteMessages[signal.key] = { text: t("web.signals.write_success"), isError: false };
      } catch (error) {
        this.rawWriteMessages[signal.key] = { text: error.message, isError: true };
      } finally {
        this.rawWriteBusyKey = null;
      }
    },

    rawWriteMessageClass(signal) {
      const message = this.rawWriteMessages[signal.key];
      return message && message.isError ? "hint danger-text" : "hint";
    },

    toggleSignalDetails(signal) {
      this.expandedSignalKey = this.expandedSignalKey === signal.key ? null : signal.key;
    },

    // The raw-value field only makes sense for attributes (an event has
    // no stored value that you could overwrite) - the origin note above
    // it, by contrast, applies to both signal kinds. As its own
    // helper instead of `signal.kind === 'attribute'` directly in the
    // markup, so the same condition does not appear twice in the source.
    isAttributeSignal(signal) {
      return signal.kind === "attribute";
    },

    /**
     * Formulates a signal's origin as a plain-text sentence for the
     * disclosure.
     *
     * `signal.path` already carries the same information as "1/59/1" -
     * that the THIRD segment in it is the element is knowledge about the
     * path format and therefore belongs in a named function, not as
     * `signal.path.split('/')[2]` right in the markup (the same reason as
     * with `commandsFor` further above: an ordinary function is more
     * readable than an expression with a built-in decomposition rule in
     * the markup - and if the path format changes, there is exactly one
     * place for that instead of a search through `index.html`).
     */
    signalOriginText(signal) {
      return t("web.signals.origin", {
        endpoint: signal.endpoint,
        cluster: signal.cluster_id,
        element: signal.path.split("/")[2],
      });
    },

    // ---------------------------------------------------------------------
    // Control modal (task 7): sliders instead of bare number fields for
    // value-carrying commands. Structure, opening, closing, and backdrop
    // click follow the signal modal above exactly - see its comments
    // for the rationale.
    // ---------------------------------------------------------------------

    /** The modal's subject, resolved against the CURRENT lists - the field
     * holds an id, not an object, for the reason given there. A string
     * subject is a group ("g3"), a number is a device id; the two cannot be
     * confused. */
    controlModalDeviceObject() {
      if (typeof this.controlModalDevice === "string") {
        return (
          this.groups.find((group) => this.groupSubject(group) === this.controlModalDevice) || null
        );
      }
      return this.devices.find((device) => device.id === this.controlModalDevice) || null;
    },

    /**
     * Whether this subject's controls must be blocked because it cannot be
     * reached. An OFFLINE DEVICE blocks: a command to it would be the
     * silent nothing Spec 8.1 exists to prevent.
     *
     * A GROUP never blocks. It has no online state of its own, and an
     * aggregate over six lamps would be exactly the invention design
     * section 2 rules out - "some of them are offline" is not a reason to
     * refuse the ones that are not. A fan-out with an unreachable member
     * answers 502 and names it (design 3.2), which is the honest place for
     * that information; the reachable lamps still switch.
     */
    subjectOffline(subject) {
      if (typeof subject === "string") {
        return false;
      }
      const device = this.devices.find((entry) => entry.id === subject);
      return device ? !this.isOnline(device) : false;
    },

    /** The member a group's slider positions were read from, or "" for a
     * device (whose controls carry no such field). The modal shows it under
     * every slider: a group has no state of its own, and naming the device
     * the number came from is the honest alternative both to hiding it and
     * to presenting it as the group's (design 5). */
    controlSeedLabel(subject) {
      const controls = this.controlsBySubject[subject];
      return controls && controls.seed_device_label ? controls.seed_device_label : "";
    },

    /**
     * Opens the control modal. The `$nextTick` is mandatory, not style -
     * the same reasoning as with `openSignalsModal`: `showModal()` sets
     * the initial focus on the first focusable element IN the dialog,
     * and that does not exist until Alpine has built the `x-if` content.
     */
    openControlModal(device) {
      this.deviceActionError = null;
      this.controlModalDevice = device.id;
      this.controlDrafts = this.readStartValues(device.id);
      this.controlTab = this.controlDrafts.colormode === 0 ? "colour" : "white";
      this.$nextTick(() => this.$refs.controlModal.showModal());
    },

    /**
     * The same modal for a group. The start values come from the SEED
     * member that `GET /api/groups/{id}/controls` names, not from the group
     * - a group has no state to read (design 2), and the lamp-controls
     * design ruled out a slider with no start value at all, because the
     * first nudge then yanks the light somewhere and proves nothing about
     * what it changed. `readStartValues` therefore runs against the seed
     * device's signals, which are already loaded like every other device's.
     *
     * An empty group has no seed: the drafts stay empty and every slider
     * falls back to the "start value unknown" note the device path already
     * shows, rather than to a made-up position.
     */
    openGroupControlModal(group) {
      this.groupActionError = null;
      const subject = this.groupSubject(group);
      const controls = this.controlsBySubject[subject];
      this.controlModalDevice = subject;
      this.controlDrafts =
        controls && controls.seed_device_id !== null && controls.seed_device_id !== undefined
          ? this.readStartValues(controls.seed_device_id)
          : {};
      this.controlTab = this.controlDrafts.colormode === 0 ? "colour" : "white";
      this.$nextTick(() => this.$refs.controlModal.showModal());
    },

    /** Closes via `close()`, so the `close` handler in index.html
     * stays the one place that resets `controlModalDevice` -
     * the same rule as with `closeSignalsModal`. */
    closeControlModal() {
      this.$refs.controlModal.close();
    },

    // The slugs under which a device's signals carry the start values.
    // `signalsByDevice` holds ALL signals, not only the
    // exported ones (see api/devices.py, `get_signals`), and their values
    // are already scaled (loxone/values.py, `to_loxone_value`) - level
    // and saturation in percent, hue in degrees. Only the color
    // temperature stays in mired, because Kelvin is a reciprocal that
    // `scale` cannot do.
    // Looked up by PATH, not by the slug in the key: if a key collides
    // within a device, `Store._assign_key` appends the element ID
    // (`d1_1_hue_0`), and a comparison against `_hue` would then come up
    // empty. `SignalOut.path` is "endpoint/cluster/element" and therefore
    // exact.
    signalValueByPath(deviceId, clusterId, elementId) {
      const signals = this.signalsByDevice[deviceId] || [];
      const signal = signals.find((entry) => entry.path.endsWith(`/${clusterId}/${elementId}`));
      return signal ? this.liveValueOf(signal) : undefined;
    },

    /**
     * Read once on open, then NOT kept in sync afterward (design
     * 2026-09-07, section 2). Without these start values, every slider
     * would sit at a made-up position, and the first nudge would yank the
     * light off somewhere - the click would then prove nothing about the
     * state it just changed.
     *
     * `undefined` stays `undefined` and is not replaced by a null:
     * the UI shows the note "start value unknown" for that instead,
     * rather than faking knowledge that does not exist.
     */
    readStartValues(deviceId) {
      // Cluster 8 attribute 0 = CurrentLevel; cluster 768: 0 = CurrentHue,
      // 1 = CurrentSaturation, 7 = ColorTemperatureMireds, 8 = ColorMode.
      // All checked against the installed SDK (see design, section 4).
      const mireds = this.signalValueByPath(deviceId, 768, 7);
      return {
        percent: this.signalValueByPath(deviceId, 8, 0),
        kelvin: mireds > 0 ? Math.round(1000000 / mireds) : undefined,
        hue: this.signalValueByPath(deviceId, 768, 0),
        saturation: this.signalValueByPath(deviceId, 768, 1),
        colormode: this.signalValueByPath(deviceId, 768, 8),
      };
    },

    // ---------------------------------------------------------------------
    // Export
    // ---------------------------------------------------------------------

    async loadExportStatus() {
      this.exportError = null;
      try {
        const rows = await this.request("GET", "/api/export/status");
        const byDevice = {};
        for (const row of rows) {
          byDevice[row.device_id] = row;
        }
        this.exportStatusByDevice = byDevice;
      } catch (error) {
        this.exportError = t("web.export.status_load_error", { message: error.message });
      }
    },

    exportStatusFor(deviceId) {
      return this.exportStatusByDevice[deviceId] || null;
    },

    // ---------------------------------------------------------------------
    // Settings
    // ---------------------------------------------------------------------

    async loadSettings() {
      this.settingsError = null;
      try {
        this.bridgeSettings = await this.request("GET", "/api/settings");
        this.settingsDraft = {
          bridge_ip: this.bridgeSettings.bridge_ip ?? "",
          udp_port: this.bridgeSettings.udp_port,
          listen_port: this.bridgeSettings.listen_port,
        };
      } catch (error) {
        this.settingsError = t("web.settings.load_error", { message: error.message });
      }
    },

    async saveSettings() {
      this.settingsError = null;
      if (!this.settingsDraft.bridge_ip.trim()) {
        this.settingsError = t("web.settings.bridge_ip_required");
        return;
      }
      this.settingsBusy = true;
      try {
        this.bridgeSettings = await this.request("PATCH", "/api/settings", {
          bridge_ip: this.settingsDraft.bridge_ip.trim(),
          udp_port: Number(this.settingsDraft.udp_port),
          listen_port: Number(this.settingsDraft.listen_port),
        });
        this.showToast(t("web.settings.saved_toast"));
      } catch (error) {
        this.settingsError = t("web.settings.save_error", { message: error.message });
      } finally {
        this.settingsBusy = false;
      }
    },

    /** Sets the shared language setting (PATCH /api/language, Task 1) and
     * then reloads the whole page - the confirmed, simpler variant from
     * the design discussion (Spec section 7): no special case for toasts
     * already shown or WebSocket state, which would otherwise stay in the
     * old language.
     *
     * try/catch/finally around `this.request(...)` - the same shape as
     * `saveSettings()` above (Review-Fix Important, whole-branch review
     * 2026-09-04): `this.request` rethrows on every error except 401,
     * and without this try/catch a failure (e.g. 400/502) would be an
     * unhandled promise rejection with no feedback at all for the user.
     * `settingsBusy` also prevents a fast double click from firing two
     * simultaneous PATCH calls - shared with `saveSettings()`, both
     * actions live in the same settings card. */
    async setLanguage(language) {
      if (language === this.language) {
        return;
      }
      this.settingsError = null;
      this.settingsBusy = true;
      try {
        await this.request("PATCH", "/api/language", { language });
        window.location.reload();
      } catch (error) {
        this.settingsError = t("web.settings.language_error", { message: error.message });
      } finally {
        this.settingsBusy = false;
      }
    },

    async loadResendInterval() {
      this.resendIntervalError = null;
      try {
        this.resendInterval = await this.request("GET", "/api/settings/resend-interval");
        this.resendIntervalDraft = this.resendInterval.interval_seconds;
      } catch (error) {
        this.resendIntervalError = t("web.settings.resend_load_error", { message: error.message });
      }
    },

    async saveResendInterval() {
      this.resendIntervalError = null;
      if (!Number.isFinite(this.resendIntervalDraft) || this.resendIntervalDraft < 10) {
        this.resendIntervalError = t("web.settings.resend_interval_invalid");
        return;
      }
      this.resendIntervalBusy = true;
      try {
        this.resendInterval = await this.request("PATCH", "/api/settings/resend-interval", {
          interval_seconds: Number(this.resendIntervalDraft),
        });
        this.showToast(t("web.settings.resend_saved_toast"));
      } catch (error) {
        this.resendIntervalError = t("web.settings.resend_save_error", { message: error.message });
      } finally {
        this.resendIntervalBusy = false;
      }
    },

    async previewExport() {
      this.exportError = null;
      if (!this.bridgeSettings.bridge_ip) {
        this.exportError = t("web.export.bridge_ip_missing");
        return;
      }
      this.exportBusy = true;
      try {
        const params = new URLSearchParams({
          bridge_ip: this.bridgeSettings.bridge_ip,
          system: String(this.exportIncludeSystem),
        });
        this.exportPreview = await this.request("GET", `/api/export/preview?${params}`);
        await this.loadExportStatus();
      } catch (error) {
        this.exportError = t("web.export.preview_failed", { message: error.message });
      } finally {
        this.exportBusy = false;
      }
    },

    previewDevices() {
      if (!this.exportPreview) {
        return [];
      }
      if (!this.exportOnlyPending) {
        return this.exportPreview.devices;
      }
      return this.exportPreview.devices.filter((device) => {
        const status = this.exportStatusFor(device.device_id);
        return !status || status.changed_since_export;
      });
    },

    // `only_pending` travels along with the request (Review-Fix Fix 4,
    // 2026-09-03). Previously the filter only applied to the table above
    // it, while the download delivered every device without exception AND
    // marked all of them as exported - anyone who filtered, saw a pending
    // device, and downloaded got everything, and the filter would be
    // empty forever afterward. Now the same checkbox decides both, and
    // `/api/export/download` only marks what it actually delivered (see
    // `api/export.py`).
    downloadUrl() {
      const params = new URLSearchParams({
        bridge_ip: this.bridgeSettings.bridge_ip,
        port: String(this.bridgeSettings.udp_port),
        listen: String(this.bridgeSettings.listen_port),
        system: String(this.exportIncludeSystem),
        only_pending: String(this.exportOnlyPending),
      });
      return `/api/export/download?${params}`;
    },

    // Formerly a plain `<a href>`: an error response (e.g. 401 after an
    // expired session, or 422 for an empty required field) would have
    // replaced the whole page with its raw text instead of appearing in
    // the UI (Review-Fix Fix 1a, 2026-09-03). Hence via `download()`,
    // which uses `requestDownload()` like every other call.
    //
    // The IP check was here for the same reason previously: without it, a
    // click with an empty IP field replaced the page with the backend's
    // raw 422 error response (required parameter `bridge_ip`, see
    // `api/export.py`) - for a diagnostics tool that is used precisely in
    // difficult moments, an error message in the same place where the
    // preview already shows its errors is the better answer.
    async downloadExport() {
      this.exportError = null;
      if (!this.bridgeSettings.bridge_ip) {
        this.exportError = t("web.export.bridge_ip_missing");
        return;
      }
      try {
        await this.download(this.downloadUrl(), "loxmatter-export.zip");
      } catch (error) {
        this.exportError = t("web.export.download_failed", { message: error.message });
        return;
      }
      // A download IS an export (see `api/export.py`, decision 1): it
      // writes `exported_at` for every delivered device. Without this
      // reload, the "Last exported" column kept showing the earlier state
      // and the "only not-yet-exported" filter kept showing the devices
      // just exported - until someone eventually reloaded the preview
      // (Review-Fix Fix 12, 2026-09-03).
      await this.loadExportStatus();
    },

    // Export button on an individual device card (device dashboard
    // design, section 6) - no preview step: the values are already
    // openly displayed on the card, an additional preview would be
    // duplicate information.
    async exportDevice(device) {
      this.deviceActionError = null;
      if (!this.bridgeSettings.bridge_ip) {
        this.deviceActionError = t("web.export.bridge_ip_missing");
        return;
      }
      const params = new URLSearchParams({
        bridge_ip: this.bridgeSettings.bridge_ip,
        port: String(this.bridgeSettings.udp_port),
        listen: String(this.bridgeSettings.listen_port),
        device_id: String(device.id),
      });
      try {
        await this.download(`/api/export/download?${params}`, `loxmatter-d${device.id}-export.zip`);
        this.showToast(t("web.devices.exported_toast", { label: device.label }));
      } catch (error) {
        this.deviceActionError = t("web.devices.export_failed", { message: error.message });
        return;
      }
      await this.loadExportStatus();
    },

    // ---------------------------------------------------------------------
    // System
    // ---------------------------------------------------------------------

    // Now only loads the system check once via GET - datagrams, command
    // log, and log lines have been delivered continuously since Task 6 by
    // the diagnostics channel (`connectDiagnosticsLive`, opens on
    // switching to this view in `selectView`). For the system check,
    // however, there is no third stream on `/api/diagnostics/live` - it
    // stays a one-time fetch, triggered here and via the "Refresh"
    // button.
    async loadSystem() {
      this.systemError = null;
      this.diagnosticsBusy = true;
      try {
        // Before the checks, not after: the version is the first card in
        // the tab and should not wait for the checks (the real network
        // work) to finish.
        //
        // In its OWN try now, no longer sharing one with the two update
        // calls below: `/api/version` is exactly the call in this
        // function that fails throughout the bridge's OWN restart window
        // (`recreate`/`health` in `update.py`'s `_RUNNING_PHASES`) -
        // precisely the window the update card further down exists to
        // report on. A shared `try` let this expected failure jump
        // straight to the outer `catch`, past both
        // `loadUpdateStatus()`/`loadUpdateCheck()`: on a cold load
        // `updateStatus` stayed `null`, `updateRunning()` read `false`,
        // and the poll timer - which only `loadUpdateStatus()` ever
        // starts - never got the chance to. Opening the System tab (or
        // reloading it, or pressing "Refresh") during exactly this
        // window then showed nothing but the generic load-error banner -
        // no restarting message, no steps, no polling - until a later,
        // manual retry happened to land after the bridge answered again.
        // Version and update state are two independent facts from two
        // independent sources (the bridge's own endpoint vs. the
        // sidecar's state file, read back through the bridge once it
        // returns) - one being briefly unreadable says nothing about the
        // other and must not blank it too.
        try {
          this.versionInfo = await this.request("GET", "/api/version");
        } catch {
          // Left as-is rather than set to `null`: the template's own
          // `x-if="versionInfo"` already hides a missing value, a stale
          // "last known" version is a truer answer than none during a
          // restart, and both the restart banner (index.html, driven by
          // `updateRunning()`) and the update card below already explain
          // WHY the bridge is not answering right now - a second message
          // for the same fact here would only be noise.
        }
        await this.loadUpdateStatus();
        await this.loadUpdateCheck();
        this.systemChecks = await this.request("GET", "/api/diagnostics/system");
      } catch (error) {
        this.systemError = t("web.system.load_error", { message: error.message });
      } finally {
        this.diagnosticsBusy = false;
      }
    },

    /** The phases in which the sidecar is still doing something - copied
     * one-to-one from `update.py`'s own `_RUNNING_PHASES` (that module's
     * docstring is explicit that `rejected`/`idle`/`done`/`failed` are
     * end states, `rollback` is not). Outside of these, the timer rests.
     *
     * `build` (the dev channel's own local build, alongside `pull` for
     * the stable channel's download - the two never both occur in one
     * pass) has to be in this list for the identical reason `pull` and
     * `recreate` already are: leaving it out does not just mislabel a
     * step, it makes THIS function read `false` for the better part of a
     * minute while a real build runs - stopping the poll timer
     * (`loadUpdateStatus`, below) and un-suppressing the "no updater
     * installed" hint (index.html, gated on `!updateRunning()`) for a
     * sidecar that is, at that exact moment, working as hard as it ever
     * does. */
    updateRunning() {
      const phase = this.updateStatus?.state?.phase;
      return ["queued", "backup", "pull", "build", "recreate", "health", "rollback"].includes(
        phase,
      );
    },

    /** Whether the UPDATER SIDECAR ITSELF (not the bridge, and nothing to
     * do with `updateRunning()`) is running an image GHCR no longer
     * serves under `:stable` - i.e. whether refreshing it (the command
     * `updaterRefreshMessage()` below prints) would actually change
     * anything. The sidecar no longer replaces its own container after a
     * successful update (removed - see the incident recorded in
     * update-once.sh, near the end of the success branch: measured to
     * corrupt its own container instead of updating it), so nothing keeps
     * the two in step on its own any more - this is the replacement
     * signal, and the card (index.html) shows the refresh command when
     * this reads `true`.
     *
     * A DIGEST comparison, deliberately not the version-STRING comparison
     * this used to be: `.github/workflows/ci.yml`'s `updater-image` job
     * bakes the release version into every tagged image unconditionally,
     * so a version-string comparison flags "newer" on every single
     * release - including one that never touched deploy/updater/ at all,
     * where the image is behaviourally identical and there is nothing to
     * refresh. The digest is the manifest GHCR would actually serve right
     * now for `:stable` (`published_updater_digest`, resolved on the
     * bridge - see `update_check.resolve_updater_digest`) against the one
     * the running sidecar container actually has
     * (`updater_digest`, `update-once.sh`'s own `updater_digest()`) -
     * either matches or it does not, with nothing left to guess.
     *
     * `false`, not "unknown", whenever either digest is missing: a
     * sidecar built before this field existed, one built locally rather
     * than pulled (no `RepoDigests` entry at all), checking switched off,
     * or GHCR unreachable right now, must not be nagged about a problem
     * that cannot be measured - an unknown is not a mismatch. `updater_version`/
     * `versionInfo.version` stay exactly what they were: not part of this
     * decision any more, only of the message `updaterRefreshMessage()`
     * below builds once this reads `true`. */
    updaterVersionBehind() {
      const updaterDigest = this.updateStatus?.state?.updater_digest;
      const publishedDigest = this.updateStatus?.published_updater_digest;
      return Boolean(updaterDigest) && Boolean(publishedDigest) && updaterDigest !== publishedDigest;
    },

    /** The TEXT of the "refresh the updater" warning `updaterVersionBehind()`
     * (above) decides whether to show at all - `updater_version`/
     * `versionInfo.version` still name what changed, exactly as before the
     * digest-based trigger replaced the version-based one (see that
     * method's own comment).
     *
     * The COMMAND half depends on whether the sidecar could resolve its
     * own `$LOXMATTER_STACK` to a HOST path
     * (`updater_stack_host_path` - update-once.sh's `host_path_for()`, via
     * entrypoint.sh's one-time resolution at container start). A
     * documentation-guessed path is exactly what this feature replaces:
     * `~/loxmatter/deploy/testhost` is wrong on any checkout not named
     * `loxmatter` (the maintainer's own is `~/matter-loxone`). When the
     * sidecar cannot resolve its own mount table either, this prints no
     * path at all rather than fabricate one - `web.system.updater_behind_unknown_path`
     * says WHERE to run the command in words instead. */
    updaterRefreshMessage() {
      const path = this.updateStatus?.state?.updater_stack_host_path;
      const params = {
        updater_version: this.updateStatus?.state?.updater_version,
        version: this.versionInfo?.version,
      };
      return path
        ? t("web.system.updater_behind", { ...params, path })
        : t("web.system.updater_behind_unknown_path", params);
    },

    /** Whether `state.json` still claims a job is running while the
     * sidecar itself has gone silent - `updater_present` (see
     * `update.py`'s own docstring and `_MAX_SILENT_SECONDS`) already
     * folds "no heartbeat in the last 30 seconds" into one boolean on
     * every `/api/update/status` response, so this only has to combine
     * it with `updateRunning()`. Without this check a crashed sidecar
     * (OOM, a full disk) is indistinguishable from a healthy one still
     * working through its steps: `phase` never advances once nothing is
     * left to write it, so the card would otherwise keep highlighting
     * the same step forever with no explanation for why it stopped
     * moving. */
    updateStalled() {
      return this.updateRunning() && this.updateStatus != null && !this.updateStatus.updater_present;
    },

    /** True from the moment `applyUpdate()`'s POST succeeds until either
     * `state.json` reports this exact job's `id` (see `loadUpdateStatus()`)
     * or `UPDATE_APPLY_GRACE_MS` runs out, whichever happens first. This is
     * the race this fix closes: the sidecar's own loop only wakes once
     * every two seconds (deploy/updater/entrypoint.sh), so a poll landing
     * in that window would otherwise see `updateRunning()` read `false` -
     * exactly the previous end state, not this request - and stop the
     * timer, going blind for the rest of a job that is in fact under way.
     * Every caller that decides whether to keep polling now checks this
     * alongside `updateRunning()` so that stale read cannot do that. */
    updateAwaitingPickup() {
      return this.updateApplyDeadline !== null && Date.now() < this.updateApplyDeadline;
    },

    /** The other half of the same race: the grace window above ran out and
     * `state.json` never once reported this apply's job id. This is the
     * honest reading of "the sidecar crashed between the 503 presence
     * check in `api/update.py`'s `apply()` and actually reading
     * request.json" - `updater_present` alone cannot tell that story in
     * time, since its own heartbeat can still look fresh for up to 30
     * seconds (`_MAX_SILENT_SECONDS` in update.py) after a crash that
     * happened right after the last one was written. Deliberately its own
     * predicate rather than folded into `updateStalled()`: that one means
     * "was running, then went quiet mid-step", worded and rendered (see
     * index.html) around an actual step list this request never reached -
     * conflating the two would either show step progress that never
     * happened or a message that refers to a step nobody can see.
     *
     * Reads `updateApplyMissed`, NOT a fresh `Date.now()` comparison against
     * `updateApplyDeadline` - the two were equivalent right up until the
     * moment this predicate actually flips, which is precisely the moment
     * that equivalence stops helping: Alpine's `x-show="updateNeverCollected()"`
     * (index.html) only re-evaluates when a reactive property IT read on a
     * PREVIOUS run later changes, and `Date.now()` is never such a
     * property. The one poll where a time-based version of this method
     * would first return `true` is also the poll where `loadUpdateStatus()`
     * lets the timer stop (nothing left to await) - so no future tick would
     * ever call this method again to notice, and the banner would stay
     * hidden behind the value computed one poll earlier: `false`. Reading a
     * plain boolean field instead means the ONE write to it, made below in
     * `loadUpdateStatus()` at the exact moment the deadline passes, is
     * itself the reactive event Alpine's `x-show` needs. */
    updateNeverCollected() {
      return this.updateApplyMissed;
    },

    stopUpdateTimer() {
      if (this.updateTimer) {
        clearInterval(this.updateTimer);
        this.updateTimer = null;
      }
    },

    /** Arms the poll timer if it is not already running - idempotent, so
     * every caller (the interval-driven arm below AND `applyUpdate`'s own
     * unconditional call, see there) can call this without first checking
     * `this.updateTimer` itself, and without ever ending up with two
     * `setInterval`s ticking against the same state. */
    startUpdateTimer() {
      if (!this.updateTimer) {
        this.updateTimer = setInterval(() => this.loadUpdateStatus(), 2000);
      }
    },

    /**
     * `allowStop` exists for exactly one caller: `applyUpdate`'s own
     * immediate read right after the POST (Critical 3). The sidecar's
     * loop wakes at most every two seconds (update-once.sh's own
     * `entrypoint.sh` cadence), so `state.json` - and with it this very
     * read - can still report the PREVIOUS end state (`idle`/`done`/
     * `failed`) for up to that long after the POST already succeeded.
     * `updateRunning()` reading `false` off that stale state must not be
     * mistaken for "nothing is running" - the POST already got a 2xx,
     * meaning the job was accepted. Proven end to end in a node harness
     * replaying the real methods: with the timer armed only through the
     * branch below (i.e. only once THIS function itself already sees a
     * running phase), the only requests after the click were the POST
     * and one status GET, `setInterval` was called zero times, and the
     * card kept showing "Install update" while the job actually ran -
     * worse, `updateRunning()` staying `false` also un-suppressed the
     * red "connection lost" banner, the exact outcome the update-specific
     * banner exists to prevent.
     *
     * `applyUpdate` arms the timer itself, unconditionally, BEFORE this
     * call - so the stop branch below, reached on this same stale read,
     * must not undo that arming. Every OTHER caller (the timer's own
     * interval tick, and the cold-load path through `loadSystem`) has no
     * such freshly-armed timer to protect and keeps the default
     * `allowStop: true` - once the job it already knows about genuinely
     * ends, THAT read is exactly what should turn the timer back off. */
    async loadUpdateStatus({ allowStop = true } = {}) {
      try {
        this.updateStatus = await this.request("GET", "/api/update/status");
        this.updateError = null;
        // Catches an update this page did not start - another tab, or a
        // phone - so its result still gets announced here. See
        // `updateWatchedJobId`.
        if (this.updateRunning() && this.updateStatus?.state?.id) {
          this.updateWatchedJobId = this.updateStatus.state.id;
        }
        // The moment `state.json`'s own `id` matches the job this
        // pending apply is waiting on, the race `updateAwaitingPickup()`
        // exists for is over - the sidecar has genuinely read
        // request.json, whatever phase it wrote next (even straight to
        // `rejected`). Clearing both fields here, unconditionally,
        // rather than only when `updateRunning()` is true: a request
        // that gets rejected on the spot never passes through a running
        // phase at all, and must not be left "awaiting pickup" for the
        // remainder of `UPDATE_APPLY_GRACE_MS` regardless.
        if (this.updateApplyJobId !== null && this.updateStatus?.state?.id === this.updateApplyJobId) {
          this.updateApplyJobId = null;
          this.updateApplyDeadline = null;
        }
      } catch (error) {
        // A 401 here means the session expired or was ended elsewhere -
        // `this.request()` has already flipped `this.authenticated` to
        // `false` and set `this.authError` by the time this `catch`
        // runs (see `noteAuthError`). Same rule `handleDiagnosticsDisconnect`
        // states for the diagnostics socket, one screen away: go back to
        // the login screen instead of retrying against a session that
        // will never answer again. Without this check, `updateRunning()`
        // kept reading `true` off the last good state fetched before the
        // session died - the branch below saw nothing wrong with that and
        // left the timer running, firing this same request every two
        // seconds against a dead session until the page was reloaded by
        // hand. An explicit logout is unaffected: it reloads the page
        // and clears all JS state, this timer included, before any of
        // this can run again.
        if (!this.authenticated) {
          this.stopUpdateTimer();
          return;
        }
        // During the restart the bridge itself is gone - that is the
        // normal case for this flow, not an error (the connection banner
        // in index.html carries the message for it). The last known
        // state stays on screen and the timer keeps trying. Only a
        // failure OUTSIDE a running job or a pending pickup - the bridge
        // is simply down, or there never was a job - is worth
        // `updateError`.
        if (!this.updateRunning() && !this.updateAwaitingPickup()) {
          this.updateError = error.message;
        }
      }
      // The reactive write `updateNeverCollected()` reads (see that
      // method's own comment). Placed here, ahead of the stop-timer
      // decision just below, because this IS "the same place that decides
      // to stop the timer" for exactly this case: a poll that still finds
      // `updateApplyJobId` set (no id match arrived, in the `try` above or
      // any earlier one) with `updateApplyDeadline` now in the past is
      // about to let `updateAwaitingPickup()` read `false` and, with
      // `updateRunning()` also `false` (nothing ever started), fall into
      // the stop branch below - the last moment ANY code runs for this
      // apply attempt. `Date.now()` is read here, this one time, by
      // ordinary function-call code that unconditionally executes on every
      // poll; the write it produces is what makes the fact durable and
      // Alpine-visible, not the read itself. Independent of whether the
      // `try` above succeeded: a bridge that stops answering entirely is
      // exactly as "never collected" as one that answers but never shows
      // the job.
      if (this.updateApplyJobId !== null && this.updateApplyDeadline !== null && Date.now() >= this.updateApplyDeadline) {
        this.updateApplyMissed = true;
        this.updateApplyJobId = null;
        this.updateApplyDeadline = null;
      }
      // `updateAwaitingPickup()` alongside `updateRunning()` in both
      // branches below is this fix's own core: a poll landing before the
      // sidecar has caught up sees `updateRunning()` read `false` off the
      // previous end state, same as before this fix - what changes is
      // that the timer no longer treats that as "nothing to watch for"
      // while a request is still within its grace window. Once the
      // window closes without a match, `updateAwaitingPickup()` itself
      // turns `false` (see its own comment) and the branch below
      // correctly gives up, exactly as the second half of this fix
      // requires.
      if (this.updateRunning() || this.updateAwaitingPickup()) {
        this.startUpdateTimer();
      }
      if (allowStop && !this.updateRunning() && !this.updateAwaitingPickup() && this.updateTimer) {
        this.stopUpdateTimer();
        // Fetch the version once more after the end: the card up top
        // should show the new number, not the one the page loaded with.
        this.versionInfo = await this.request("GET", "/api/version");
        // Review finding, 2026-09-09: `updateAvailable` used to keep
        // holding whatever offer this same job had just accepted -
        // nothing re-ran `loadUpdateCheck()` when a job ended, so the card
        // kept showing "Version X available" and an "Install update"
        // button right beside the "Now running: X" banner this very block
        // just made accurate above. This `this.updateTimer` truthy check
        // is exactly the "a job WAS running and just reached a terminal
        // phase" transition (see the comment above `updateRunning()`), so
        // it covers `done` (offer installed, almost certainly gone now)
        // AND `failed`/`rollback`'s own end state (the offer may well
        // still be valid, and the whole point of refreshing here is
        // letting the user retry with a check that reflects reality,
        // rather than leave them looking at a now-stale offer either way).
        // A plain rejection never runs this branch at all if it never
        // passed through a running phase - `updateStatus?.state?.phase
        // === 'rejected'` (index.html) already renders on its own from
        // `updateStatus` alone, no offer refresh needed for that case.
        await this.loadUpdateCheck();
      }
    },

    async loadUpdateCheck() {
      try {
        this.updateAvailable = await this.request("GET", "/api/update/check");
      } catch (error) {
        this.updateAvailable = { target: null, error: error.message };
      }
    },

    async applyUpdate() {
      this.updateConfirming = false;
      this.updateError = null;
      // A fresh attempt starts here, before the POST even lands - clear the
      // PREVIOUS attempt's `updateApplyMissed` now rather than wait for
      // this attempt to resolve one way or the other. Otherwise a retry
      // that is itself picked up promptly would still render the "never
      // collected" banner (index.html) for the up-to-`UPDATE_APPLY_GRACE_MS`
      // stretch until the id-match branch below clears `updateApplyJobId`/
      // `updateApplyDeadline` - a true fact about the LAST attempt wrongly
      // presented as still true about this one.
      this.updateApplyMissed = false;
      try {
        // `target` comes from `updateAvailable`, not from anything typed
        // in this dialog - Section 10 of the update design puts the
        // actual validation in the sidecar, and the one thing this route
        // must not do is offer a free-text field that reaches it.
        const accepted = await this.request("POST", "/api/update/apply", {
          target: this.updateAvailable.target,
        });
        // Remaining Stufe 2 gap: the fix above (arming the timer here,
        // unconditionally) stops the FIRST poll from undoing it, but does
        // nothing about the SECOND one, two seconds later, and every one
        // after that up to `UPDATE_APPLY_GRACE_MS` - each lands on
        // `loadUpdateStatus()`'s default `allowStop: true` and re-reads
        // `state.json` fresh. On a lightly loaded sidecar that read
        // already shows a running phase by then and none of this matters.
        // On one that is mid-cleanup of the pass before this request
        // arrived, or just slow, it can still be the previous end state -
        // and `updateAwaitingPickup()`/`updateNeverCollected()` (see
        // their own comments) are what keep those later polls from
        // making the exact same mistake the first poll used to.
        // `accepted.id` is the job id `api/update.py`'s
        // `apply()` route already hands back in its response body -
        // `update.py`'s own `request_update()` writes the identical value
        // into `request.json`, and the sidecar copies it into
        // `state.json`'s `id` field the instant it reads that file (see
        // `update-once.sh`'s `JOB_ID`/`set_state queued ""`) - so it is
        // the one signal that survives every possible phase the sidecar
        // could write next, rejection included.
        this.updateApplyJobId = accepted.id;
        this.updateWatchedJobId = accepted.id;
        this.updateApplyDeadline = Date.now() + UPDATE_APPLY_GRACE_MS;
        // Critical 3: arm the timer HERE, unconditionally, the moment the
        // POST itself succeeds - not by relying on the `loadUpdateStatus`
        // call right below to notice a running phase and arm it as a side
        // effect. `startUpdateTimer` is idempotent (see its own comment),
        // so this can never end up racing a SECOND `setInterval` against
        // whatever the cold-load/interval path already armed.
        this.startUpdateTimer();
        // `allowStop: false`: the read below can still land on the
        // sidecar's PREVIOUS end state (see `loadUpdateStatus`'s own
        // comment on this parameter) - including a read that fails
        // outright, e.g. a transient network hiccup right after the
        // POST. Either way `updateRunning()` off that stale/missing read
        // may say `false`, but the timer just armed above must survive
        // it regardless: the POST already succeeded, a job is queued.
        // From here on `updateApplyDeadline`, set just above, is what
        // keeps every LATER poll (this function is done after this one
        // call) from repeating the same mistake for up to
        // `UPDATE_APPLY_GRACE_MS`, and `updateNeverCollected()` is what
        // makes the card say something true if that whole window passes
        // with the sidecar never having read the request at all.
        await this.loadUpdateStatus({ allowStop: false });
      } catch (error) {
        // `error.message` is already the specific, human sentence the
        // backend chose for this exact refusal (busy, no updater, disk
        // full - see api/update.py's module docstring for why a 409 and
        // a 503 each carry their own text rather than a generic one) -
        // showing it as-is is the useful answer, not a rewrite of it.
        this.updateError = error.message;
      }
    },

    async setUpdateChannel(channel) {
      this.updateError = null;
      try {
        this.updateStatus = await this.request("PATCH", "/api/update/settings", { channel });
        await this.loadUpdateCheck();
      } catch (error) {
        // Every sibling method in this card (`applyUpdate`, `resyncAll`,
        // `downloadFabricBackup`, ...) funnels a failure into a visible
        // error - this one, alone, awaited the PATCH with no `try` at
        // all. A rejected request became an unhandled rejection straight
        // out of the `@click` handler in index.html: nothing shown, the
        // channel silently left as it was, and no way for whoever
        // clicked to tell the click did anything.
        this.updateError = error.message;
      }
    },

    // Also no longer an `<a href>` (see `downloadExport`): an error
    // (e.g. 503 with no data directory mounted) should appear as a
    // readable message, instead of resulting in a downloaded file that is
    // actually an error message.
    async downloadFabricBackup() {
      this.backupError = null;
      try {
        await this.download("/api/diagnostics/fabric-backup", "matter-fabric-backup.zip");
      } catch (error) {
        this.backupError = t("web.system.backup_error", { message: error.message });
      }
    },

    /**
     * Sends all known values to the Miniserver again - the same thing
     * that happens on bridge startup and when `/resync` is called from
     * the Config project. Goes via `POST /api/diagnostics/resync` and NOT
     * via `/resync` itself: `/resync` deliberately lives outside `/api`
     * and thus outside the guard, because the Miniserver cannot send an
     * `Authorization` header along. This UI certainly can, and
     * `this.request` brings the 401 handling with it, without which an
     * expired session here would appear as "Resend failed" instead of the
     * login screen.
     *
     * The count from the response goes into a toast: without it, a
     * successful resync cannot be told apart from one that had nothing to
     * send - both would look like a button that was briefly gray.
     */
    async resyncAll() {
      this.resyncError = null;
      this.resyncBusy = true;
      try {
        const result = await this.request("POST", "/api/diagnostics/resync");
        this.showToast(t("web.system.resync_toast", { count: result.sent }));
      } catch (error) {
        this.resyncError = t("web.system.resync_error", { message: error.message });
      } finally {
        this.resyncBusy = false;
      }
    },

    // ---------------------------------------------------------------------
    // Project file sync (Task 12)
    // ---------------------------------------------------------------------

    /**
     * Sends `file` to `/api/export/project-sync`, optionally with a
     * `miniserverIp` already chosen. Shared core of `uploadProjectFile`
     * (first attempt, without an IP) and `confirmProjectSyncMiniserver`
     * (second attempt, after the user has chosen a Miniserver in the
     * selection field) - both display the same response, only the caller
     * decides whether an IP is already fixed.
     *
     * `needs_miniserver_selection=true` in the response (user request
     * after the review: selecting instead of typing the IP by hand)
     * means: the file carries more than one Miniserver, `index.html` then
     * shows this selection field instead of a plan - not an error,
     * `projectSync.error` stays empty.
     */
    async _syncProjectFile(file, miniserverIp) {
      this.projectSync.error = "";
      this.projectSync.busy = true;
      this.projectSync.plan = null;
      try {
        const formData = new FormData();
        formData.append("file", file);
        const params = new URLSearchParams({
          bridge_ip: this.bridgeSettings.bridge_ip,
          port: String(this.bridgeSettings.udp_port),
          listen: String(this.bridgeSettings.listen_port),
        });
        if (miniserverIp) {
          params.set("miniserver_ip", miniserverIp);
        }
        const result = await this.upload(`/api/export/project-sync?${params}`, formData);
        if (result.needs_miniserver_selection) {
          this.projectSync.needsMiniserverSelection = true;
          this.projectSync.availableMiniservers = result.available_miniservers;
        } else {
          this.projectSync.needsMiniserverSelection = false;
          this.projectSync.availableMiniservers = [];
          this.projectSync.plan = result;
        }
      } catch (error) {
        this.projectSync.error = t("web.export.projectsync_upload_failed", { message: error.message });
      } finally {
        this.projectSync.busy = false;
      }
    },

    /**
     * Uploads the uploaded Loxone project file to
     * `/api/export/project-sync` and displays the response (plan + both
     * patched files, or the Miniserver selection field). The same IP
     * check as `downloadExport`/`exportDevice`: without it, a click with
     * an empty IP field replaced the page with the backend's raw 422
     * error response (required parameter `bridge_ip`).
     */
    async uploadProjectFile(event) {
      const input = event.target;
      const file = input.files && input.files[0];
      if (!file) {
        return;
      }
      if (!this.bridgeSettings.bridge_ip) {
        this.projectSync.error = t("web.export.bridge_ip_missing");
        input.value = "";
        return;
      }
      // Cleared in case a previous upload had already required a
      // selection - a newly selected file starts over from zero,
      // regardless of whether the previous one had several Miniservers.
      this.projectSync.file = file;
      this.projectSync.needsMiniserverSelection = false;
      this.projectSync.availableMiniservers = [];
      this.projectSync.selectedMiniserverIp = "";
      await this._syncProjectFile(file, null);
      // Clears the file selection in the input field itself - without
      // this, a repeated upload of the SAME file no longer fires a
      // `change` event, because the field's value has not changed from
      // the browser's point of view.
      input.value = "";
    },

    /**
     * Second attempt, after the user has chosen a Miniserver in the
     * selection field - the same file (`projectSync.file`, still in the
     * browser's memory) goes out a second time, this time with
     * `miniserver_ip` set, no repeated file dialog needed.
     */
    async confirmProjectSyncMiniserver() {
      if (!this.projectSync.selectedMiniserverIp || !this.projectSync.file) {
        return;
      }
      await this._syncProjectFile(this.projectSync.file, this.projectSync.selectedMiniserverIp);
    },

    /** Short display label for the `PlanStatus` values from
     * `projectsync/diff.py` (`unchanged`, `updated`, `new_signal`,
     * `new_device`, `orphaned`, `conflict`, `possible_duplicate`) - the
     * raw values are English (this project's identifier convention, see
     * the comment at the top of this file), but must not land untranslated
     * on screen. */
    projectSyncStatusLabel(status) {
      const labels = {
        unchanged: t("web.export.projectsync_status_unchanged"),
        updated: t("web.export.projectsync_status_updated"),
        new_signal: t("web.export.projectsync_status_new_signal"),
        new_device: t("web.export.projectsync_status_new_device"),
        orphaned: t("web.export.projectsync_status_orphaned"),
        conflict: t("web.export.projectsync_status_conflict"),
        possible_duplicate: t("web.export.projectsync_status_possible_duplicate"),
      };
      return labels[status] || status;
    },

    /** Badge color for `projectSyncStatusLabel`. Four separate cases
     * instead of three previously (user request after the review:
     * new/updated must be distinguishable at a glance, not both collapse
     * into `warn`) - `ok` (green) for everything new is the same color
     * language as an added diff in a version control system, `warn` stays
     * exclusive to `updated`, `off` (the same neutral color as a device
     * card in the "offline" state) for `orphaned`, `danger` for
     * `conflict` AND `possible_duplicate` (neither is ever auto-created/
     * auto-adopted, both deserve the same "look at this" color).
     * `unchanged` no longer needs a badge here - it now only appears as a
     * plain chip, see `projectSyncSplitBySignificance`. */
    projectSyncStatusBadgeClass(status) {
      if (status === "conflict" || status === "possible_duplicate") {
        return "danger";
      }
      if (status === "orphaned") {
        return "off";
      }
      if (status === "updated") {
        return "warn";
      }
      return "ok";
    },

    /** Maps a plan status to one of five collection buckets - the same
     * classification underlies both the per-device counters
     * (`projectSyncGroupedEntries`) and the overall summary above
     * (`projectSyncOverallCounts`), as well as each entry row's CSS class
     * (`is-<bucket>`) - a single mapping instead of several that could
     * drift apart. `possible_duplicate` shares the `conflict` bucket:
     * both mean "something here is not right, please check", only the
     * label and explanation text (`projectSyncStatusLabel`/
     * `projectSyncEntryNote`) distinguishes them for the user. */
    projectSyncStatusBucket(status) {
      if (status === "new_signal" || status === "new_device") {
        return "new";
      }
      if (status === "updated") {
        return "updated";
      }
      if (status === "orphaned") {
        return "orphaned";
      }
      if (status === "conflict" || status === "possible_duplicate") {
        return "conflict";
      }
      return "unchanged";
    },

    /**
     * Groups the flat plan by owner and, within that, again by input/
     * output - exactly the nesting in which the signals later end up as
     * virtual inputs/outputs in Loxone Config (one container per device
     * or group, `Inputs` and `Outputs` as separate groups underneath it),
     * instead of one single long, unsorted list.
     *
     * Keyed on `owner_kind` + `device_id` TOGETHER, not `device_id` alone:
     * a group and a device can carry the same numeric id (both counters
     * start at 1, design 2026-09-10, section 8), and on a first
     * installation - the normal case, not an edge one - the first device
     * and the first group both get id 1. Keying on the id alone would
     * merge a group's outputs into the same-numbered device's card, under
     * the device's label (devices are planned first, so the device's
     * label would win the merge). A group's card is labelled via
     * `web.export.projectsync_group_label` so it reads as a group even
     * when its id collides with a device's.
     *
     * Orphaned entries (`device_id === -1`, see `PlanEntry` in `diff.py` -
     * no longer belong to any currently known device or group) get their
     * own group with no real owner name and are deliberately placed at
     * the end, regardless of their position in the flat plan. Orphaned
     * entries always carry `owner_kind === "device"` (the default in
     * `PlanEntry`, `diff.py`) regardless of whether they used to belong to
     * a device or a group - the object no longer maps to anything in the
     * current index, so there is nothing left to attribute it to, and
     * both cases share this one "no longer assigned" bucket by design.
     *
     * Each group additionally carries `counts` (per status bucket, for the
     * count chips in the collapsible card header) and `needsAttention`
     * (everything except `unchanged` - controls whether the card is
     * already expanded on first display). `sections` bundles inputs/
     * outputs already pre-sorted into "needs a look" vs. "unchanged,
     * collapsed" (`projectSyncSplitBySignificance`) - computed once here
     * instead of again on every render in the template.
     */
    projectSyncGroupedEntries(entries) {
      const groups = [];
      const byOwnerKey = new Map();
      for (const entry of entries || []) {
        const ownerKind = entry.owner_kind || "device";
        const ownerKey = `${ownerKind}:${entry.device_id}`;
        let group = byOwnerKey.get(ownerKey);
        if (!group) {
          const rawLabel = entry.device_label || "—";
          group = {
            key: ownerKey,
            deviceId: entry.device_id,
            ownerKind,
            deviceLabel:
              entry.device_id === -1
                ? t("web.export.projectsync_unassigned_device_label")
                : ownerKind === "group"
                  ? t("web.export.projectsync_group_label", { label: rawLabel })
                  : rawLabel,
            inputs: [],
            outputs: [],
            counts: { new: 0, updated: 0, unchanged: 0, orphaned: 0, conflict: 0 },
          };
          byOwnerKey.set(ownerKey, group);
          groups.push(group);
        }
        (entry.kind === "input" ? group.inputs : group.outputs).push(entry);
        group.counts[this.projectSyncStatusBucket(entry.status)] += 1;
      }
      groups.sort((a, b) => (a.deviceId === -1 ? 1 : 0) - (b.deviceId === -1 ? 1 : 0));
      for (const group of groups) {
        group.needsAttention =
          group.counts.new + group.counts.updated + group.counts.orphaned + group.counts.conflict >
          0;
        group.sections = [
          {
            label: t("web.export.projectsync_section_inputs"),
            ...this.projectSyncSplitBySignificance(group.inputs),
          },
          {
            label: t("web.export.projectsync_section_outputs"),
            ...this.projectSyncSplitBySignificance(group.outputs),
          },
        ];
      }
      return groups;
    },

    /** Splits a list of entries into `attention` (everything except
     * `unchanged` - always shown as its own row with status and, where
     * applicable, a diff) and `unchanged` (now only shown as a plain chip
     * behind a collapsed summary, see `index.html`) - for a real file that
     * has grown over years, that quickly amounts to dozens of signals that
     * have long been correct and would only get in the way of the overview
     * (user request: "faster and cleaner overview"). */
    projectSyncSplitBySignificance(items) {
      const attention = [];
      const unchanged = [];
      for (const entry of items) {
        (entry.status === "unchanged" ? unchanged : attention).push(entry);
      }
      return { attention, unchanged };
    },

    /** Total count per status bucket across the whole plan - the basis
     * for the summary row right at the top, before clicking through the
     * individual device cards. */
    projectSyncOverallCounts(entries) {
      const counts = { new: 0, updated: 0, unchanged: 0, orphaned: 0, conflict: 0 };
      for (const entry of entries || []) {
        counts[this.projectSyncStatusBucket(entry.status)] += 1;
      }
      return counts;
    },

    /** For the "everything up to date" message (Review-Fix Important #5):
     * `orphaned`, `conflict`, and `possible_duplicate` are informational
     * and are never patched (see `SyncPlan.has_changes` in `diff.py`,
     * which deliberately excludes exactly these statuses), but still need
     * to stay visible even though `has_changes` is therefore `false`. */
    projectSyncHasInformationalEntries(entries) {
      return (entries || []).some((entry) =>
        ["orphaned", "conflict", "possible_duplicate"].includes(entry.status),
      );
    },

    /** Short explanatory sentence under an entry row's title - makes
     * `new_device` (a complete new container) and `new_signal` (just one
     * new command in an existing container) distinguishable at a glance,
     * without the user first having to look up the difference between the
     * two badge texts (user request: see "which nodes + commands" are
     * newly added). `possible_duplicate` (user report "onoff shows up
     * twice"): an existing command with the same title was found, but
     * under a different key - more likely a damaged old object than a
     * genuinely new signal, hence no automatic creation. */
    projectSyncEntryNote(entry) {
      if (entry.status === "new_device") {
        return t("web.export.projectsync_note_new_device");
      }
      if (entry.status === "new_signal") {
        return t("web.export.projectsync_note_new_signal");
      }
      if (entry.status === "orphaned") {
        return t("web.export.projectsync_note_orphaned");
      }
      if (entry.status === "conflict") {
        return t("web.export.projectsync_note_conflict");
      }
      if (entry.status === "possible_duplicate") {
        return t("web.export.projectsync_note_possible_duplicate");
      }
      return "";
    },

    /** Display label for the attribute names from `entry.changes` - the
     * same keys as `MANAGED_INPUT_CMD_ATTRS`/`MANAGED_OUTPUT_CMD_ATTRS` in
     * `projectsync/schema.py`. Unknown names (should not happen) appear
     * untranslated instead of vanishing. */
    projectSyncAttrLabel(attr) {
      const labels = {
        Title: t("web.export.projectsync_attr_title"),
        Check: t("web.export.projectsync_attr_check"),
        Analog: t("web.export.projectsync_attr_analog"),
        Unit: t("web.export.projectsync_attr_unit"),
        CmdOn: t("web.export.projectsync_attr_cmd_on"),
        CmdOff: t("web.export.projectsync_attr_cmd_off"),
      };
      return labels[attr] || attr;
    },

    /**
     * Turns `entry.changes` (only filled for `status === "updated"`,
     * otherwise empty - see `ProjectSyncEntryOut` in `api/models.py`) into
     * a list of `{label, oldValue, newValue}` for the diff rows in the
     * template (Review-Fix Important #6, now structured instead of a
     * single block of running text, so the old and new value can be
     * styled separately). Plain `x-text` in the template, never `x-html`:
     * the values come from the uploaded project file and are not
     * trustworthy.
     */
    projectSyncChangeList(entry) {
      const changes = entry.changes || {};
      return Object.entries(changes).map(([attr, values]) => {
        const [oldValue, newValue] = values;
        return { label: this.projectSyncAttrLabel(attr), oldValue, newValue };
      });
    },

    /**
     * Builds the blob from the base64-encoded file that was already part
     * of the plan response (no second call to the bridge needed) - the
     * "Also create new device containers" checkbox only selects WHICH of
     * the two supplied versions gets downloaded.
     *
     * `patched_with_new_devices_base64` can be `null` if the uploaded file
     * has no `VirtualInCaption`/`VirtualOutCaption` section for a new
     * device container (`new_devices_unavailable_reason` then carries the
     * reason, displayed in `index.html`). The checkbox is already
     * disabled in that case - this check here is only the second line of
     * defense in case it is checked anyway, and quietly falls back to the
     * conservative version instead of triggering `atob(null)`.
     */
    downloadPatchedProject() {
      if (!this.projectSync.plan) {
        return;
      }
      const wantsNewDevices = this.projectSync.includeNewDevices;
      const base64 =
        wantsNewDevices && this.projectSync.plan.patched_with_new_devices_base64
          ? this.projectSync.plan.patched_with_new_devices_base64
          : this.projectSync.plan.patched_conservative_base64;
      const isNewDevicesVariant = wantsNewDevices && Boolean(this.projectSync.plan.patched_with_new_devices_base64);
      const blob = blobFromBase64(base64, "application/xml");
      const objectUrl = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = objectUrl;
      link.download = isNewDevicesVariant
        ? "loxmatter-project-patched-with-new-devices.Loxone"
        : "loxmatter-project-patched.Loxone";
      link.click();
      // Delayed release like in `requestDownload` above - some browsers
      // (Firefox) only start the download of an object URL after the
      // current call stack.
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
    },

    // ---------------------------------------------------------------------
    // Live diagnostics (Task 6, Spec 10.5)
    // ---------------------------------------------------------------------

    /**
     * Opens the diagnostics channel (`/api/diagnostics/live`) - the same
     * clean-up-before-rebuild pattern as `connectLive()` for the value
     * channel (see there for the detailed rationale, not repeated here):
     * an old timer is stopped first, an old socket is first removed from
     * `this.diagnosticsSocket` and THEN closed, so that its `close` event
     * hits a field that has already been swapped out and does not trigger
     * a second reconnection.
     *
     * No subprotocol (review of the task statement, see
     * `.superpowers/sdd/task-6-report.md`): contrary to what the task text
     * assumed, `connectLive()` no longer carries a `["bearer", token]`
     * subprotocol since the WebUI login - the session cookie travels along
     * on its own for a WebSocket to the same origin (see its comment).
     * This channel hangs off exactly the same `build_api_guard` as
     * `/api/live` (`loxone/server.py`) and therefore needs the same,
     * simpler path - inventing a second subprotocol would be a deviation
     * from the model, not a following of it.
     *
     * **Clears all three streams BEFORE the new connection is built**
     * (follow-up fix Task 6, 2026-09-03): every (re)connection gets a
     * snapshot of up to `SNAPSHOT_LIMIT` entries per stream from
     * `api/diagnostics_live.py`, in exactly the same message shape as a
     * running line and with no marker of its own identifying it as a
     * snapshot. Without this clearing, that snapshot would simply attach
     * to what was already held - switching away from "System" and back,
     * or any automatic reconnection after a network hiccup, would have
     * appended up to 150 already-present lines a second time, on the most
     * ordinary path through the UI. The simpler of the two possible
     * approaches compared to a server-side marking of the snapshot:
     * `clearDiagnosticsBuffers()` (the same function the "Clear" button
     * also calls) simply makes the "snapshot vs. running line" distinction
     * unnecessary in the browser, instead of reproducing it there - no new
     * message shape, no merging of two sources on display. The cost: a
     * reconnection also discards lines older than the last
     * `SNAPSHOT_LIMIT` per stream (50) that are NOT replaced by the
     * following snapshot - for a diagnostics view whose "Clear" button
     * already deliberately offers exactly that at any time anyway, this
     * is not a new risk, just the same loss at one additional point in
     * time.
     */
    connectDiagnosticsLive() {
      if (this.diagnosticsReconnectTimer !== null) {
        window.clearTimeout(this.diagnosticsReconnectTimer);
        this.diagnosticsReconnectTimer = null;
      }
      const previous = this.diagnosticsSocket;
      this.diagnosticsSocket = null;
      this.diagnosticsConnected = false;
      if (previous) {
        previous.close();
      }
      this.clearDiagnosticsBuffers();

      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      const url = `${protocol}//${window.location.host}/api/diagnostics/live`;
      const socket = new WebSocket(url);

      socket.addEventListener("open", () => {
        this.diagnosticsConnected = true;
        this.diagnosticsReconnectDelayMs = RECONNECT_DELAY_INITIAL_MS;
      });

      socket.addEventListener("message", (event) => {
        this.handleDiagnosticsMessage(JSON.parse(event.data));
      });

      // The same `this.diagnosticsSocket === socket` check as in
      // `connectLive()`: only a connection that still counts as THE
      // current one may trigger a reconnection - a deliberately closed
      // one (see `disconnectDiagnosticsLive`) no longer has `this.
      // diagnosticsSocket` set by that point.
      socket.addEventListener("close", () => {
        if (this.diagnosticsSocket === socket) {
          this.handleDiagnosticsDisconnect();
        }
      });
      socket.addEventListener("error", () => socket.close());

      this.diagnosticsSocket = socket;
    },

    /**
     * Closes the diagnostics channel and stops any scheduled reconnection -
     * called by `selectView` as soon as "System" is no longer the active
     * view (trap 3: exactly one connection, only while the view is open).
     * Sets `this.diagnosticsSocket` to `null` BEFORE the `close()` call,
     * so the `close` handler above does not answer this disconnection with
     * a reconnection.
     */
    disconnectDiagnosticsLive() {
      if (this.diagnosticsReconnectTimer !== null) {
        window.clearTimeout(this.diagnosticsReconnectTimer);
        this.diagnosticsReconnectTimer = null;
      }
      const socket = this.diagnosticsSocket;
      this.diagnosticsSocket = null;
      this.diagnosticsConnected = false;
      if (socket) {
        socket.close();
      }
    },

    /**
     * Reacts to the diagnostics channel dropping - the equivalent of
     * `handleLiveDisconnect` for the value channel (see there), measured
     * against this view instead of the session: the view can have been
     * left long before this `close` event arrives (asynchronous), and
     * `disconnectDiagnosticsLive` has already cleared `this.
     * diagnosticsSocket` by then - the `close` handler above does not even
     * call this function in that case. If it still hits a view that has
     * meanwhile been left (e.g. a switch happening exactly while this call
     * is already running), it aborts here instead of continuing to retry
     * in the background.
     */
    async handleDiagnosticsDisconnect() {
      this.diagnosticsConnected = false;
      if (this.view !== "system") {
        return;
      }
      // As with `handleLiveDisconnect`: a 401 mid-operation means the
      // session has expired - then back to login instead of continuing to
      // retry every second against an invalid session.
      await this.loadAuthInfo();
      if (!this.authenticated) {
        this.authError = t("web.auth.session_expired");
        return;
      }
      this.scheduleDiagnosticsReconnect();
    },

    scheduleDiagnosticsReconnect() {
      if (this.diagnosticsReconnectTimer !== null) {
        return;
      }
      this.diagnosticsReconnectTimer = window.setTimeout(() => {
        this.diagnosticsReconnectTimer = null;
        this.connectDiagnosticsLive();
      }, this.diagnosticsReconnectDelayMs);
      this.diagnosticsReconnectDelayMs = Math.min(
        this.diagnosticsReconnectDelayMs * 2,
        RECONNECT_DELAY_MAX_MS,
      );
    },

    /**
     * Dispatches a diagnostics channel message to its stream, based on
     * `message.kind` (see api/diagnostics_live.py for the three shapes).
     * While `diagnosticsPaused` is set, NOTHING is appended - that is the
     * pause itself, not a display filter (see its comment in the state
     * above).
     */
    handleDiagnosticsMessage(message) {
      if (this.diagnosticsPaused) {
        return;
      }
      if (message.kind === "datagram") {
        this.appendDiagnosticsEntry(this.datagrams, message);
        this.pinLogListToTop("datagramsList");
      } else if (message.kind === "command") {
        this.appendDiagnosticsEntry(this.commandLog, message);
      } else if (message.kind === "log") {
        this.appendDiagnosticsEntry(this.diagnosticsLogs, message);
        this.pinLogListToTop("diagnosticsLogsList");
      }
      // An unknown `kind` is silently ignored rather than thrown: a
      // future message type not yet known here should not tear down the
      // connection.
    },

    /** Appends, capped at DIAGNOSTICS_LINE_LIMIT per stream (see there). */
    appendDiagnosticsEntry(list, entry) {
      list.push(entry);
      if (list.length > DIAGNOSTICS_LINE_LIMIT) {
        list.shift();
      }
    },

    /**
     * Keeps a `.log-list` pinned to the top after a new line has arrived -
     * but only if you were already there. The template displays the
     * streams in reverse (newest first, see
     * `visibleDatagrams`/`visibleDiagnosticsLogs`), so "following along"
     * here means: keeping the scroll position at the top (0), not jumping
     * to the end. Anyone who has scrolled down to read older lines is not
     * yanked back by newly arriving lines - the tolerance value (4px)
     * catches rounding remainders from scrolling, since no trackpad/mouse
     * wheel stops at exactly 0.
     */
    pinLogListToTop(ref) {
      const el = this.$refs[ref];
      if (!el) {
        return;
      }
      const wasAtTop = el.scrollTop <= 4;
      this.$nextTick(() => {
        if (wasAtTop) {
          el.scrollTop = 0;
        }
      });
    },

    /**
     * The UDP capture lines as `hideNoise` currently wants them shown.
     * The filter reads `entry.forced` (`api/diagnostics_live.py`, filled
     * from `DatagramLogEntry.forced` - see there for the rationale behind
     * why this information comes from the server instead of a time
     * heuristic reconstructed in the browser): `True` stands exclusively
     * for the heartbeat and a full resend, never for a real value change -
     * not even when two real changes (e.g. a pulse and its counter, see
     * `Runtime.on_event`) arrive back to back within microseconds.
     */
    visibleDatagrams() {
      const entries = this.hideNoise
        ? this.datagrams.filter((entry) => !entry.forced)
        : this.datagrams;
      // Displayed in reverse (newest first) - the ring buffer itself stays
      // oldest-first, so `appendDiagnosticsEntry` with `shift()` continues
      // to cap at the oldest entry (see there).
      return [...entries].reverse();
    },

    /**
     * The log lines from `logLevel` up (see LOG_LEVEL_ORDER above). A
     * line with a level unknown here stays visible instead of silently
     * disappearing.
     */
    visibleDiagnosticsLogs() {
      const threshold = LOG_LEVEL_ORDER.indexOf(this.logLevel);
      const entries = this.diagnosticsLogs.filter((entry) => {
        const rank = LOG_LEVEL_ORDER.indexOf(entry.level);
        return rank === -1 || rank >= threshold;
      });
      // See the comment in visibleDatagrams(): display reversed, ring
      // buffer not.
      return entries.reverse();
    },

    /**
     * Clears all three streams held on this page - only the display in
     * this tab, no effect on the server's rings (the next snapshot on
     * reconnecting shows them again unchanged). Also called by
     * `connectDiagnosticsLive()` itself, BEFORE every (re)build of the
     * connection - see the comment there.
     */
    clearDiagnosticsBuffers() {
      this.datagrams = [];
      this.commandLog = [];
      this.diagnosticsLogs = [];
    },

    // ---------------------------------------------------------------------
    // Live connection (Spec 8.3)
    // ---------------------------------------------------------------------

    connectLive() {
      // Clean up before every rebuild - previously the body of
      // `restartLive()`, which served token cleanup (a token change
      // invalidated an existing connection) and was deleted along with
      // the token input as supposedly dead code when that was removed. It
      // was not: the same cleanup is now also missing for the case where
      // `connectLive()` is called again while an OLD connection is still
      // alive - for instance when, after an expired session
      // (`noteAuthError`, login screen), the old connection's
      // `reconnectTimer` is still armed AND `submitPassword` ->
      // `startApp()` itself triggers a call after re-login: without this
      // cleanup, the old, orphaned socket would keep running
      // authenticated (its `close` handler would already have lost the
      // `this.socket === socket` comparison against the NEW socket and
      // therefore never triggers a reconnection), while the timer opens a
      // third connection shortly afterward.
      if (this.reconnectTimer !== null) {
        window.clearTimeout(this.reconnectTimer);
        this.reconnectTimer = null;
      }
      const previous = this.socket;
      this.socket = null;
      this.socketConnected = false;
      if (previous) {
        previous.close();
      }

      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      const url = `${protocol}//${window.location.host}/api/live`;
      // No more subprotocol: the session cookie travels along on its own
      // during the handshake, because this WebSocket has the same origin
      // as the page. The previously necessary detour `new WebSocket(url,
      // ["bearer", token])` - and with it the special case where a token
      // containing spaces made the constructor throw synchronously - is
      // dropped with nothing taking its place. The server still reads the
      // subprotocol, but for scripts (see
      // `loxone.server.build_api_guard`).
      const socket = new WebSocket(url);

      socket.addEventListener("open", () => {
        this.socketConnected = true;
        // Clears a stale "bridge unreachable" banner (review finding,
        // 2026-09-09): `handleLiveDisconnect()` (below) calls
        // `loadAuthInfo()` on every drop, and DURING an outage that call's
        // own request can fail outright (the HTTP server is gone along
        // with this socket) - its generic catch then lands the
        // `web.errors.bridge_unreachable` text in `authError`, and nothing
        // ever ran `loadAuthInfo()` again to clear it once the socket
        // alone reconnected: this `open` handler used to only flip
        // `socketConnected` and backfill devices. The header then read
        // "Live connection active" right next to a red banner about an
        // outage that had already ended - not specific to updates, any
        // transient network drop left the same stale text.
        //
        // Guarded by `this.authenticated` rather than cleared
        // unconditionally, to keep this from ever touching a GENUINE auth
        // failure: `noteAuthError` and `handleLiveDisconnect`'s own `if
        // (!this.authenticated)` branch a few lines below both flip
        // `authenticated` to `false` before putting such a message into
        // `authError`, and a socket rejected for an invalid session never
        // reaches `open` in the first place (`build_api_guard`,
        // loxone/server.py, closes the handshake before that event can
        // fire) - so a message still sitting in `authError` at this exact
        // point can only be the stale connection text above.
        if (this.authenticated) {
          this.authError = null;
        }
        // On a RE-connection, fetch the server's `last_heard` again
        // (final review, A2). Everything sent while the socket was down
        // never reached this tab, and `deviceHeardAt` is the tab's own
        // bookkeeping: it cannot know what it missed, and nothing else
        // backfills it - `loadDevices()` otherwise runs only from
        // `startApp()` and after commissioning or removal. A window
        // contact that reports once during a two-hour outage would
        // otherwise leave its tile reading "Last heard 3h ago"
        // indefinitely, with the staleness banner already cleared: the
        // same wasted investigation this line was built to prevent,
        // pointing the other way. The server's `Runtime._last_heard`
        // does know, and `lastHeardAt` takes the LATER of the two
        // sources, so the refresh can only ever move a label forward.
        //
        // Read BEFORE `socketEverConnected` is set below, so this is the
        // previous connection state, not this one: on the very first
        // connection `startApp()` has just loaded the list and a second
        // identical request per page load would buy nothing.
        if (this.socketEverConnected) {
          this.loadDevices();
        }
        this.socketEverConnected = true;
        this.reconnectDelayMs = RECONNECT_DELAY_INITIAL_MS;
      });

      socket.addEventListener("message", (event) => {
        const message = JSON.parse(event.data);
        this.liveValues[message.key] = message.value;
        const now = Date.now();
        this.liveSeenAt[message.key] = now;
        // Which device a message belongs to is in its key: signal keys
        // start with `d<device id>_`. The heartbeat (`bridge_alive`)
        // matches no device on purpose - see the comment below for why it
        // is the honest sign of life, and exactly for that reason it must
        // not count here: it arrives every 30 seconds regardless, and
        // crediting it would make every tile claim it had just been heard
        // from.
        //
        // `d<id>_online` is the second key that must not count, and the
        // key pattern does NOT exclude it on its own (final review, A1;
        // the design reasoned only about the heartbeat and assumed it
        // did). Reachability is matter-server's bookkeeping ABOUT a node,
        // not the node saying anything - which is why `Runtime` calls
        // `_mark_heard` from `on_attribute`, `on_node_snapshot` and
        // `on_event`, but deliberately not from `set_online`
        // (loxone/runtime.py). `set_online` still notifies its observers,
        // so `d<id>_online` reaches this handler verbatim; without the
        // exclusion below the tile would render the Offline pill and
        // "Last heard just now" on the same card, at the exact moment
        // this line exists to serve. The client's definition of "heard"
        // is hereby the same as `Runtime._mark_heard`'s.
        const owner = /^d(\d+)_/.exec(message.key);
        if (owner && message.key !== `d${owner[1]}_online`) {
          this.deviceHeardAt[Number(owner[1])] = now;
        }
        // The heartbeat does not belong to any device (Spec 6.5) and is
        // exactly for that reason the honest sign of life: it arrives
        // even when nothing changes on any device.
        if (message.key === HEARTBEAT_KEY) {
          this.lastHeartbeatAt = now;
        }
      });

      // Both a clean close and a connection error should trigger the same
      // reconnection - a UI that keeps showing frozen values as current
      // is worse than one that admits it has lost the connection. The
      // `this.socket === socket` check prevents a deliberately discarded
      // connection from still triggering a reconnection: the cleanup part
      // above in `connectLive()` only closes an old connection AFTER it
      // has already cleared `this.socket`, and this call itself sets
      // `this.socket` to the new socket right afterward - the old
      // connection's `close` event fires asynchronously and therefore
      // hits a `this.socket` here that is no longer itself. Only one path
      // can ever let `connectLive()` run into a still-living connection
      // at all: `startApp()` after a re-login. The `reconnectTimer`
      // below, by contrast, only ever arises from this connection's own
      // `close` event and therefore always hits an already-closed socket.
      socket.addEventListener("close", () => {
        if (this.socket === socket) {
          this.handleLiveDisconnect();
        }
      });
      socket.addEventListener("error", () => socket.close());

      this.socket = socket;
    },

    /**
     * Reacts to the live connection dropping: an ordinary network outage
     * should keep automatically reconnecting, while a session that has
     * become invalid should instead lead back to login - without this
     * distinction, an open tab would hang forever on "Connection lost",
     * because the browser cannot tell a WebSocket connection rejected
     * with 401 apart from a genuine network error (both only fire
     * `close`), and `scheduleReconnect` would therefore keep trying
     * indefinitely, every second.
     *
     * Triggered, among other things, by `loxmatter set-password` (logs
     * out every session) or a logout in another tab - no call on this
     * page would otherwise ever learn about it, as long as no one asks:
     * there is no periodic HTTP call that catches the session state, the
     * views only load on click.
     *
     * `/auth-info` sits outside the guard (see api/auth.py) and exists
     * exactly for this question. If the session stays valid (or the
     * request itself already fails, e.g. because the network is
     * completely gone - `loadAuthInfo` does not touch `authenticated` in
     * that case), things continue as before with `scheduleReconnect`; the
     * exponential backoff thus remains unchanged in effect.
     */
    async handleLiveDisconnect() {
      // FIRST, before the `await` below: the socket is already dead at
      // this point (this call comes from its `close` event); without this
      // line, the header would keep reporting "Live connection active"
      // until `loadAuthInfo()` finished and would omit the "Values may be
      // stale" banner - exactly the state the comment in `connectLive()`
      // above describes as "worse than admitting the connection is gone"
      // (review finding, 2026-09-03).
      this.socketConnected = false;
      await this.loadAuthInfo();
      if (!this.authenticated) {
        this.authError = t("web.auth.session_expired");
        return;
      }
      this.scheduleReconnect();
    },

    scheduleReconnect() {
      this.socketConnected = false;
      if (!this.socketEverConnected) {
        // Never successfully connected before - this attempt was one of
        // the FIRST ones, not the loss of an existing connection
        // (Review-Fix Minor #4). Capped from above, so the number does
        // not grow without bound while the bridge stays permanently
        // unreachable - `connectionStatusText()` below only ever asks
        // whether the threshold has been reached anyway, not for the
        // exact value.
        this.initialConnectFailures = Math.min(
          this.initialConnectFailures + 1,
          INITIAL_CONNECT_FAILURES_BEFORE_GIVING_UP_ON_SILENCE,
        );
      }
      if (this.reconnectTimer !== null) {
        return;
      }
      this.reconnectTimer = window.setTimeout(() => {
        this.reconnectTimer = null;
        this.connectLive();
      }, this.reconnectDelayMs);
      this.reconnectDelayMs = Math.min(this.reconnectDelayMs * 2, RECONNECT_DELAY_MAX_MS);
    },

    // Header text of the live connection (Spec 8.3) - as a dedicated
    // function instead of a nested condition directly in `index.html`,
    // since Review-Fix Minor #4 added a third case (see
    // `INITIAL_CONNECT_FAILURES_BEFORE_GIVING_UP_ON_SILENCE` above).
    connectionStatusText() {
      if (this.socketConnected) {
        return t("web.connection.live");
      }
      if (this.socketEverConnected) {
        return t("web.connection.lost_reconnecting");
      }
      if (this.initialConnectFailures >= INITIAL_CONNECT_FAILURES_BEFORE_GIVING_UP_ON_SILENCE) {
        return t("web.connection.never_connected");
      }
      return t("web.connection.connecting");
    },

    // ---------------------------------------------------------------------
    // Formatting
    // ---------------------------------------------------------------------

    formatTimestamp(isoTimestamp) {
      if (!isoTimestamp) {
        return t("web.format.never");
      }
      try {
        return new Date(isoTimestamp).toLocaleString(this.language === "de" ? "de-DE" : "en-US");
      } catch {
        return isoTimestamp;
      }
    },

    // Numbers get at most two decimal places (2026-09-07). Live values
    // arise from Matter attributes that arrive as integer hundredths and
    // inherit the usual floating-point imprecision when converted - 2253
    // becomes 22.529999999999998, and that used to appear as-is in the
    // tile: it blows up the column and asserts a precision the device
    // never delivered.
    //
    // `toFixed(2)` rounds to two places and returns a string,
    // `Number(...)` throws away the resulting trailing zeros again -
    // otherwise a clean value would read "21.00" instead of "21".
    // The detour via `Number.isFinite` keeps out `NaN` and `Infinity`,
    // which `toFixed` does not throw on but also does not round
    // sensibly. Non-numbers (strings from the live connection) are left
    // untouched: parsing a string here would mean guessing what in it
    // is a number.
    formatValue(value) {
      if (value === null || value === undefined) {
        return "-";
      }
      if (typeof value === "boolean") {
        return value ? t("web.format.true") : t("web.format.false");
      }
      if (typeof value === "number" && Number.isFinite(value)) {
        return String(Number(value.toFixed(2)));
      }
      return String(value);
    },
  };
}
