/*
 * loxmatter - bindet Matter-Geraete an einen Loxone Miniserver an.
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

// Zustand und Verhalten der loxmatter-Oberflaeche (Task 7, Phase 5).
//
// Bezeichner sind Englisch, wie im restlichen Code dieses Projekts - nur
// Text, der tatsaechlich auf dem Bildschirm oder im Fehlerfall vor einer
// Person landet, ist Deutsch (siehe Aufgabenstellung: "Deutsch in der
// Oberflaeche, in Prosa und Fehlermeldungen, Englisch in Bezeichnern -
// auch in JavaScript").
//
// Keine Klassen, kein Modul-System, kein Bundler: eine einzige Funktion
// `app()`, die Alpine.js per `x-data="app()"` in `index.html` aufruft und
// deren zurueckgegebenes Objekt den gesamten Zustand traegt. Das passt zum
// Rest dieser Datei - eine flache, leicht lesbare Struktur statt einer
// Klassenhierarchie fuer eine einzige Seite.

// Zeitspanne zwischen zwei Wiederverbindungsversuchen des Live-Websockets,
// nach einem Abbruch verdoppelt bis zu diesem Maximum (Spec 8.3: eine
// verlorene Verbindung muss sich von selbst erholen, ohne die Leitung mit
// Versuchen im Sekundentakt zu fluten).
const RECONNECT_DELAY_INITIAL_MS = 1000;
const RECONNECT_DELAY_MAX_MS = 15000;

// Nach wie vielen erfolglosen Versuchen der ALLERERSTEN Verbindung (vor der
// ersten je erfolgreichen) die Kopfzeile von der neutralen "Verbinde…"-
// Formulierung auf einen klareren Text wechselt (Review-Fix Minor #4,
// 2026-09-02). Ohne das bliebe die Kopfzeile bei einer Bruecke, die von
// Anfang an nicht erreichbar ist, unbegrenzt bei "Verbinde…" stehen, waehrend
// im Hintergrund still weiterversucht wird - kein Datenrisiko (es gibt ja
// noch keine Live-Werte, die faelschlich aktuell wirken koennten), aber ein
// schwaecheres Diagnosesignal als der Fall der VERLORENEN Verbindung, der
// bereits einen roten Banner und "Verbindung verloren" bekommt.
const INITIAL_CONNECT_FAILURES_BEFORE_GIVING_UP_ON_SILENCE = 3;

// Der Heartbeat-Schluessel der Bruecke (siehe loxone/runtime.py,
// HEARTBEAT_KEY). Er gehoert zu keinem Geraet und kommt auch dann, wenn
// sich an keinem etwas aendert - damit ist er das einzige verlaessliche
// Lebenszeichen, das diese Oberflaeche hat.
const HEARTBEAT_KEY = "bridge_alive";

// Wie lange ein frisch eingetroffener Wert hervorgehoben bleibt. Etwas mehr
// als zwei Sekunden: `nowTick` laeuft im Sekundentakt, also ist die Grenze
// ohnehin auf eine Sekunde genau, und kuerzer als zwei Ticks liefe man
// Gefahr, die Hervorhebung zu verpassen, wenn man gerade woanders hinsieht.
const VALUE_FRESH_MS = 2500;

// --- Live-Diagnose (Aufgabe 6, Spec 10.5) -----------------------------------
//
// Obergrenze der gehaltenen Zeilen je Strom (Logs, UDP-Mitschnitt,
// Kommando-Log). Ohne sie wuerde jeder der drei Ringe waehrend einer langen
// Sitzung unbegrenzt weiterwachsen - anders als beim Server (dessen drei
// Ringe von vornherein feste Groessen haben, siehe DATAGRAM_LOG_SIZE,
// COMMAND_LOG_SIZE, LOG_BUFFER_SIZE) haelt der Browser-Tab sonst jede
// Zeile seit dem Oeffnen der Ansicht im Speicher. Derselbe Wert wie die
// Momentaufnahme-Begrenzung je Strom auf der Serverseite waere zu knapp
// (SNAPSHOT_LIMIT = 50 gilt nur fuer den EINMALIGEN Schub beim Verbinden) -
// 500 entspricht stattdessen der vollen Ringgroesse je Strom und reicht
// damit fuer eine laengere Sitzung, ohne den Tab unbegrenzt wachsen zu
// lassen.
const DIAGNOSTICS_LINE_LIMIT = 500;

// Woran ein Datagramm als "Rauschen" erkannt wird, das `hideNoise`
// ausblendet: an `message.forced` (`api/diagnostics_live.py`, gefuellt aus
// `DatagramLogEntry.forced`, siehe dort) - NICHT mehr an der Ankunftsrate im
// Browser (Nachbesserung Task 6, 2026-09-03). Die fruehere Heuristik hier
// nahm an, "kein Geraet dieses Projekts aendert regelmaessig mehrere Signale
// in derselben Millisekunde" - das widerlegt `Runtime.on_event`
// (`loxone/runtime.py`) selbst: ein Impuls und sein Zaehler gehen dort ohne
// jede Wartezeit dazwischen hintereinander raus, wenige Mikrosekunden
// auseinander, und waren damit IMMER als Schwall markiert. Der Server weiss
// dagegen bereits verlaesslich, warum ein Datagramm ging - `force=True`
// steht fuer GENAU zwei Aufrufer, `Runtime.resend_all()` (Full-Resend) und
// den Heartbeat, niemals fuer eine echte Wertaenderung. Diese Auskunft zu
// uebernehmen statt sie im Browser aus dem Zeitabstand zu erraten, ist der
// eigentliche Fix - eine Ankunftsrate kann zwei echte, dicht aufeinander
// folgende Aenderungen strukturell nicht von einem Schwall unterscheiden.

// Reihenfolge der Python-Logstufen, wie `logging` sie kennt - fuer den
// Vergleich mit `logLevel` unten ("ab Stufe X zeigen"). Ein Eintrag mit
// einer hier unbekannten Stufe wird NICHT herausgefiltert (siehe
// `visibleDiagnosticsLogs`): eine unerwartete Stufe lieber zeigen als eine
// vielleicht wichtige Zeile stillschweigend verschlucken.
const LOG_LEVEL_ORDER = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"];

// Laufende Nummer fuer Kurzmeldungen. Modulweit statt im Zustand, weil
// sie nur die Meldungen auseinanderhalten muss und niemanden sonst
// interessiert.
let toastCounter = 0;

/**
 * Fehler eines Aufrufs ohne gueltige Sitzung - eigene Klasse, damit die
 * Oberflaeche diesen Fall von jedem anderen Fehlschlag unterscheiden kann,
 * ohne auf einen Meldungstext zu pruefen.
 */
class UnauthorizedError extends Error {
  constructor() {
    super(t("web.auth.session_expired"));
    this.name = "UnauthorizedError";
  }
}

/**
 * Liest den Fehlertext aus einer FastAPI-Fehlerantwort (`{"detail": "..."}"`)
 * - oder liefert einen generischen Text, falls die Antwort kein JSON war
 * (z. B. ein Netzwerkfehler ganz ohne Antwort vom Server).
 */
async function readErrorDetail(response) {
  try {
    const body = await response.json();
    if (body && typeof body.detail === "string") {
      return body.detail;
    }
  } catch {
    // Antwort war kein JSON - der generische Text unten reicht dann.
  }
  return t("web.errors.http_status", { status: response.status });
}

/**
 * Ruft einen JSON-Endpunkt auf und wirft bei einer Fehlerantwort einen
 * `Error`, dessen Nachricht der `detail`-Text des Backends ist - dieselbe
 * Nachricht, die auch ein Server-Log sehen wuerde, nicht nur "HTTP 400".
 * Eine Diagnose-Oberflaeche, die einen Fehlschlag verschluckt oder
 * verwaessert, waere genau das Gegenteil ihres Zwecks (Spec 8.1).
 */
async function requestJson(method, path, body) {
  let response;
  try {
    response = await fetch(path, {
      method,
      // Das Sitzungs-Cookie statt eines Tokens im Header: `same-origin`
      // schickt es an genau den Ursprung mit, von dem diese Seite geladen
      // wurde, und an keinen anderen. Ein `Authorization`-Header wird hier
      // nicht mehr gesetzt - der Weg ueber das Token gibt es weiterhin,
      // aber fuer Skripte, nicht fuer diesen Browser (siehe api/auth.py).
      credentials: "same-origin",
      headers: body !== undefined ? { "Content-Type": "application/json" } : {},
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    // `fetch()` selbst wirft nur bei einem Fehler auf Netzwerkebene -
    // Verbindung abgelehnt, Bruecken-Prozess unten, Netz nicht erreichbar -
    // nie bei einer Fehlerantwort des Servers (die faengt `readErrorDetail`
    // oben ab, weiter unten in dieser Funktion). Ohne dieses `catch` liefe
    // der rohe Browsertext dafuer ("Failed to fetch" o. ae.) unveraendert
    // bis in die Oberflaeche durch - Englisch und Browser-Jargon, in einem
    // Werkzeug, dessen Zweck es gerade ist, einen Fehlschlag ehrlich UND
    // verstaendlich zu zeigen (Spec 8.1). Review-Fix Important #2,
    // 2026-09-02.
    throw new Error(t("web.errors.bridge_unreachable"));
  }
  if (response.status === 401 && !path.startsWith("/auth/")) {
    // Nicht der rohe Servertext: eine 401 mitten im Betrieb heisst, die
    // Sitzung ist abgelaufen, und die eigene Fehlerklasse fuehrt die
    // Oberflaeche zurueck zum Login-Bildschirm (siehe `noteAuthError`
    // unten). Fuer `/auth/`-Pfade selbst gilt das NICHT: dort ist eine 401
    // schlicht "Falsches Passwort", und genau dieser Servertext soll den
    // Login-Bildschirm erreichen, nicht die hier vorformulierte Meldung
    // ueber eine abgelaufene Sitzung, die es beim allerersten Versuch noch
    // gar nicht gab.
    throw new UnauthorizedError();
  }
  if (!response.ok) {
    const error = new Error(await readErrorDetail(response));
    // `status` haengt hier mit dran, nicht nur der Text: `submitPassword`
    // unten muss eine 409 auf `/auth/setup` von jedem anderen Fehlschlag
    // unterscheiden koennen, und der Meldungstext dafuer ist kein
    // verlaesslicher Anker (der duerfte sich unabhaengig vom Statuscode
    // aendern).
    error.status = response.status;
    throw error;
  }
  if (response.status === 204) {
    return null;
  }
  return response.json();
}

/**
 * Laedt eine Datei von `/api` herunter. Ueber `fetch` und nicht ueber ein
 * `<a href>`, weil eine 401 sonst als roher Fehlertext im Browserfenster
 * landete statt in der Oberflaeche - und weil der Blob-Download so den
 * Dateinamen setzen kann.
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
  // Ohne dieses Freigeben haelt der Browser den kompletten Blob bis zum
  // Verlassen der Seite im Speicher - bei einer Fabric-Sicherung oder einem
  // Export-ZIP ist das kein Kleingeld. Aber NICHT sofort: manche Browser
  // starten den Download eines Objekt-URLs erst nach dem laufenden
  // Aufrufstapel, und ein bereits freigegebener URL laesst den Download
  // dann stillschweigend ausfallen. Das `setTimeout` gibt ihn eine
  // Runde spaeter frei - fuer Chrome unnoetig, fuer Firefox nicht
  // (Review-Fix Minor #3, 2026-09-03).
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
}

// Modul-global, absichtlich NICHT auf dem app()-Objekt (siehe
// Implementierungsplan, Task 8: "t() muss global aufrufbar sein") - jede
// Funktion in dieser Datei erreicht sie, auch requestJson/requestDownload/
// requestUpload, die keinen Zugriff auf `this` des Alpine-Bauteils haben.
// Nicht reaktiv, weil sie es nicht sein muss: ein Sprachwechsel laedt die
// ganze Seite neu.
let translationStrings = {};

/** Uebersetzungshelfer - liefert den zu key gehoerenden Text in der
 * aktuellen Sprache, mit {platzhalter} aus values ersetzt. Fehlt der
 * Schluessel (z. B. eine noch nicht neu geladene Seite nach einem
 * Deployment mit neuen Schluesseln), liefert t() den Schluessel selbst
 * zurueck statt abzustuerzen - sichtbar falsch statt einer kaputten
 * Seite, dieselbe Haltung wie ueberall sonst in diesem Projekt
 * ("ein Klick, der nichts bewirkt, muss als klare Absage ankommen"). */
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
 * Laedt eine Datei per multipart/form-data hoch und erwartet JSON zurueck -
 * eigene Funktion statt `requestJson`, weil ein Datei-Upload kein
 * `JSON.stringify`-Body ist und `Content-Type` dem Browser ueberlassen
 * werden muss (er setzt die Multipart-Boundary selbst, inklusive der
 * Trennzeichenfolge, die `JSON.stringify` gar nicht kennt).
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
 * Dekodiert einen Base64-String zu einem Blob - fuer den Download der
 * gepatchten Projektdatei aus der JSON-Antwort von
 * `/api/export/project-sync` (die Datei kommt eingebettet in der
 * Plan-Antwort, nicht ueber einen eigenen Download-Aufruf, siehe
 * `downloadPatchedProject` unten).
 */
function blobFromBase64(base64, mimeType) {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return new Blob([bytes], { type: mimeType });
}

function app() {
  return {
    // --- Ansicht ---------------------------------------------------------
    view: "devices",

    // --- Zugang -----------------------------------------------------------
    // `authReady` verhindert das Aufblitzen des falschen Bildschirms: bis
    // `/auth-info` geantwortet hat, weiss die Seite nicht, ob sie Einrichtung,
    // Login oder die App zeigen muss, und zeigt deshalb keines davon.
    authReady: false,
    passwordSet: false,
    authenticated: false,
    passwordDraft: "",
    passwordRepeatDraft: "",
    authBusy: false,
    authError: null,

    // --- Uebersetzung -------------------------------------------------------
    // Nach demselben Muster wie authReady: bis GET /api/i18n geantwortet hat,
    // zeigt die Seite nichts - siehe stringsReady in den beiden
    // auth-screen-templates in index.html. Die eigentliche Tabelle liegt
    // NICHT hier, sondern im modul-globalen translationStrings (siehe t()
    // oben) - dieses Feld existiert nur fuer x-if="stringsReady && ...".
    stringsReady: false,
    language: "en",

    // --- Geraete -----------------------------------------------------------
    devices: [],
    devicesError: null,
    controlsByDevice: {},
    commandValueDrafts: {},
    commandBusyKey: null,
    // Kurzmeldungen als Overlay statt im Textfluss (2026-09-03): eine
    // eingeblendete Zeile im Fluss verschiebt alles darunter, und wer
    // gerade einen zweiten Befehl anklicken will, trifft daneben.
    toasts: [],
    // Wann ein Schluessel zuletzt ueber die Live-Verbindung kam. Macht den
    // Unterschied sichtbar zwischen "nichts aendert sich" und "nichts
    // kommt an" - bei einer Steckdose ohne Last sieht beides gleich aus.
    liveSeenAt: {},
    lastHeartbeatAt: null,
    // Tickt jede Sekunde, damit die "vor ..."-Angaben mitlaufen. Ohne
    // dieses Feld saehe Alpine keinen Grund, sie neu zu zeichnen.
    nowTick: Date.now(),
    labelDrafts: {},
    deviceActionError: null,

    // Einlernen (Spec 7.1).
    commissionCode: "",
    commissionThreadDataset: "",
    commissionBusy: false,
    commissionMessage: null,
    commissionMessageIsError: false,

    // --- Signale (geteilt mit der Geraete-Ansicht: dieselbe Liste dient
    // dort als Kurzfassung der funktionalen Signale) -----------------------
    signalsByDevice: {},
    signalsError: null,
    titleDrafts: {},
    rawWriteDrafts: {},
    rawWriteBusyKey: null,
    rawWriteMessages: {},
    // Experte-Block (Aufgabe 8): standardmaessig zugeklappt, ein einziger
    // globaler Schalter statt Zustand je Geraet - die Ansicht "Signale"
    // zeigt ohnehin alle Geraete auf einmal untereinander, ein Zustand pro
    // Karte wuerde hier keinen zusaetzlichen Nutzen bringen, nur zusaetzliche
    // Klicks.
    showExpertSignals: false,

    // --- Einstellungen ---------------------------------------------------
    // `bridgeSettings` ist der zuletzt vom Server geladene Stand (auch von
    // Task 7 und Task 9 gelesen); `settingsDraft` sind die drei Eingabefelder
    // auf diesem Tab, erst nach "Speichern" uebernommen.
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

    // --- Projektdatei-Sync (Aufgabe 12) ------------------------------------
    // `plan` traegt die komplette Antwort von `/api/export/project-sync`
    // unveraendert (Entries UND die beiden fertig gepatchten Dateien als
    // Base64) - `downloadPatchedProject` liest daraus, statt fuer den
    // Haken "Neue Geraete-Container ebenfalls anlegen" einen zweiten Aufruf
    // an die Bruecke zu machen. Solange `plan` `null` ist, gab es noch
    // keine Antwort zu sehen - und genau daran haengt der Download-Knopf
    // in `index.html` (`x-show="projectSync.plan"`): nichts herunterladen,
    // bevor der Plan gesehen wurde.
    // `plan.patched_with_new_devices_base64` ist `null`, wenn die
    // hochgeladene Datei keinen `VirtualInCaption`/`VirtualOutCaption`-
    // Abschnitt hat (Review-Fix Important #4) - `plan.
    // new_devices_unavailable_reason` traegt dann den Grund. Beides bleibt
    // wie der Rest von `plan` unveraendert aus der API-Antwort, keine
    // eigene camelCase-Kopie.
    projectSync: {
      file: null,
      plan: null,
      includeNewDevices: false,
      busy: false,
      error: "",
      // Nur noetig, wenn die hochgeladene Datei mehr als einen Miniserver
      // konfiguriert (`LoxLIVE.IntAddr`) - bei genau einem wird er
      // automatisch verwendet, siehe `api.project_sync`s `miniserver_ip`.
      // Leer bleibt leer: `uploadProjectFile` haengt das Feld nur an, wenn
      // hier etwas drinsteht.
      miniserverIp: "",
    },

    // --- Live-Diagnose (Aufgabe 6, Spec 10.5) -----------------------------
    // Drei Straeme, gefuellt von genau EINEM WebSocket
    // (`/api/diagnostics/live`) statt wie bisher einmalig per GET - siehe
    // `connectDiagnosticsLive`. `datagrams` und `commandLog` hiessen schon
    // vorher so (frueher durch `loadSystem()` einmalig befuellt); die neue
    // dritte Sorte (`kind: "log"`) kommt mit dieser Aufgabe dazu.
    datagrams: [],
    commandLog: [],
    diagnosticsLogs: [],
    diagnosticsSocket: null,
    diagnosticsConnected: false,
    // Haelt nur das ANHAENGEN neuer Zeilen an - nicht die Verbindung selbst
    // (siehe `handleDiagnosticsMessage`). Bewusst KEIN Anzeigefilter wie
    // `hideNoise`/`logLevel` (Entwurf 4 gilt fuer die beiden, nicht fuer
    // diese Pause): waehrend der Pause eintreffende Zeilen werden nicht
    // nachgeholt, wie bei einem angehaltenen `tail -f`.
    diagnosticsPaused: false,
    diagnosticsReconnectDelayMs: RECONNECT_DELAY_INITIAL_MS,
    diagnosticsReconnectTimer: null,
    // Anzeigefilter (Entwurf 4): wirken NUR auf das, was diese beiden
    // `visible...`-Funktionen zurueckgeben - die gehaltenen Zeilen selbst
    // (`datagrams`, `diagnosticsLogs`) bleiben unveraendert. Wer den Filter
    // ausschaltet, sieht die vorhandenen Zeilen deshalb sofort, statt auf
    // neue zu warten.
    hideNoise: true,
    logLevel: "INFO",

    // --- Live-Verbindung (Spec 8.3) --------------------------------------
    liveValues: {},
    socket: null,
    socketConnected: false,
    socketEverConnected: false,
    // Zaehlt erfolglose Versuche der ALLERERSTEN Verbindung - bleibt ab der
    // ersten erfolgreichen Verbindung unbenutzt (Review-Fix Minor #4, siehe
    // `INITIAL_CONNECT_FAILURES_BEFORE_GIVING_UP_ON_SILENCE` oben).
    initialConnectFailures: 0,
    reconnectDelayMs: RECONNECT_DELAY_INITIAL_MS,
    reconnectTimer: null,

    // Ruft Alpine von sich aus genau EINMAL auf, sobald `x-data="app()"`
    // ausgewertet ist. `index.html` traegt deshalb bewusst kein
    // `x-init="init()"` (Review-Fix Fix 2, 2026-09-03) - das rief die
    // Methode ein zweites Mal auf, und mit ihr (nach einer angemeldeten
    // Sitzung) `startApp()`: jeder offene Tab hielt dann zwei
    // Live-Verbindungen, von denen nur die zuletzt geoeffnete in
    // `this.socket` landete. Die andere blieb unsichtbar und lief bis zum
    // Schliessen des Tabs weiter.
    async init() {
      // Der Sekundentakt fuer die Lebenszeichen-Anzeige steht hier und NICHT
      // in `startApp()`: `startApp()` laeuft auch nach einer Neuanmeldung
      // erneut, und ein zweites `setInterval` liesse sich danach durch
      // nichts mehr stoppen - genau die Falle, die dieses Projekt schon
      // zweimal mit doppelten Live-Verbindungen getroffen hat. `init()`
      // ruft Alpine garantiert genau einmal auf. Der Takt kostet nichts,
      // solange niemand angemeldet ist: er schreibt in ein Feld, das nur
      // die Kopfzeile der App liest.
      window.setInterval(() => {
        this.nowTick = Date.now();
      }, 1000);
      await Promise.all([this.loadI18n(), this.loadAuthInfo()]);
      if (this.authenticated) {
        await this.startApp();
      }
    },

    // ---------------------------------------------------------------------
    // Zugang
    // ---------------------------------------------------------------------

    /**
     * Der einzige Weg dieser Oberflaeche zu `/api`. Reicht `requestJson`
     * unveraendert durch und merkt sich unterwegs nur den einen Fall, den
     * jeder Aufrufer sonst einzeln behandeln muesste: eine 401. Dann setzt
     * er den Hinweis oben und klappt das Eingabefeld auf, statt den
     * Anwender mit fuenfzehn verschiedenen Fehlermeldungen zu belegen, die
     * alle dasselbe bedeuten.
     */
    async request(method, path, body) {
      try {
        return await requestJson(method, path, body);
      } catch (error) {
        this.noteAuthError(error);
        throw error;
      }
    },

    /** Wie `request`, aber fuer die beiden Datei-Downloads. */
    async download(path, filename) {
      try {
        await requestDownload(path, filename);
      } catch (error) {
        this.noteAuthError(error);
        throw error;
      }
    },

    /** Wie `request`, aber fuer den Datei-Upload (Projektdatei-Sync). */
    async upload(path, formData) {
      try {
        return await requestUpload(path, formData);
      } catch (error) {
        this.noteAuthError(error);
        throw error;
      }
    },

    /**
     * Eine 401 mitten im Betrieb heisst: die Sitzung ist abgelaufen oder
     * wurde anderswo beendet. Dann zurueck auf den Login-Bildschirm - eine
     * Fehlermeldung, die auf ein Eingabefeld verweist, das es nicht mehr
     * gibt, waere schlimmer als gar keine.
     */
    noteAuthError(error) {
      if (error instanceof UnauthorizedError) {
        this.authenticated = false;
        this.authError = error.message;
      }
    },

    /** Fragt den Zustand des Zugangs ab - der erste Aufruf jeder Seite. */
    async loadAuthInfo() {
      try {
        const info = await requestJson("GET", "/auth-info");
        this.passwordSet = info.password_set;
        this.authenticated = info.authenticated;
        // Loescht einen aelteren Fehlerbanner ("Die Bruecke ist nicht
        // erreichbar" o. ae.) im Erfolgsfall - diese Funktion lief frueher
        // nur einmal je Seitenaufbau, seit `handleLiveDisconnect` laeuft sie
        // aber bei JEDEM Verbindungsabbruch erneut: ohne diese Zeile bliebe
        // ein Banner von einem kurzen Netzausfall stehen, auch nachdem die
        // naechste Anfrage laengst wieder erfolgreich war (Review-Fund,
        // 2026-09-03).
        this.authError = null;
      } catch (error) {
        this.authError = error.message;
      } finally {
        this.authReady = true;
      }
    },

    /** Laedt die aktuelle Sprache und die web.*-Uebersetzungstabelle - der
     * erste Aufruf jeder Seite, wie loadAuthInfo(), aber unabhaengig davon
     * (siehe init(), das beide parallel startet): GET /api/i18n ist
     * ungeschuetzt, die Ersteinrichtungs-/Anmeldeseite braucht diese Texte,
     * bevor sich jemand angemeldet hat. */
    async loadI18n() {
      try {
        const info = await requestJson("GET", "/api/i18n");
        this.language = info.language;
        translationStrings = info.strings;
        document.documentElement.lang = info.language;
      } catch (error) {
        // Einzige bewusste Ausnahme von "keine console.*-Aufrufe in dieser
        // Datei" (siehe Kopfkommentar): es gibt keinen Oberflaechen-Platz fuer
        // "Uebersetzungen konnten nicht geladen werden" wie es ihn fuer
        // authError gibt - ohne dieses Log waere ein Fehlschlag hier komplett
        // unsichtbar. stringsReady wird trotzdem gesetzt (siehe finally): t()
        // faellt fuer jeden noch nicht geladenen Schluessel selbst auf den
        // rohen Schluesseltext zurueck, statt die Seite fuer immer zu blockieren.
        console.error("Uebersetzungen konnten nicht geladen werden:", error);
      } finally {
        this.stringsReady = true;
      }
    },

    /**
     * Alles, was eine angemeldete Sitzung voraussetzt. Getrennt von `init`,
     * weil es nach dem Login ein zweites Mal laufen muss - dann ohne
     * Neuladen der Seite.
     */
    async startApp() {
      // Zwischenspeicher und Fehlermeldungen leeren, BEVOR irgendetwas neu
      // geladen wird. Diese Methode laeuft nicht nur beim ersten Aufbau,
      // sondern auch nach einer Neuanmeldung - und dann steht in diesen
      // Feldern noch der Stand von vor dem Sitzungsende.
      //
      // Die geraeteweisen Zwischenspeicher sind dabei der heikle Teil (Fund
      // aus Phase 6, hier uebernommen): bei einer 401 legt
      // `loadControls`/`loadSignals` gar keinen Eintrag an - der
      // Zwischenspeicher bleibt leer, und ein leerer Eintrag ist von "dieses
      // Geraet hat keine Befehle" nicht zu unterscheiden. Ohne dieses Leeren
      // zeigte ein aufgeklapptes Geraet nach der Neuanmeldung dauerhaft
      // "Keine bekannten Ausgangsbefehle", obwohl es drei gibt, und nur ein
      // Neuladen der Seite half. Genau die Sorte stillschweigend falscher
      // Zustand, die Spec 8.1 ausschliessen will.
      this.backupError = null;
      this.exportError = null;
      this.deviceActionError = null;
      this.signalsError = null;
      this.settingsError = null;
      this.controlsByDevice = {};
      this.signalsByDevice = {};
      await this.loadDevices();
      // Jede Karte zeigt Werte und Bedienelemente sofort, ohne Klick
      // (Geraete-Dashboard-Entwurf Abschnitt 3) - deshalb laedt startApp()
      // beides fuer JEDES Geraet, nicht erst fuer eines nach einem
      // Aufklappen (das es seit diesem Entwurf nicht mehr gibt).
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
     * Der gemeinsame Teil von Einrichtung und Login: absenden, Fehler
     * anzeigen, bei Erfolg die App starten. Das Cookie setzt der Server,
     * diese Seite fasst es nie an (es ist `HttpOnly`).
     *
     * Ruft `requestJson` direkt auf, nicht `this.request`: dessen 401-Fall
     * ist fuer einen Tippfehler im Passwort gedacht, nicht fuer einen
     * fehlgeschlagenen Login selbst - `requestJson` weiss das (siehe dessen
     * Pfadpruefung) und wirft hier den Servertext ("Falsches Passwort.")
     * unveraendert als gewoehnlichen `Error`.
     */
    async submitPassword(path) {
      this.authBusy = true;
      this.authError = null;
      try {
        await requestJson("POST", path, { password: this.passwordDraft });
      } catch (error) {
        this.authError = error.message;
        if (path === "/auth/setup" && error.status === 409) {
          // Diese Bruecke hat laengst ein Passwort - der Bildschirm zeigte
          // die Einrichtung trotzdem, typischerweise weil `/auth-info` beim
          // Laden fehlschlug und `passwordSet` deshalb bei `false` blieb
          // (siehe `loadAuthInfo`). Ohne diesen Wechsel bliebe die Seite auf
          // dem Einrichtungsbildschirm samt Uebernahme-Warnung stehen, und
          // ein Betreiber, der dort mehrfach "Passwort vergeben" klickt,
          // sperrte sich ueber die gemeinsame `LoginThrottle` auch aus dem
          // LOGIN aus - ohne je ein falsches Passwort eingegeben zu haben
          // (Review-Fund, 2026-09-03; der zweite Teil der Behebung ist der
          // 409-Zweig in `api/auth.py`, der seither keinen Fehlversuch mehr
          // dafuer bucht).
          this.passwordSet = true;
        }
        return;
      } finally {
        this.authBusy = false;
        // In jedem Fall: ein Passwort bleibt nicht im Speicher der Seite
        // stehen, auch nicht nach einem Fehlversuch.
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
        // Auch ein fehlgeschlagener Logout soll abmelden: das Neuladen
        // unten verwirft jeden geladenen Stand, und ohne gueltige Sitzung
        // kommt die Seite ohnehin nur bis zum Login-Bildschirm.
      }
      window.location.reload();
    },

    // ---------------------------------------------------------------------
    // Navigation
    // ---------------------------------------------------------------------

    async selectView(view) {
      this.view = view;
      // Genau EINE Diagnose-Verbindung, offen nur waehrend "System"
      // tatsaechlich die aktive Ansicht ist (Falle 3, Aufgabe 6): sie oeffnet
      // beim Wechsel AUF "system" und schliesst bei jedem anderen Wert -
      // dasselbe Muster wie `connectLive()`/dessen Aufraeumen fuer den
      // Wertekanal, nur an die Ansicht statt an den Login gebunden.
      if (view === "system") {
        this.connectDiagnosticsLive();
      } else {
        this.disconnectDiagnosticsLive();
      }
      if (view === "signals") {
        // Der vollstaendige Baum, nicht erst nach einem weiteren Klick pro
        // Geraet - die "Signale laden"-Schaltflaeche in index.html bleibt
        // trotzdem stehen, sie erscheint nur noch als Wiederholung fuer den
        // Fall, dass ein einzelnes Geraet hier scheitert (`signalsError`).
        await Promise.all(
          this.devices
            .filter((device) => !this.signalsByDevice[device.id])
            .map((device) => this.loadSignals(device.id)),
        );
      } else if (view === "export") {
        await this.loadExportStatus();
      } else if (view === "system") {
        await this.loadSystem();
      } else if (view === "settings") {
        await this.loadSettings();
      }
    },

    // ---------------------------------------------------------------------
    // Geraete
    // ---------------------------------------------------------------------

    async loadDevices() {
      this.devicesError = null;
      try {
        this.devices = await this.request("GET", "/api/devices");
      } catch (error) {
        this.devicesError = t("web.devices.list_load_error", { message: error.message });
      }
    },

    // Online-Status bevorzugt aus dem Live-Websocket (dessen Wert kann
    // sich seit dem letzten Laden der Liste geaendert haben, siehe Spec
    // 8.3) - fehlt er (noch keine Nachricht fuer dieses Geraet
    // eingetroffen), gilt der zuletzt geladene Stand aus `GET
    // /api/devices`.
    // KEIN `hasOwnProperty` hier (2026-09-03). Alpines Reaktivitaet
    // erfasst eine Abhaengigkeit nur bei einem echten Property-ZUGRIFF;
    // `Object.prototype.hasOwnProperty.call(obj, key)` laeuft daran vorbei.
    // Folge in der ausgelieferten Fassung: die erste Nachricht fuer einen
    // Schluessel, den `liveValues` noch nicht kannte, veraenderte die
    // Anzeige NICHT - erst ein spaeteres Neuzeichnen aus anderem Grund
    // holte sie nach. Von aussen sah das aus, als kaeme ueber die
    // Live-Verbindung nichts an, obwohl der Wert laengst im Zustand stand.
    // Ein direkter Zugriff mit `=== undefined` wird dagegen erfasst.
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
     * Ob die Bedienelemente dieses Geraets ueberhaupt geladen werden
     * konnten. Ohne diese Unterscheidung rendert die Oberflaeche einen
     * fehlgeschlagenen Abruf als "Keine bekannten Ausgangsbefehle" - eine
     * Aussage ueber das Geraet, wo in Wahrheit eine ueber die Verbindung
     * faellig waere (Spec 8.1: ein Fehlschlag darf nicht als harmloser
     * Zustand erscheinen).
     */
    controlsLoaded(deviceId) {
      // Direkter Zugriff, kein `hasOwnProperty` - siehe `isOnline`.
      return this.controlsByDevice[deviceId] !== undefined;
    },

    // Die drei folgenden Helfer bestehen einzig, damit `index.html` kein
    // optionales Verkettungsoperator (`?.`) in einem Alpine-Ausdruck
    // braucht, um mit einem noch nicht geladenen Eintrag umzugehen - eine
    // gewoehnliche Funktion ist hier lesbarer als ein Ausdruck mit
    // eingebauter Existenzpruefung mitten im Markup.
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

    // Wie `ExportStatusOut.changed_since_export` server-seitig: ohne
    // geladenen Status (z. B. ein gerade erst eingelerntes Geraet, bevor
    // die naechste `loadExportStatus`-Runde durch ist) gilt "geaendert" -
    // dieselbe vorsichtige Annahme wie beim Server (siehe api/export.py,
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

    // Klassen fuer den Farbstreifen der Kachel (style.css, `.device-card`) -
    // eine Funktion statt eines Inline-Ausdrucks in index.html, weil zwei
    // Bedingungen (online UND geaendert) hier zusammenkommen.
    deviceCardClass(device) {
      return {
        "is-offline": !this.isOnline(device),
        "is-changed": this.isOnline(device) && this.changedSinceExport(device.id),
      };
    },

    // Kurzliste fuer die Geraete-Ansicht: nur die funktionalen Signale
    // (`signal.functional`, aus `profiles.relevance.is_functional` -
    // Aufgabe 8), und davon nur die ersten paar - der vollstaendige Baum
    // (inklusive Experte-Block) steht in der Signale-Ansicht. Die Deckelung
    // bleibt trotzdem bestehen, auch wenn die funktionale Menge fuer die
    // beiden bislang bekannten Geraete klein ist (5 bzw. 17): ein Geraet mit
    // mehr funktionalen Signalen als hier Platz haben, ist von dieser Regel
    // nicht ausgeschlossen.
    //
    // Frueher hiessen diese drei Helfer `exportableSignalsFor`/
    // `firstSignalsFor`/`remainingSignalCount`, gefiltert auf `exportable`
    // statt auf `functional`, und die Ueberschrift daneben hiess "Signale
    // (Anfang der Liste)" (Review-Fix Fix 9, 2026-09-03): `exportable`
    // beantwortet nur, ob ein Wert TECHNISCH auf einen Loxone-Eingang
    // passt, nicht, ob ihn jemand WILL - eine Steckdose hat 110
    // exportierbare Signale, darunter Netzwerk- und Geraeteangaben, aber
    // nur 5 funktionale. Seit `signal.functional` das direkt beantwortet
    // (statt einer geratenen Reihenfolge), ist die Kurzliste wieder ehrlich
    // benennbar.
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

    // Signale-Ansicht (Aufgabe 8): "Funktional" zeigt sofort, was
    // `is_functional` als gewollt einstuft; "Experte" bleibt zugeklappt,
    // bis `showExpertSignals` das global fuer alle Geraetekarten umschaltet
    // - dieselbe Datengrundlage wie oben, nur ungefiltert nach der
    // jeweils anderen Bedingung. Keine der beiden Listen bildet die
    // Relevanz-Regel selbst nach: beide lesen nur `signal.functional`, das
    // die API bereits fertig mitliefert (`api.devices._signal_out`).
    expertSignalsFor(deviceId) {
      const signals = this.signalsByDevice[deviceId];
      return signals ? signals.filter((signal) => !signal.functional) : [];
    },

    // Beide Bloecke der Signale-Ansicht als eine Liste (Review-Fix 6,
    // Nachbesserung Phase 6): vorher stand die Signalzeilen-Vorlage in
    // index.html zweimal, byte-identisch bis auf `functionalSignalsFor`
    // gegen `expertSignalsFor` - 51 Zeilen doppelt, die bei jeder
    // Aenderung zweimal angefasst werden mussten, ohne dass etwas ein
    // Auseinanderlaufen bemerkt haette. `collapsible` steuert in der
    // Vorlage, ob ein Block hinter `showExpertSignals` versteckt ist und
    // seine Anzahl in der Ueberschrift zeigt - der Rest (Zeilen-Markup,
    // leer-Hinweis) ist fuer beide Gruppen identisch.
    signalGroupsFor(deviceId) {
      return [
        { key: "functional", title: t("web.signals.group_functional"), collapsible: false, signals: this.functionalSignalsFor(deviceId) },
        { key: "expert", title: t("web.signals.group_expert"), collapsible: true, signals: this.expertSignalsFor(deviceId) },
      ];
    },

    liveValueOf(signal) {
      // Direkter Zugriff, kein `hasOwnProperty` - siehe `isOnline`. Genau
      // hier war der Fehler am sichtbarsten: die Werte bewegten sich nicht.
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

    // Die Rueckfrage benennt die Objekte, die verwaisen (Spec 9, Zeile
    // "Geraet entfernt und neu eingelernt"; Review-Fix Fix 10,
    // 2026-09-03). Genannt werden der Schluesselpraefix und die beiden
    // Vorlagendateien - und zwar nur bis zur Geraete-ID (`VIU_d12_….xml`),
    // nicht der vollstaendige Dateiname: dessen zweite Haelfte entsteht aus
    // `export.documents.filename_for`, das das Label auf ASCII normalisiert
    // (Umlaute, Sonderzeichen, Mehrfach-Unterstriche). Diese Regel hier in
    // JavaScript nachzubilden hiesse, sie ein zweites Mal zu pflegen - genau
    // die Verdopplung, die Fix 8 an anderer Stelle gerade beseitigt hat.
    // Der Praefix mit der Geraete-ID ist eindeutig (siehe `filename_for`)
    // und reicht, um die Datei in Loxone Config wiederzufinden.
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
    // Kurzmeldungen (2026-09-03)
    // ---------------------------------------------------------------------

    /**
     * Zeigt eine Meldung als Overlay am unteren Rand. Bewusst NICHT im
     * Textfluss: die vorherige Fassung blendete eine Zeile ueber der
     * Geraeteliste ein, wodurch beim Schalten die ganze Seite sprang und
     * der naechste Klick daneben ging.
     *
     * Fehler bleiben deutlich laenger stehen als Erfolge - eine
     * Erfolgsmeldung hat man gesehen, sobald das Geraet reagiert, eine
     * Fehlermeldung will man lesen.
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
    // Lebenszeichen (2026-09-03)
    // ---------------------------------------------------------------------

    /**
     * "vor 3 s", "vor 12 min" - oder null, wenn dieser Schluessel ueber die
     * Live-Verbindung noch nie kam. Liest `nowTick`, damit Alpine die
     * Angabe jede Sekunde neu zeichnet.
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

    /** Wann zuletzt IRGENDETWAS ueber die Leitung kam - der Heartbeat
     * eingeschlossen. Das ist die Angabe, die "nichts aendert sich" von
     * "nichts kommt an" unterscheidet. */
    heartbeatText() {
      return this.sinceText(this.lastHeartbeatAt);
    },

    /** Wann dieses eine Signal zuletzt einen Wert lieferte. Steht nur noch
     * im `title` der Zelle, nicht mehr daneben im Textfluss: eine Angabe wie
     * "vor 7 s", die sich jede Sekunde aendert, aendert dabei auch ihre
     * Breite und schiebt die Zeile hin und her. Das zog den Blick auf die
     * Bewegung statt auf die Aenderung, um die es geht (2026-09-03). */
    signalSeenText(signal) {
      return this.sinceText(this.liveSeenAt[signal.key]);
    },

    /** Ob dieses Signal gerade eben einen Wert bekommen hat. Traegt die
     * Hervorhebung, die den Blick an die richtige Stelle zieht - ohne dass
     * sich am Aufbau der Zeile irgendetwas bewegt. Liest `nowTick`, damit
     * Alpine die Klasse wieder loswird, wenn die Zeit um ist. */
    signalIsFresh(signal) {
      const at = this.liveSeenAt[signal.key];
      return at !== undefined && this.nowTick - at < VALUE_FRESH_MS;
    },

    /** Der Tooltip einer Wertzelle: wann der Wert zuletzt kam, oder ein
     * Hinweis, dass seit dem Laden der Seite nichts kam. */
    signalAgeTitle(signal) {
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
      try {
        const body = { code: this.commissionCode.trim() };
        if (this.commissionThreadDataset.trim()) {
          body.thread_dataset = this.commissionThreadDataset.trim();
        }
        const device = await this.request("POST", "/api/devices/commission", body);
        this.devices.push(device);
        // Karte ist ab sofort sichtbar und immer offen (Abschnitt 3) - ohne
        // dieses Nachladen zeigte sie "Signale werden geladen…" dauerhaft,
        // bis irgendwann die Ansicht neu betreten wuerde.
        await Promise.all([this.loadControls(device.id), this.loadSignals(device.id)]);
        // Der Satz zur Subscription ist kein Schmuck (Review-Fix Fix 3,
        // 2026-09-03, siehe Spec 12.3): `BridgeMatterClient.subscribe()`
        // laeuft genau einmal beim Start der Bruecke und meldet nur die
        // damals bekannten (Node, Pfad)-Paare an. Ein gerade eingelerntes
        // Geraet geht ueber das NODE_ADDED-Ereignis sofort auf "online"
        // und erscheint gruen - bekommt aber bis zum naechsten Neustart
        // keinen einzigen Attributwert. Ohne diesen Hinweis sieht der
        // Anwender ein gruenes Geraet, dessen Signale alle auf "-" stehen,
        // und sucht den Fehler bei sich.
        this.commissionMessage = t("web.devices.commission_success", { label: device.label });
        this.commissionMessageIsError = false;
        this.commissionCode = "";
        this.commissionThreadDataset = "";
      } catch (error) {
        this.commissionMessage = t("web.devices.commission_failed", { message: error.message });
        this.commissionMessageIsError = true;
      } finally {
        this.commissionBusy = false;
      }
    },

    // ---------------------------------------------------------------------
    // Signale
    // ---------------------------------------------------------------------

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
        this.signalsError = `Resend-Kennzeichen konnte nicht geaendert werden: ${error.message}`;
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
    // Einstellungen
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

    /** Setzt die gemeinsame Spracheinstellung (PATCH /api/language, Aufgabe 1)
     * und laedt danach die ganze Seite neu - bestaetigte, einfachere Variante
     * aus dem Entwurfsgespraech (Spec Abschnitt 7): kein Sonderfall fuer
     * bereits angezeigte Toasts oder WebSocket-Zustaende, die sonst in der
     * alten Sprache stehen blieben.
     *
     * try/catch/finally um `this.request(...)` - dieselbe Form wie
     * `saveSettings()` oben (Review-Fix Important, Whole-Branch-Review
     * 2026-09-04): `this.request` wirft erneut bei jedem Fehler ausser 401,
     * ohne dieses try/catch waere ein Fehlschlag (z. B. 400/502) eine
     * unbehandelte Promise-Ablehnung ohne jede Rueckmeldung fuer den
     * Nutzer. `settingsBusy` verhindert ausserdem, dass ein schneller
     * Doppelklick zwei gleichzeitige PATCH-Aufrufe abfeuert - wird mit
     * `saveSettings()` geteilt, beide Aktionen leben in derselben
     * Einstellungen-Karte. */
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
        this.resendIntervalError = `Resend-Intervall konnte nicht geladen werden: ${error.message}`;
      }
    },

    async saveResendInterval() {
      this.resendIntervalError = null;
      if (!Number.isFinite(this.resendIntervalDraft) || this.resendIntervalDraft < 10) {
        this.resendIntervalError = "Bitte ein Intervall von mindestens 10 Sekunden eingeben.";
        return;
      }
      this.resendIntervalBusy = true;
      try {
        this.resendInterval = await this.request("PATCH", "/api/settings/resend-interval", {
          interval_seconds: Number(this.resendIntervalDraft),
        });
        this.showToast("Resend-Intervall gespeichert.");
      } catch (error) {
        this.resendIntervalError = `Resend-Intervall konnte nicht gespeichert werden: ${error.message}`;
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

    // `only_pending` geht mit auf die Leitung (Review-Fix Fix 4,
    // 2026-09-03). Vorher galt der Filter nur fuer die Tabelle darueber,
    // waehrend der Download ausnahmslos alle Geraete lieferte UND alle als
    // exportiert markierte - wer gefiltert hat, ein ausstehendes Geraet sah
    // und herunterlud, bekam alles und hatte den Filter danach fuer immer
    // leer. Jetzt entscheidet dasselbe Kaestchen ueber beides, und
    // `/api/export/download` markiert nur, was es tatsaechlich ausgeliefert
    // hat (siehe `api/export.py`).
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

    // Frueher ein gewoehnlicher `<a href>`: eine Fehlerantwort (z. B. 401
    // nach abgelaufener Sitzung, oder 422 bei leerem Pflichtfeld) haette die
    // ganze Seite durch ihren rohen Text ersetzt statt in der Oberflaeche zu
    // erscheinen (Review-Fix Fix 1a, 2026-09-03). Deshalb ueber `download()`,
    // das wie jeder andere Aufruf `requestDownload()` nutzt.
    //
    // Die IP-Pruefung stand vorher aus demselben Grund hier: ohne sie
    // ersetzte ein Klick bei leerem IP-Feld die Seite durch die rohe
    // 422-Fehlerantwort des Backends (Pflichtparameter `bridge_ip`, siehe
    // `api/export.py`) - fuer ein Diagnosewerkzeug, das gerade in
    // schwierigen Momenten benutzt wird, ist eine Fehlermeldung an
    // derselben Stelle, an der schon die Vorschau ihre Fehler zeigt, die
    // bessere Antwort.
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
      // Ein Download IST ein Export (siehe `api/export.py`, Entscheidung 1):
      // er schreibt `exported_at` fuer jedes ausgelieferte Geraet. Ohne
      // dieses Nachladen zeigte die Spalte "Zuletzt exportiert" weiter den
      // Stand von vorhin und der Filter "nur noch nicht exportierte" die
      // gerade exportierten Geraete - bis irgendwann jemand die Vorschau neu
      // lud (Review-Fix Fix 12, 2026-09-03).
      await this.loadExportStatus();
    },

    // Export-Knopf an einer einzelnen Geraetekarte (Geraete-Dashboard-
    // Entwurf, Abschnitt 6) - kein Vorschauschritt: die Werte stehen ja
    // bereits offen auf der Karte, eine zusaetzliche Vorschau waere
    // doppelte Information.
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

    // Laedt nur noch den Systemcheck einmalig per GET - Datagramme,
    // Kommando-Log und Logzeilen liefert seit Aufgabe 6 laufend der
    // Diagnose-Kanal (`connectDiagnosticsLive`, oeffnet beim Wechsel auf
    // diese Ansicht in `selectView`). Fuer den Systemcheck gibt es dagegen
    // keinen dritten Strom auf `/api/diagnostics/live` - er bleibt ein
    // einmaliger Abruf, ausgeloest hier und ueber den "Aktualisieren"-Knopf.
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

    // Ebenfalls kein `<a href>` mehr (siehe `downloadExport`): ein
    // Fehler (z. B. 503 ohne eingehaengtes Datenverzeichnis) soll als
    // lesbare Meldung erscheinen, statt eine heruntergeladene Datei zu
    // ergeben, die in Wahrheit eine Fehlermeldung ist.
    async downloadFabricBackup() {
      this.backupError = null;
      try {
        await this.download("/api/diagnostics/fabric-backup", "matter-fabric-backup.zip");
      } catch (error) {
        this.backupError = t("web.system.backup_error", { message: error.message });
      }
    },

    // ---------------------------------------------------------------------
    // Projektdatei-Sync (Aufgabe 12)
    // ---------------------------------------------------------------------

    /**
     * Laedt die hochgeladene Loxone-Projektdatei zu `/api/export/project-sync`
     * hoch und zeigt die Antwort (Plan + beide gepatchten Dateien) an.
     * Dieselbe IP-Pruefung wie `downloadExport`/`exportDevice`: ohne sie
     * ersetzte ein Klick bei leerem IP-Feld die Seite durch die rohe
     * 422-Fehlerantwort des Backends (Pflichtparameter `bridge_ip`).
     */
    async uploadProjectFile(event) {
      const input = event.target;
      const file = input.files && input.files[0];
      if (!file) {
        return;
      }
      this.projectSync.error = "";
      if (!this.bridgeSettings.bridge_ip) {
        this.projectSync.error =
          "Bitte zuerst in Einstellungen → Verbindung zum Miniserver die Brücken-IP hinterlegen.";
        input.value = "";
        return;
      }
      this.projectSync.busy = true;
      this.projectSync.plan = null;
      this.projectSync.file = file.name;
      try {
        const formData = new FormData();
        formData.append("file", file);
        const params = new URLSearchParams({
          bridge_ip: this.bridgeSettings.bridge_ip,
          port: String(this.bridgeSettings.udp_port),
          listen: String(this.bridgeSettings.listen_port),
        });
        if (this.projectSync.miniserverIp.trim()) {
          params.set("miniserver_ip", this.projectSync.miniserverIp.trim());
        }
        this.projectSync.plan = await this.upload(`/api/export/project-sync?${params}`, formData);
      } catch (error) {
        this.projectSync.error = `Hochladen fehlgeschlagen: ${error.message}`;
      } finally {
        this.projectSync.busy = false;
        // Loescht die Dateiauswahl im Eingabefeld selbst - ohne das loest
        // ein erneuter Upload DERSELBEN Datei kein `change`-Ereignis mehr
        // aus, weil sich der Wert des Feldes aus Sicht des Browsers nicht
        // geaendert hat.
        input.value = "";
      }
    },

    /** Deutsche Kurzbezeichnung fuer die `PlanStatus`-Werte aus
     * `projectsync/diff.py` (`unchanged`, `updated`, `new_signal`,
     * `new_device`, `orphaned`, `conflict`, `possible_duplicate`) - die
     * Rohwerte sind Englisch (Bezeichner-Konvention dieses Projekts, siehe
     * Kommentar am Kopf dieser Datei), duerfen aber nicht unuebersetzt auf
     * dem Bildschirm landen. */
    projectSyncStatusLabel(status) {
      const labels = {
        unchanged: "Unverändert",
        updated: "Aktualisiert",
        new_signal: "Neues Signal",
        new_device: "Neues Gerät",
        orphaned: "Verwaist – wird nicht verändert",
        conflict: "Konflikt – wird übersprungen",
        possible_duplicate: "Mögliches Duplikat – wird übersprungen",
      };
      return labels[status] || status;
    },

    /** Badge-Farbe fuer `projectSyncStatusLabel`. Vier eigenstaendige Faelle
     * statt vorher drei (Nutzerwunsch nach dem Review: neu/aktualisiert
     * muessen sich auf den ersten Blick unterscheiden lassen, nicht beide
     * als `warn` zusammenfallen) - `ok` (gruen) fuer alles Neue ist dieselbe
     * Farbsprache wie ein hinzugefuegter Diff in einer Versionsverwaltung,
     * `warn` bleibt exklusiv fuer `updated`, `off` (dieselbe neutrale Farbe
     * wie eine Geraetekarte im Zustand "offline") fuer `orphaned`, `danger`
     * fuer `conflict` UND `possible_duplicate` (beide werden nie automatisch
     * angelegt/uebernommen, beide verdienen dieselbe "hinschauen"-Farbe).
     * `unchanged` braucht hier kein Badge mehr - es erscheint nur noch als
     * schlichter Chip, siehe `projectSyncSplitBySignificance`. */
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

    /** Ordnet einen Plan-Status einem von fuenf Sammel-Eimern zu - dieselbe
     * Einteilung liegt sowohl den Zaehlern je Geraet (`projectSyncGrouped
     * Entries`) als auch der Gesamt-Uebersicht oben (`projectSyncOverall
     * Counts`) und der CSS-Klasse jeder Eintragszeile (`is-<bucket>`)
     * zugrunde - eine einzige Zuordnung statt mehrerer, die auseinanderlaufen
     * koennten. `possible_duplicate` teilt sich den `conflict`-Eimer: beide
     * sind "etwas stimmt hier nicht, bitte pruefen", nur der Beschriftungs-
     * und Erklaertext (`projectSyncStatusLabel`/`projectSyncEntryNote`)
     * unterscheidet sie fuer den Anwender. */
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
     * Gruppiert den flachen Plan nach Gerät und dort nochmal nach Ein-/
     * Ausgang - genau die Verschachtelung, in der die Signale hinterher als
     * virtuelle Ein-/Ausgänge in Loxone Config landen (ein Container je
     * Gerät, `Eingänge` und `Ausgänge` als eigene Gruppen darunter), statt
     * einer einzigen langen, unsortierten Liste.
     *
     * Verwaiste Einträge (`device_id === -1`, siehe `PlanEntry` in `diff.py`
     * - gehören zu keinem aktuell bekannten Gerät mehr) bekommen eine eigene
     * Gruppe ohne echten Gerätenamen und stehen bewusst am Ende, unabhängig
     * von ihrer Position im flachen Plan.
     *
     * Jede Gruppe trägt zusätzlich `counts` (je Status-Eimer, für die
     * Zähl-Chips im aufklappbaren Kartenkopf) und `needsAttention` (alles
     * außer `unchanged` - steuert, ob die Karte beim ersten Anzeigen schon
     * aufgeklappt ist). `sections` bündelt Ein-/Ausgänge bereits vorsortiert
     * in "braucht einen Blick" vs. "unverändert, eingeklappt" (`projectSync
     * SplitBySignificance`) - einmal hier berechnet statt bei jedem
     * Render erneut im Template.
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
              entry.device_id === -1 ? "Nicht mehr zugeordnet" : entry.device_label || "—",
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
          { label: "Eingänge", ...this.projectSyncSplitBySignificance(group.inputs) },
          { label: "Ausgänge", ...this.projectSyncSplitBySignificance(group.outputs) },
        ];
      }
      return groups;
    },

    /** Trennt eine Liste von Einträgen in `attention` (alles außer
     * `unchanged` - wird immer als eigene Zeile mit Status und ggf. Diff
     * gezeigt) und `unchanged` (wird nur noch als schlichter Chip hinter
     * einer eingeklappten Zusammenfassung gezeigt, siehe `index.html`) -
     * bei einer echten, seit Jahren gewachsenen Datei sind das schnell
     * Dutzende Signale, die längst stimmen und beim Überblick nur stören
     * würden (Nutzerwunsch: "schneller und sauberer Überblick"). */
    projectSyncSplitBySignificance(items) {
      const attention = [];
      const unchanged = [];
      for (const entry of items) {
        (entry.status === "unchanged" ? unchanged : attention).push(entry);
      }
      return { attention, unchanged };
    },

    /** Gesamtzahl je Status-Eimer über den kompletten Plan - Grundlage der
     * Übersichtszeile ganz oben, bevor man sich durch die einzelnen
     * Geräte-Karten klickt. */
    projectSyncOverallCounts(entries) {
      const counts = { new: 0, updated: 0, unchanged: 0, orphaned: 0, conflict: 0 };
      for (const entry of entries || []) {
        counts[this.projectSyncStatusBucket(entry.status)] += 1;
      }
      return counts;
    },

    /** Fuer die "Alles aktuell"-Meldung (Review-Fix Important #5): `orphaned`,
     * `conflict` und `possible_duplicate` sind informativ und werden nie
     * gepatcht (siehe `SyncPlan.has_changes` in `diff.py`, das genau diese
     * Status bewusst ausklammert), muessen aber trotzdem sichtbar bleiben,
     * auch wenn `has_changes` deshalb `false` ist. */
    projectSyncHasInformationalEntries(entries) {
      return (entries || []).some((entry) =>
        ["orphaned", "conflict", "possible_duplicate"].includes(entry.status),
      );
    },

    /** Kurzer Erklärsatz unter dem Titel einer Eintragszeile - macht
     * `new_device` (kompletter neuer Container) und `new_signal` (nur ein
     * neues Kommando in einem bestehenden Container) auf einen Blick
     * unterscheidbar, ohne dass der Anwender erst den Unterschied der beiden
     * Badge-Texte nachschlagen muss (Nutzerwunsch: sehen, "welche Knoten +
     * Befehle" neu dazukommen). `possible_duplicate` (Anwenderbericht "zwei
     * mal onoff drin"): ein bestehender Befehl mit demselben Titel wurde
     * gefunden, aber unter einem anderen Schluessel - eher ein beschaedigtes
     * altes Objekt als ein wirklich neues Signal, deshalb keine automatische
     * Neuanlage. */
    projectSyncEntryNote(entry) {
      if (entry.status === "new_device") {
        return "Neuer virtueller Ein-/Ausgang wird für dieses Gerät angelegt.";
      }
      if (entry.status === "new_signal") {
        return "Neues Signal wird im bestehenden Ein-/Ausgang ergänzt.";
      }
      if (entry.status === "orphaned") {
        return "Gehört zu keinem bekannten Gerät mehr.";
      }
      if (entry.status === "conflict") {
        return "Unerwartete Struktur in der Datei.";
      }
      if (entry.status === "possible_duplicate") {
        return "Ein bestehender Befehl trägt bereits diesen Titel, aber unter einem anderen Schlüssel (z. B. durch eine beschädigte Check-/CmdOn-Kennung) – wird nicht automatisch angelegt, um keine Dopplung zu erzeugen. Bitte in Loxone Config manuell prüfen.";
      }
      return "";
    },

    /** Deutsche Beschriftung fuer die Attributnamen aus `entry.changes` -
     * dieselben Schluessel wie `MANAGED_INPUT_CMD_ATTRS`/
     * `MANAGED_OUTPUT_CMD_ATTRS` in `projectsync/schema.py`. Unbekannte
     * Namen (sollte nicht vorkommen) erscheinen unuebersetzt statt zu
     * verschwinden. */
    projectSyncAttrLabel(attr) {
      const labels = {
        Title: "Titel",
        Check: "Prüfbefehl",
        Analog: "Analog",
        Unit: "Einheit",
        CmdOn: "Befehl Ein",
        CmdOff: "Befehl Aus",
      };
      return labels[attr] || attr;
    },

    /**
     * Wandelt `entry.changes` (nur bei `status === "updated"` befuellt,
     * sonst leer - siehe `ProjectSyncEntryOut` in `api/models.py`) in eine
     * Liste aus `{label, oldValue, newValue}` fuer die Diff-Zeilen im
     * Template (Review-Fix Important #6, jetzt strukturiert statt als ein
     * einzelner Fliesstext, damit Alt- und Neu-Wert getrennt gestylt werden
     * koennen). Reines `x-text` im Template, nie `x-html`: die Werte stammen
     * aus der hochgeladenen Projektdatei und sind nicht vertrauenswuerdig.
     */
    projectSyncChangeList(entry) {
      const changes = entry.changes || {};
      return Object.entries(changes).map(([attr, values]) => {
        const [oldValue, newValue] = values;
        return { label: this.projectSyncAttrLabel(attr), oldValue, newValue };
      });
    },

    /**
     * Baut den Blob aus der Base64-kodierten Datei, die bereits Teil der
     * Plan-Antwort war (kein zweiter Aufruf an die Bruecke noetig) - der
     * Haken "Neue Geraete-Container ebenfalls anlegen" waehlt dabei nur
     * aus, WELCHE der beiden mitgelieferten Fassungen heruntergeladen wird.
     *
     * `patched_with_new_devices_base64` kann `null` sein, wenn die
     * hochgeladene Datei keinen `VirtualInCaption`/`VirtualOutCaption`-
     * Abschnitt fuer einen neuen Geraete-Container enthaelt
     * (`new_devices_unavailable_reason` traegt dann den Grund, angezeigt in
     * `index.html`). Der Haken ist in dem Fall bereits deaktiviert - diese
     * Pruefung hier ist nur die zweite Verteidigungslinie, falls er trotzdem
     * angehakt sein sollte, und faellt still auf die konservative Fassung
     * zurueck statt `atob(null)` auszuloesen.
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
        ? "loxmatter-projekt-gepatcht-mit-neuen-geraeten.Loxone"
        : "loxmatter-projekt-gepatcht.Loxone";
      link.click();
      // Verzoegertes Freigeben wie in `requestDownload` oben - manche
      // Browser (Firefox) starten den Download eines Objekt-URLs erst nach
      // dem laufenden Aufrufstapel.
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
    },

    // ---------------------------------------------------------------------
    // Live-Diagnose (Aufgabe 6, Spec 10.5)
    // ---------------------------------------------------------------------

    /**
     * Oeffnet den Diagnose-Kanal (`/api/diagnostics/live`) - dasselbe
     * Aufraeum-vor-Neuaufbau-Muster wie `connectLive()` fuer den Wertekanal
     * (siehe dort fuer die ausfuehrliche Begruendung, hier nicht wiederholt):
     * ein alter Timer wird zuerst gestoppt, ein alter Socket zuerst aus
     * `this.diagnosticsSocket` entfernt und DANACH geschlossen, damit dessen
     * `close`-Ereignis auf ein bereits ausgetauschtes Feld trifft und keine
     * zweite Wiederverbindung anstoesst.
     *
     * Kein Subprotokoll (Review der Aufgabenstellung, siehe
     * `.superpowers/sdd/task-6-report.md`): anders als im Aufgabentext
     * angenommen traegt `connectLive()` seit dem WebUI-Login KEIN
     * `["bearer", token]`-Subprotokoll mehr - das Sitzungs-Cookie reist bei
     * einem WebSocket zum selben Ursprung von selbst mit (siehe dessen
     * Kommentar). Dieser Kanal haengt an genau demselben `build_api_guard`
     * wie `/api/live` (`loxone/server.py`) und braucht deshalb denselben,
     * einfacheren Weg - ein erfundenes zweites Subprotokoll waere eine
     * Abweichung vom Vorbild, nicht ein Folgen.
     *
     * **Leert alle drei Straeme, BEVOR die neue Verbindung aufgebaut wird**
     * (Nachbesserung Task 6, 2026-09-03): jede (Wieder-)Verbindung bekommt
     * von `api/diagnostics_live.py` eine Momentaufnahme von bis zu
     * `SNAPSHOT_LIMIT` Eintraegen je Strom, in genau derselben
     * Nachrichtenform wie eine laufende Zeile und ohne eigene Kennzeichnung
     * als Momentaufnahme. Ohne dieses Leeren haengte sich diese
     * Momentaufnahme einfach an das bereits Gehaltene an - ein Wechsel weg
     * von "System" und zurueck, oder jede automatische Wiederverbindung
     * nach einem Netzhaenger, haette bis zu 150 bereits vorhandene Zeilen
     * ein zweites Mal angehaengt, auf dem gewoehnlichsten Weg durch die
     * Oberflaeche. Der einfachere der beiden moeglichen Wege gegenueber
     * einer serverseitigen Kennzeichnung der Momentaufnahme:
     * `clearDiagnosticsBuffers()` (dieselbe Funktion, die auch der
     * "Leeren"-Knopf ruft) macht die Unterscheidung "Momentaufnahme vs.
     * laufende Zeile" im Browser schlicht ueberfluessig, statt sie dort
     * nachzubilden - keine neue Nachrichtenform, kein Zusammenfuehren zweier
     * Quellen beim Anzeigen. Der Preis: eine Wiederverbindung verwirft auch
     * Zeilen, die aelter sind als die letzten `SNAPSHOT_LIMIT` je Strom (50)
     * und NICHT durch die folgende Momentaufnahme ersetzt werden - fuer eine
     * Diagnoseansicht, deren "Leeren"-Knopf genau das ohnehin schon jederzeit
     * bewusst anbietet, ist das kein neues Risiko, nur derselbe Verlust zu
     * einem zusaetzlichen Zeitpunkt.
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

      // Dieselbe `this.diagnosticsSocket === socket`-Pruefung wie bei
      // `connectLive()`: nur eine Verbindung, die noch als DIE aktuelle
      // gilt, darf eine Wiederverbindung ausloesen - eine bewusst
      // geschlossene (siehe `disconnectDiagnosticsLive`) hat `this.
      // diagnosticsSocket` da schon nicht mehr gesetzt.
      socket.addEventListener("close", () => {
        if (this.diagnosticsSocket === socket) {
          this.handleDiagnosticsDisconnect();
        }
      });
      socket.addEventListener("error", () => socket.close());

      this.diagnosticsSocket = socket;
    },

    /**
     * Schliesst den Diagnose-Kanal und stoppt jede geplante
     * Wiederverbindung - aufgerufen von `selectView`, sobald "System" nicht
     * mehr die aktive Ansicht ist (Falle 3: genau eine Verbindung, nur
     * waehrend die Ansicht offen ist). Setzt `this.diagnosticsSocket` VOR
     * dem `close()`-Aufruf auf `null`, damit der `close`-Handler oben diese
     * Trennung nicht mit einer Wiederverbindung beantwortet.
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
     * Reagiert auf den Abbruch des Diagnose-Kanals - sinngemaess
     * `handleLiveDisconnect` fuer den Wertekanal (siehe dort), an dieser
     * Ansicht statt an der Sitzung gemessen: die Ansicht kann laengst
     * verlassen worden sein, BEVOR dieses `close`-Ereignis eintrifft
     * (asynchron), und `disconnectDiagnosticsLive` hat `this.
     * diagnosticsSocket` dann schon geleert - der `close`-Handler oben ruft
     * diese Funktion in dem Fall gar nicht erst auf. Trifft sie trotzdem auf
     * eine inzwischen verlassene Ansicht (z. B. ein Wechsel genau waehrend
     * dieser Aufruf schon laeuft), bricht sie hier ab, statt im Hintergrund
     * weiterzuversuchen.
     */
    async handleDiagnosticsDisconnect() {
      this.diagnosticsConnected = false;
      if (this.view !== "system") {
        return;
      }
      // Wie bei `handleLiveDisconnect`: eine 401 mitten im Betrieb heisst,
      // die Sitzung ist abgelaufen - dann zurueck zum Login statt im
      // Sekundentakt gegen eine ungueltige Sitzung weiterzuversuchen.
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
     * Verteilt eine Nachricht des Diagnose-Kanals an ihren Strom, nach
     * `message.kind` (siehe api/diagnostics_live.py fuer die drei Formen).
     * Waehrend `diagnosticsPaused` gesetzt ist, wird NICHTS angehaengt -
     * das ist die Pause selbst, kein Anzeigefilter (siehe deren Kommentar
     * im Zustand oben).
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
      // Eine unbekannte `kind` wird still ignoriert statt zu werfen: eine
      // kuenftige, hier noch unbekannte Nachrichtenart soll die Verbindung
      // nicht abreissen lassen.
    },

    /** Haengt an, gedeckelt auf DIAGNOSTICS_LINE_LIMIT je Strom (siehe dort). */
    appendDiagnosticsEntry(list, entry) {
      list.push(entry);
      if (list.length > DIAGNOSTICS_LINE_LIMIT) {
        list.shift();
      }
    },

    /**
     * Haelt eine `.log-list` oben angeheftet, nachdem eine neue Zeile
     * eingetroffen ist - aber nur, wenn man dort ohnehin schon war. Die
     * Vorlage zeigt die Straeme umgekehrt an (jüngste zuerst, siehe
     * `visibleDatagrams`/`visibleDiagnosticsLogs`), daher bedeutet
     * "mitscrollen" hier: Scrollposition oben (0) halten, nicht ans Ende
     * springen. Wer nach unten gescrollt hat, um aeltere Zeilen zu lesen,
     * wird durch neu eintreffende Zeilen nicht zurueckgerissen - der
     * Toleranzwert (4px) faengt Rundungsreste vom Scrollen ab, kein
     * Trackpad/Mausrad haelt exakt bei 0 an.
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
     * Die UDP-Mitschnitt-Zeilen, wie `hideNoise` sie gerade zeigen soll.
     * Der Filter liest `entry.forced` (`api/diagnostics_live.py`, gefuellt
     * aus `DatagramLogEntry.forced` - siehe dort fuer die Begruendung,
     * warum diese Auskunft vom Server kommt statt aus einer im Browser
     * nachgebauten Zeitheuristik): `True` steht ausschliesslich fuer den
     * Heartbeat und einen Full-Resend, niemals fuer eine echte
     * Wertaenderung - auch dann nicht, wenn zwei echte Aenderungen (z. B.
     * ein Impuls und sein Zaehler, siehe `Runtime.on_event`) binnen
     * Mikrosekunden hintereinander eintreffen.
     */
    visibleDatagrams() {
      const entries = this.hideNoise
        ? this.datagrams.filter((entry) => !entry.forced)
        : this.datagrams;
      // Angezeigt wird umgekehrt (juengste zuerst) - der Ringpuffer selbst
      // bleibt aeltester-zuerst, damit `appendDiagnosticsEntry` mit `shift()`
      // weiterhin am aeltesten Eintrag kappt (siehe dort).
      return [...entries].reverse();
    },

    /**
     * Die Logzeilen ab `logLevel` (siehe LOG_LEVEL_ORDER oben). Eine Zeile
     * mit einer hier unbekannten Stufe bleibt sichtbar statt stillschweigend
     * zu verschwinden.
     */
    visibleDiagnosticsLogs() {
      const threshold = LOG_LEVEL_ORDER.indexOf(this.logLevel);
      const entries = this.diagnosticsLogs.filter((entry) => {
        const rank = LOG_LEVEL_ORDER.indexOf(entry.level);
        return rank === -1 || rank >= threshold;
      });
      // Siehe Kommentar in visibleDatagrams(): Anzeige umgekehrt, Ringpuffer nicht.
      return entries.reverse();
    },

    /**
     * Leert alle drei gehaltenen Straeme auf dieser Seite - nur die Anzeige
     * in diesem Tab, keine Wirkung auf die Ringe des Servers (die naechste
     * Momentaufnahme beim erneuten Verbinden zeigt sie unveraendert wieder).
     * Auch von `connectDiagnosticsLive()` selbst gerufen, VOR jedem
     * (Wieder-)Aufbau der Verbindung - siehe dortiger Kommentar.
     */
    clearDiagnosticsBuffers() {
      this.datagrams = [];
      this.commandLog = [];
      this.diagnosticsLogs = [];
    },

    // ---------------------------------------------------------------------
    // Live-Verbindung (Spec 8.3)
    // ---------------------------------------------------------------------

    connectLive() {
      // Aufraeumen vor jedem Neuaufbau - frueher der Rumpf von
      // `restartLive()`, das der Token-Aufraeumung diente (Tokenwechsel
      // machte eine bestehende Verbindung ungueltig) und beim Entfernen der
      // Token-Eingabe als vermeintlich toter Code mitgeloescht wurde. War es
      // nicht: dieselbe Aufraeumung fehlt jetzt auch dem Fall, dass
      // `connectLive()` waehrend eine ALTE Verbindung noch lebt erneut
      // aufgerufen wird - etwa wenn nach einer abgelaufenen Sitzung
      // (`noteAuthError`, Login-Bildschirm) gleichzeitig der
      // `reconnectTimer` der alten Verbindung noch armiert ist UND
      // `submitPassword` -> `startApp()` nach der Neuanmeldung selbst einen
      // Aufruf ausloest: ohne dieses Aufraeumen liefe der alte, verwaiste
      // Socket authentifiziert weiter (sein `close`-Handler haette den
      // Vergleich `this.socket === socket` schon gegen den NEUEN Socket
      // verloren und loest daher nie eine Wiederverbindung aus), waehrend
      // der Timer kurz danach eine dritte Verbindung eroeffnet.
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
      // Kein Subprotokoll mehr: das Sitzungs-Cookie reist beim Handshake von
      // selbst mit, weil dieser WebSocket denselben Ursprung hat wie die
      // Seite. Der frueher noetige Umweg `new WebSocket(url, ["bearer",
      // token])` - und mit ihm der Sonderfall, dass ein Token mit Leerzeichen
      // den Konstruktor synchron werfen liess - entfaellt ersatzlos. Der
      // Server liest das Subprotokoll weiterhin, aber fuer Skripte (siehe
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
        // Der Heartbeat gehoert zu keinem Geraet (Spec 6.5) und ist genau
        // deshalb das ehrliche Lebenszeichen: er kommt auch dann, wenn
        // sich an keinem Geraet etwas aendert.
        if (message.key === HEARTBEAT_KEY) {
          this.lastHeartbeatAt = now;
        }
      });

      // Sowohl ein sauberes Schliessen als auch ein Verbindungsfehler
      // sollen dieselbe Wiederverbindung ausloesen - eine Oberflaeche, die
      // eingefrorene Werte weiter als aktuell zeigt, ist schlimmer als
      // eine, die zugibt, dass sie die Verbindung verloren hat. Die
      // Pruefung `this.socket === socket` verhindert, dass eine bewusst
      // verworfene Verbindung noch eine Wiederverbindung anstoesst: der
      // Aufraeum-Teil oben in `connectLive()` schliesst eine alte Verbindung
      // erst, NACHDEM er `this.socket` schon geleert hat, und dieser Aufruf
      // selbst setzt `this.socket` gleich im Anschluss auf den neuen Socket
      // - das `close`-Ereignis der alten Verbindung feuert asynchron und
      // trifft hier also auf ein `this.socket`, das schon nicht mehr sie
      // selbst ist. Nur ein Weg kann `connectLive()` ueberhaupt auf eine
      // noch lebende Verbindung treffen lassen: `startApp()` nach einer
      // Neuanmeldung. Der `reconnectTimer` unten dagegen entsteht erst aus
      // dem `close`-Ereignis dieser Verbindung und trifft daher immer auf
      // einen bereits geschlossenen Socket.
      socket.addEventListener("close", () => {
        if (this.socket === socket) {
          this.handleLiveDisconnect();
        }
      });
      socket.addEventListener("error", () => socket.close());

      this.socket = socket;
    },

    /**
     * Reagiert auf den Abbruch der Live-Verbindung: ein gewoehnlicher
     * Netzwerkausfall soll weiter automatisch wiederverbinden, eine
     * ungueltig gewordene Sitzung dagegen zurueck zum Login fuehren - ohne
     * diese Unterscheidung bliebe ein offener Tab fuer immer bei
     * "Verbindung verloren" haengen, weil der Browser eine mit 401
     * abgelehnte WebSocket-Verbindung nicht von einem echten Netzwerkfehler
     * unterscheiden kann (beides feuert nur `close`) und `scheduleReconnect`
     * es deshalb unbegrenzt im Sekundentakt weiter versuchen wuerde.
     *
     * Ausgeloest u. a. durch `loxmatter set-password` (meldet alle
     * Sitzungen ab) oder ein Logout in einem anderen Tab - kein Aufruf
     * dieser Seite erfaehrt sonst je davon, solange niemand nachfragt: es
     * gibt keinen periodischen HTTP-Aufruf, der den Sitzungszustand
     * einfaengt, die Ansichten laden nur auf Klick.
     *
     * `/auth-info` haengt ausserhalb des Waechters (siehe api/auth.py) und
     * ist genau fuer diese Frage da. Bleibt die Sitzung gueltig (oder
     * schlaegt schon die Anfrage selbst fehl, z. B. weil das Netz komplett
     * weg ist - `loadAuthInfo` ruehrt `authenticated` in diesem Fall nicht
     * an), geht es wie bisher mit `scheduleReconnect` weiter; der
     * exponentielle Backoff bleibt dadurch unveraendert wirksam.
     */
    async handleLiveDisconnect() {
      // ALS ERSTES, vor dem `await` unten: der Socket ist in diesem Moment
      // bereits tot (dieser Aufruf kommt aus seinem `close`-Ereignis), die
      // Kopfzeile meldete ohne diese Zeile aber bis zum Ende von
      // `loadAuthInfo()` weiter "Live-Verbindung aktiv" und liess den
      // Banner "Werte koennen veraltet sein" aus - genau der Zustand, den
      // der Kommentar in `connectLive()` oben als "schlimmer, als
      // zuzugeben, dass die Verbindung weg ist" beschreibt (Review-Fund,
      // 2026-09-03).
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
        // Noch nie erfolgreich verbunden gewesen - dieser Versuch war einer
        // der ERSTEN, nicht der Verlust einer bestehenden Verbindung
        // (Review-Fix Minor #4). Nach oben gedeckelt, damit die Zahl nicht
        // unbegrenzt waechst, waehrend die Bruecke dauerhaft unerreichbar
        // bleibt - `connectionStatusText()` unten fragt ohnehin nur, ob die
        // Schwelle erreicht ist, nicht nach dem genauen Wert.
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

    // Kopfzeilentext der Live-Verbindung (Spec 8.3) - als eigene Funktion
    // statt einer verschachtelten Bedingung direkt in `index.html`, seit
    // Review-Fix Minor #4 einen dritten Fall dazubekommen hat (siehe
    // `INITIAL_CONNECT_FAILURES_BEFORE_GIVING_UP_ON_SILENCE` oben).
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
    // Formatierung
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
