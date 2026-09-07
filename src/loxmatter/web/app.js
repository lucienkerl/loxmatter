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
// The fragment never reaches the server at all.
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
    controlsByDevice: {},
    commandValueDrafts: {},
    commandBusyKey: null,
    // Toasts as an overlay instead of in the text flow (2026-09-03): a
    // line inserted into the flow shifts everything below it, and anyone
    // trying to click a second command right then misses.
    toasts: [],
    // When a key last came in over the live connection. Makes the
    // difference visible between "nothing is changing" and "nothing is
    // arriving" - for a plug socket with no load, both look the same.
    liveSeenAt: {},
    lastHeartbeatAt: null,
    // Ticks every second so the "... ago" labels keep up. Without this
    // field, Alpine would see no reason to redraw them.
    nowTick: Date.now(),
    labelDrafts: {},
    deviceActionError: null,

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
    // The signal modal holds the device id, NOT the device object:
    // `loadDevices` replaces `devices` entirely, and a retained object
    // would afterwards be a corpse carrying a stale name and room.
    // `signalsModalDeviceObject()` resolves the id against the current
    // list each time. This field is reset at EXACTLY ONE place, the
    // `@close` of the `<dialog>` in index.html - see the comment there.
    signalsModalDevice: null,
    // Pure presentation bookkeeping for the modal's backdrop click, NOT
    // modal state like `signalsModalDevice` above - see the
    // `@mousedown.self`/`@click.self` comment on the `<dialog>` in
    // index.html.
    signalsModalBackdropMousedown: false,

    // --- Settings --------------------------------------------------------
    // `bridgeSettings` is the state most recently loaded from the server
    // (also read by Task 7 and Task 9); `settingsDraft` are the three
    // input fields on this tab, only adopted after "Save".
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
      this.controlsByDevice = {};
      this.signalsByDevice = {};
      await this.loadDevices();
      // Every card shows values and controls immediately, with no click
      // needed (device dashboard design, section 3) - that is why
      // startApp() loads both for EVERY device, not just for one after an
      // expand (which no longer exists since this design).
      await Promise.all([
        ...this.devices.map((device) => this.loadControls(device.id)),
        ...this.devices.map((device) => this.loadSignals(device.id)),
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
        this.controlsByDevice[deviceId] = await this.request(
          "GET",
          `/api/devices/${deviceId}/controls`,
        );
      } catch (error) {
        this.deviceActionError = t("web.devices.controls_load_error", { message: error.message });
      }
    },

    controlsFor(deviceId) {
      return this.controlsByDevice[deviceId] || null;
    },

    /**
     * Whether this device's controls could be loaded at all. Without this
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
    controlsLoaded(deviceId) {
      // Direct access, no `hasOwnProperty` - see `isOnline`.
      return this.controlsByDevice[deviceId] !== undefined;
    },

    // The following three helpers exist solely so `index.html` does not
    // need an optional chaining operator (`?.`) in an Alpine expression to
    // handle an entry that has not been loaded yet - an ordinary function
    // is more readable here than an expression with a built-in existence
    // check in the middle of the markup.
    commandsFor(deviceId) {
      const controls = this.controlsByDevice[deviceId];
      return controls ? controls.commands : [];
    },

    hiddenRawCommandsFor(deviceId) {
      const controls = this.controlsByDevice[deviceId];
      return controls ? controls.hidden_raw_commands : 0;
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

    firstSignalsFor(deviceId) {
      return this.functionalSignalsFor(deviceId).slice(0, this.FUNCTIONAL_PREVIEW_LIMIT);
    },

    remainingSignalCount(deviceId) {
      return Math.max(
        0,
        this.functionalSignalsFor(deviceId).length - this.FUNCTIONAL_PREVIEW_LIMIT,
      );
    },

    // --- Category, rooms, sorting --------------------------------------------

    // The translated name of the category. The API only returns the
    // identifier ("socket"), so the search below can compare against the
    // text the operator actually sees - in German "Steckdose", in English
    // "socket".
    categoryLabel(device) {
      return t("web.devices.category." + (device.category || "other"));
    },

    // A device's room in the encoding of `roomFilter`: "" instead of
    // null/undefined. One place, so the conversion does not live
    // separately in four helpers, one of which might eventually do it
    // differently.
    roomKeyOf(device) {
      return device.room || "";
    },

    // All rooms with their device count, "No room" right at the end.
    // `key` is the value `roomFilter` takes on ("" for no room), `label`
    // the displayed text.
    roomChips() {
      const counts = new Map();
      for (const device of this.devices) {
        const key = this.roomKeyOf(device);
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

    // The bar does not show at all as long as not a single device carries
    // a room: with three devices and no room it would be a line of noise
    // above a list that fits in one glance anyway.
    hasAnyRoom() {
      return this.devices.some((device) => Boolean(device.room));
    },

    // Does the search term match this device? Compared against name,
    // translated category name, and room name.
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

    // How many devices the search term matches OUTSIDE the selected
    // room. Only relevant when nothing is left within the room itself -
    // otherwise the note would be a distraction.
    hitsOutsideRoom() {
      if (this.roomFilter === null || !this.deviceSearch.trim()) {
        return 0;
      }
      return this.devices.filter(
        (device) => this.roomKeyOf(device) !== this.roomFilter && this.matchesSearch(device),
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

    // --- Primary signal (tile header) ----------------------------------------

    // The first functional signal in the order `firstSignalsFor` returns
    // anyway - i.e. that of the profile table. Plug socket -> state,
    // climate sensor -> temperature, blind -> position. No dedicated data
    // storage, no configuration: a device with no functional signals
    // simply has no primary signal, and the header stays single-line.
    leadSignalFor(deviceId) {
      return this.firstSignalsFor(deviceId)[0] || null;
    },

    // The rest of the short list. `FUNCTIONAL_PREVIEW_LIMIT` counts the
    // primary signal IN (design 6.2), so there is no second cutoff here -
    // `firstSignalsFor` has already done it.
    restSignalsFor(deviceId) {
      return this.firstSignalsFor(deviceId).slice(1);
    },

    // --- Changing a device's room ----------------------------------------------

    // Sends ONLY the room. A `label` sent along with it would run
    // `rename_device` and set `updated_at` - the device would afterwards
    // show as "changed since export" even though the room does not end up
    // in any template (design 3.3).
    //
    // `value` is already in the same encoding as `roomFilter`: "" means
    // "no room", and that is exactly what the API also expects for
    // "remove room". No conversion at this point.
    async saveRoom(device, value) {
      this.deviceActionError = null;
      try {
        const updated = await this.request("PATCH", `/api/devices/${device.id}`, {
          room: value,
        });
        Object.assign(device, updated);
      } catch (error) {
        this.deviceActionError = t("web.devices.room_save_error", { message: error.message });
      } finally {
        // Even on failure: if a failed write leaves a room empty, the
        // filter must not stay stuck on a room that no longer exists.
        this.reconcileRoomFilter();
      }
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

    beginNewRoom(device) {
      this.newRoomFor = device.id;
      this.newRoomDraft = "";
    },

    async commitNewRoom(device) {
      const name = this.newRoomDraft.trim();
      this.newRoomFor = null;
      this.newRoomDraft = "";
      if (name) {
        await this.saveRoom(device, name);
      }
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

    // Both blocks of the signals view as one list (Review-Fix 6, Phase 6
    // follow-up): the signal-row template used to sit in index.html
    // twice, byte-identical apart from `functionalSignalsFor` versus
    // `expertSignalsFor` - 51 duplicated lines that had to be touched
    // twice on every change, with nothing noticing if they drifted apart.
    // In the template, `collapsible` now only controls the `<details>`'s
    // starting state (functional open, expert closed, see `x-init` in
    // index.html) - the rest (row markup, empty-state note) is identical
    // for both groups. The first sentence above has applied twice since
    // the modal rework: there, both groups even share the same
    // `<details>` markup, not just the same row template.
    signalGroupsFor(deviceId) {
      return [
        { key: "functional", title: t("web.signals.group_functional"), collapsible: false, signals: this.functionalSignalsFor(deviceId) },
        { key: "expert", title: t("web.signals.group_expert"), collapsible: true, signals: this.expertSignalsFor(deviceId) },
      ];
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

    async saveLabel(device) {
      const label = (this.labelDrafts[device.id] ?? device.label).trim();
      if (!label || label === device.label) {
        return;
      }
      this.deviceActionError = null;
      try {
        const updated = await this.request("PATCH", `/api/devices/${device.id}`, { label });
        Object.assign(device, updated);
      } catch (error) {
        this.deviceActionError = t("web.devices.label_save_error", { message: error.message });
      }
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
        delete this.controlsByDevice[device.id];
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
      } catch (error) {
        this.deviceActionError = t("web.devices.remove_error", { message: error.message });
      }
    },

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
      // `null` is a VALID argument here, not a programming error:
      // `leadSignalFor` returns it for every device whose signals are not
      // loaded yet - and that is every device, for at least one render
      // pass, between `GET /api/devices` and
      // `GET /api/devices/<id>/signals` (2026-09-06).
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

    async commissionDevice() {
      this.commissionMessage = null;
      if (!this.commissionCode.trim()) {
        this.commissionMessage = t("web.devices.commission_code_required");
        this.commissionMessageIsError = true;
        return;
      }
      this.commissionBusy = true;
      this.commissionStep = 0;
      this.commissionFailed = false;
      this.commissionRunCode = this.commissionCode.trim();
      try {
        const body = { code: this.commissionCode.trim() };
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
     * clearing the state directly: `close()` fires the `close` event, and
     * its handler in index.html is the one place that resets
     * `signalsModalDevice`. Anyone additionally writing
     * `this.signalsModalDevice = null` here would once again create two
     * sources of truth for the same state.
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
        this.systemChecks = await this.request("GET", "/api/diagnostics/system");
      } catch (error) {
        this.systemError = t("web.system.load_error", { message: error.message });
      } finally {
        this.diagnosticsBusy = false;
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
     * Groups the flat plan by device and, within that, again by input/
     * output - exactly the nesting in which the signals later end up as
     * virtual inputs/outputs in Loxone Config (one container per device,
     * `Inputs` and `Outputs` as separate groups underneath it), instead of
     * one single long, unsorted list.
     *
     * Orphaned entries (`device_id === -1`, see `PlanEntry` in `diff.py` -
     * no longer belong to any currently known device) get their own group
     * with no real device name and are deliberately placed at the end,
     * regardless of their position in the flat plan.
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
      const byDeviceId = new Map();
      for (const entry of entries || []) {
        let group = byDeviceId.get(entry.device_id);
        if (!group) {
          group = {
            deviceId: entry.device_id,
            deviceLabel:
              entry.device_id === -1
                ? t("web.export.projectsync_unassigned_device_label")
                : entry.device_label || "—",
            inputs: [],
            outputs: [],
            counts: { new: 0, updated: 0, unchanged: 0, orphaned: 0, conflict: 0 },
          };
          byDeviceId.set(entry.device_id, group);
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
        this.socketEverConnected = true;
        this.reconnectDelayMs = RECONNECT_DELAY_INITIAL_MS;
      });

      socket.addEventListener("message", (event) => {
        const message = JSON.parse(event.data);
        this.liveValues[message.key] = message.value;
        const now = Date.now();
        this.liveSeenAt[message.key] = now;
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

    formatValue(value) {
      if (value === null || value === undefined) {
        return "-";
      }
      if (typeof value === "boolean") {
        return value ? t("web.format.true") : t("web.format.false");
      }
      return String(value);
    },
  };
}
