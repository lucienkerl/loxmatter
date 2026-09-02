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

// Schluessel, unter dem das API-Token im `localStorage` des Browsers liegt
// (Review-Fix Fix 1a, 2026-09-03).
//
// Warum `localStorage` und nicht etwas anderes: das Token muss einen
// Seitenwechsel und einen Neustart des Browsers ueberleben, sonst muesste es
// bei jedem Aufruf neu getippt werden - eine Diagnoseoberflaeche, die das
// verlangt, benutzt am Ende niemand, und ein Betreiber, der sie deshalb
// ohne Token betreibt, hat einen ungeschuetzten Dienst statt eines
// unbequemen. Das Token verlaesst den Browser ausschliesslich als
// `Authorization`-Header (bzw. als WebSocket-Subprotokoll, siehe
// `connectLive`) an DENSELBEN Ursprung, von dem diese Seite geladen wurde -
// es geht an keine dritte Stelle, in keine URL, in keinen Query-Parameter
// (der stuende in Server-Logs, Proxy-Logs und der Browser-History).
//
// Die bekannte Schwaeche von `localStorage` ist ein XSS: fremdes Skript im
// Ursprung dieser Seite koennte den Wert lesen. Das ist hier vertretbar,
// weil die Oberflaeche NIRGENDS Fremdinhalt als HTML rendert - jede
// Ausgabe laeuft ueber Alpines `x-text` (setzt `textContent`, nie
// `innerHTML`), es gibt kein `x-html`, kein `eval`, keinen `innerHTML`-
// Zuweisung und keine eingebundene Fremdressource (Alpine liegt vendort
// unter `/static/vendor/`, siehe Kopfkommentar von index.html). Wer Skript
// in diesen Ursprung einschleusen koennte, haette ohnehin schon Zugriff auf
// die ausgelieferten Dateien selbst - und damit ein groesseres Problem als
// das Token.
const TOKEN_STORAGE_KEY = "loxmatter_token";

// Erster Wert der WebSocket-Subprotokoll-Liste, an dem der Server erkennt,
// dass der zweite Wert das Token ist (siehe `connectLive` und
// `loxone.server.build_api_guard`).
const WEBSOCKET_BEARER_MARKER = "bearer";

/**
 * Liest das gespeicherte Token - `null`, wenn keins gesetzt ist.
 *
 * Bei jedem Aufruf frisch aus dem `localStorage` statt aus einer Kopie im
 * Zustand: so kann es keine zwei Wahrheiten geben, wenn das Token in einem
 * zweiten Tab geaendert wird. `localStorage` kann werfen (Browser mit
 * blockierten Website-Daten, privates Fenster in manchen Browsern) - dann
 * gilt "kein Token", und die Oberflaeche verhaelt sich wie ohne.
 */
function readStoredToken() {
  try {
    const token = window.localStorage.getItem(TOKEN_STORAGE_KEY);
    return token ? token : null;
  } catch {
    return null;
  }
}

/**
 * Die Kopfzeilen fuer einen Aufruf an `/api` - mit Token genau EIN
 * zusaetzlicher Header, ohne Token gar keiner (nicht `Bearer ` mit leerem
 * Wert, das waere ein Token "" und damit eine sinnlose 401).
 */
function authHeaders() {
  const token = readStoredToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/**
 * Fehler eines Aufrufs, dem das Token fehlt oder dessen Token falsch ist -
 * eigene Klasse, damit die Oberflaeche diesen Fall von jedem anderen
 * Fehlschlag unterscheiden kann, ohne auf einen Meldungstext zu pruefen.
 */
class UnauthorizedError extends Error {
  constructor() {
    super(
      "Für diesen Zugriff wird ein gültiges API-Token benötigt – " +
        "oben rechts unter „Token“ eintragen.",
    );
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
  return `HTTP ${response.status}`;
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
      headers: {
        ...authHeaders(),
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      },
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
    throw new Error("Die Brücke ist nicht erreichbar – sie läuft möglicherweise nicht.");
  }
  if (response.status === 401) {
    // Nicht der rohe Servertext ("Ungültiges oder fehlendes Token"): der
    // sagt zwar, WAS falsch ist, aber nicht, was jetzt zu tun ist. Wer das
    // Token gerade falsch abgetippt hat, muss das hier erkennen koennen -
    // deshalb eine eigene Fehlerklasse, die die Oberflaeche zum Eingabefeld
    // fuehrt (siehe `request` unten).
    throw new UnauthorizedError();
  }
  if (!response.ok) {
    throw new Error(await readErrorDetail(response));
  }
  if (response.status === 204) {
    return null;
  }
  return response.json();
}

/**
 * Laedt eine Datei von `/api` herunter - mit demselben Token-Header wie
 * `requestJson`, weil ein gewoehnlicher `<a href>` keinen Header tragen
 * kann und bei gesetztem Token nur die rohe 401-Antwort anzeigen wuerde.
 *
 * Die zweite (und letzte) `fetch()`-Stelle der Oberflaeche. Sie holt sich
 * ihre Kopfzeilen aus derselben `authHeaders()`-Funktion wie `requestJson`,
 * damit es fuer das Token weiterhin genau EINEN Ort gibt.
 */
async function requestDownload(path, filename) {
  let response;
  try {
    response = await fetch(path, { headers: authHeaders() });
  } catch {
    throw new Error("Die Brücke ist nicht erreichbar – sie läuft möglicherweise nicht.");
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

function app() {
  return {
    // --- Ansicht ---------------------------------------------------------
    view: "devices",

    // --- Zugang (Review-Fix Fix 1b, 2026-09-03) --------------------------
    // Kein Login, keine Sitzung: ein Feld fuer das API-Token, mehr nicht.
    // `tokenIsSet` haelt nur, OB eins gespeichert ist - der Wert selbst wird
    // nach dem Speichern nirgends mehr angezeigt (`tokenDraft` wird geleert),
    // damit er nicht ueber der Schulter mitlesbar auf dem Bildschirm steht.
    tokenIsSet: readStoredToken() !== null,
    tokenEditing: false,
    tokenDraft: "",
    authError: null,

    // --- Geraete -----------------------------------------------------------
    devices: [],
    devicesError: null,
    expandedDeviceId: null,
    controlsByDevice: {},
    commandValueDrafts: {},
    commandBusyKey: null,
    commandMessage: null,
    commandMessageIsError: false,
    labelDrafts: {},
    deviceActionError: null,

    // Einlernen (Spec 7.1).
    commissionCode: "",
    commissionThreadDataset: "",
    commissionBusy: false,
    commissionMessage: null,
    commissionMessageIsError: false,

    // --- Signale (geteilt mit der Geraete-Ansicht: dieselbe Liste dient
    // dort als "wichtigste Live-Werte") ------------------------------------
    signalsByDevice: {},
    signalsError: null,
    titleDrafts: {},
    rawWriteDrafts: {},
    rawWriteBusyKey: null,
    rawWriteMessages: {},

    // --- Export --------------------------------------------------------
    exportBridgeIp: "",
    exportPort: 7000,
    exportListenPort: 8080,
    exportIncludeSystem: false,
    exportOnlyPending: false,
    exportPreview: null,
    exportStatusByDevice: {},
    exportBusy: false,
    exportError: null,

    // --- System ----------------------------------------------------------
    systemChecks: [],
    systemError: null,
    datagrams: [],
    commandLog: [],
    diagnosticsBusy: false,
    backupError: null,

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

    async init() {
      await this.loadDevices();
      this.connectLive();
    },

    // ---------------------------------------------------------------------
    // Zugang: das API-Token (Review-Fix Fix 1b, 2026-09-03)
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

    noteAuthError(error) {
      if (error instanceof UnauthorizedError) {
        this.authError = error.message;
        this.tokenEditing = true;
      }
    },

    tokenStatusText() {
      return this.tokenIsSet ? "Token gesetzt" : "kein Token";
    },

    startTokenEdit() {
      // Absichtlich leer statt mit dem gespeicherten Token vorbelegt: ein
      // gespeichertes Geheimnis wird nie wieder angezeigt, auch nicht in
      // einem Passwortfeld, aus dem es sich mit zwei Handgriffen auslesen
      // liesse. Wer es ersetzen will, tippt es neu.
      this.tokenDraft = "";
      this.tokenEditing = true;
    },

    cancelTokenEdit() {
      this.tokenDraft = "";
      this.tokenEditing = false;
    },

    async saveToken() {
      const token = this.tokenDraft.trim();
      if (!token) {
        return;
      }
      try {
        window.localStorage.setItem(TOKEN_STORAGE_KEY, token);
      } catch {
        this.authError =
          "Das Token konnte nicht gespeichert werden – dieser Browser erlaubt keine " +
          "Website-Daten (privates Fenster oder blockierte Speicherung?).";
        return;
      }
      this.tokenIsSet = true;
      this.tokenDraft = "";
      this.tokenEditing = false;
      this.authError = null;
      await this.reloadAfterTokenChange();
    },

    async clearToken() {
      try {
        window.localStorage.removeItem(TOKEN_STORAGE_KEY);
      } catch {
        // Konnte nicht gespeichert werden, kann auch nicht geloescht
        // werden - dann war ohnehin nie etwas gespeichert.
      }
      this.tokenIsSet = false;
      this.tokenDraft = "";
      this.tokenEditing = false;
      this.authError = null;
      await this.reloadAfterTokenChange();
    },

    /**
     * Nach einem Wechsel des Tokens ist jeder bisher geladene Stand
     * fragwuerdig (er kann aus einer 401 stammen) und die Live-Verbindung
     * traegt noch das alte - oder gar kein - Token im Handshake. Deshalb
     * beides neu: die aktuelle Ansicht und der WebSocket.
     */
    async reloadAfterTokenChange() {
      // Auch die beiden Download-Fehler: eine Meldung "Token nötig", die
      // nach dem Eintragen des Tokens stehen bliebe, waere schlicht falsch.
      this.backupError = null;
      this.exportError = null;
      this.restartLive();
      await this.loadDevices();
      await this.selectView(this.view);
    },

    // ---------------------------------------------------------------------
    // Navigation
    // ---------------------------------------------------------------------

    async selectView(view) {
      this.view = view;
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
        this.devicesError = `Geraeteliste konnte nicht geladen werden: ${error.message}`;
      }
    },

    // Online-Status bevorzugt aus dem Live-Websocket (dessen Wert kann
    // sich seit dem letzten Laden der Liste geaendert haben, siehe Spec
    // 8.3) - fehlt er (noch keine Nachricht fuer dieses Geraet
    // eingetroffen), gilt der zuletzt geladene Stand aus `GET
    // /api/devices`.
    isOnline(device) {
      const liveKey = `d${device.id}_online`;
      if (Object.prototype.hasOwnProperty.call(this.liveValues, liveKey)) {
        return Boolean(this.liveValues[liveKey]);
      }
      return device.online;
    },

    async toggleExpanded(device) {
      if (this.expandedDeviceId === device.id) {
        this.expandedDeviceId = null;
        return;
      }
      this.expandedDeviceId = device.id;
      this.deviceActionError = null;
      await Promise.all([this.loadControls(device.id), this.loadSignals(device.id)]);
    },

    async loadControls(deviceId) {
      try {
        this.controlsByDevice[deviceId] = await this.request(
          "GET",
          `/api/devices/${deviceId}/controls`,
        );
      } catch (error) {
        this.deviceActionError = `Bedienelemente konnten nicht geladen werden: ${error.message}`;
      }
    },

    controlsFor(deviceId) {
      return this.controlsByDevice[deviceId] || null;
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

    // Kurzliste fuer die Geraete-Ansicht: nur, was nach Loxone exportiert
    // werden koennte, und davon nur die ersten paar - der vollstaendige
    // Baum steht in der Signale-Ansicht. Ohne diese Deckelung waere die
    // "wichtigste Werte"-Liste bei einem Geraet mit hundert exportierbaren
    // Signalen (kein Einzelfall, siehe IKEA-GRILLPLATS-Testvorlage) keine
    // Kurzliste mehr, sondern derselbe volle Baum ein zweites Mal.
    KEY_SIGNAL_LIMIT: 6,

    exportableSignalsFor(deviceId) {
      const signals = this.signalsByDevice[deviceId];
      return signals ? signals.filter((signal) => signal.exportable) : [];
    },

    keySignalsFor(deviceId) {
      return this.exportableSignalsFor(deviceId).slice(0, this.KEY_SIGNAL_LIMIT);
    },

    remainingKeySignalCount(deviceId) {
      return Math.max(0, this.exportableSignalsFor(deviceId).length - this.KEY_SIGNAL_LIMIT);
    },

    liveValueOf(signal) {
      if (Object.prototype.hasOwnProperty.call(this.liveValues, signal.key)) {
        return this.liveValues[signal.key];
      }
      return signal.value;
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
        this.deviceActionError = `Name konnte nicht gespeichert werden: ${error.message}`;
      }
    },

    async removeDevice(device) {
      const confirmed = window.confirm(
        `Gerät "${device.label}" wirklich entfernen? Das kann nicht rückgängig gemacht werden - ` +
          "bereits exportierte Loxone-Objekte bleiben dann verwaist.",
      );
      if (!confirmed) {
        return;
      }
      this.deviceActionError = null;
      try {
        await this.request("DELETE", `/api/devices/${device.id}`);
        this.devices = this.devices.filter((d) => d.id !== device.id);
        delete this.controlsByDevice[device.id];
        delete this.signalsByDevice[device.id];
        if (this.expandedDeviceId === device.id) {
          this.expandedDeviceId = null;
        }
      } catch (error) {
        this.deviceActionError = `Gerät konnte nicht entfernt werden: ${error.message}`;
      }
    },

    async executeCommand(device, command) {
      this.commandMessage = null;
      this.commandBusyKey = command.key;
      const value = command.takes_value ? this.commandValueDrafts[command.key] ?? "" : "1";
      try {
        await this.request("POST", `/api/commands/${command.key}`, { value: String(value) });
        this.commandMessage = `"${command.slug}" wurde an ${device.label} gesendet.`;
        this.commandMessageIsError = false;
      } catch (error) {
        this.commandMessage = `"${command.slug}" ist fehlgeschlagen: ${error.message}`;
        this.commandMessageIsError = true;
      } finally {
        this.commandBusyKey = null;
      }
    },

    async commissionDevice() {
      this.commissionMessage = null;
      if (!this.commissionCode.trim()) {
        this.commissionMessage = "Bitte zuerst einen Pairing-Code eingeben.";
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
        this.commissionMessage = `${device.label} wurde eingelernt.`;
        this.commissionMessageIsError = false;
        this.commissionCode = "";
        this.commissionThreadDataset = "";
      } catch (error) {
        this.commissionMessage = `Einlernen fehlgeschlagen: ${error.message}`;
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
        this.signalsError = `Signale konnten nicht geladen werden: ${error.message}`;
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
        this.signalsError = `Titel konnte nicht gespeichert werden: ${error.message}`;
      }
    },

    async toggleExported(signal) {
      try {
        const updated = await this.request("PATCH", `/api/signals/${signal.key}`, {
          exported: !signal.exported,
        });
        Object.assign(signal, updated);
      } catch (error) {
        this.signalsError = `Export-Kennzeichen konnte nicht geaendert werden: ${error.message}`;
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
        this.rawWriteMessages[signal.key] = { text: "Geschrieben.", isError: false };
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
        this.exportError = `Export-Status konnte nicht geladen werden: ${error.message}`;
      }
    },

    exportStatusFor(deviceId) {
      return this.exportStatusByDevice[deviceId] || null;
    },

    async previewExport() {
      this.exportError = null;
      if (!this.exportBridgeIp.trim()) {
        this.exportError = "Bitte zuerst die IP der Bruecke (aus Sicht des Miniservers) eingeben.";
        return;
      }
      this.exportBusy = true;
      try {
        const params = new URLSearchParams({
          bridge_ip: this.exportBridgeIp.trim(),
          system: String(this.exportIncludeSystem),
        });
        this.exportPreview = await this.request("GET", `/api/export/preview?${params}`);
        await this.loadExportStatus();
      } catch (error) {
        this.exportError = `Vorschau fehlgeschlagen: ${error.message}`;
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

    downloadUrl() {
      const params = new URLSearchParams({
        bridge_ip: this.exportBridgeIp.trim(),
        port: String(this.exportPort),
        listen: String(this.exportListenPort),
        system: String(this.exportIncludeSystem),
      });
      return `/api/export/download?${params}`;
    },

    // Frueher ein gewoehnlicher `<a href>`: der kann keinen
    // `Authorization`-Header tragen und wuerde bei gesetztem Token die
    // ganze Seite durch die rohe 401-Antwort ersetzen (Review-Fix Fix 1a,
    // 2026-09-03). Deshalb ueber `download()`, das denselben Header setzt
    // wie jeder andere Aufruf.
    //
    // Die IP-Pruefung stand vorher aus demselben Grund hier: ohne sie
    // ersetzte ein Klick bei leerer Miniserver-IP die Seite durch die rohe
    // 422-Fehlerantwort des Backends (Pflichtparameter `bridge_ip`, siehe
    // `api/export.py`) - fuer ein Diagnosewerkzeug, das gerade in
    // schwierigen Momenten benutzt wird, ist eine Fehlermeldung an
    // derselben Stelle, an der schon die Vorschau ihre Fehler zeigt, die
    // bessere Antwort.
    async downloadExport() {
      this.exportError = null;
      if (!this.exportBridgeIp.trim()) {
        this.exportError = "Bitte zuerst die IP der Bruecke (aus Sicht des Miniservers) eingeben.";
        return;
      }
      try {
        await this.download(this.downloadUrl(), "loxmatter-export.zip");
      } catch (error) {
        this.exportError = `Download fehlgeschlagen: ${error.message}`;
      }
    },

    // ---------------------------------------------------------------------
    // System
    // ---------------------------------------------------------------------

    async loadSystem() {
      this.systemError = null;
      this.diagnosticsBusy = true;
      try {
        const [checks, datagrams, commandLog] = await Promise.all([
          this.request("GET", "/api/diagnostics/system"),
          this.request("GET", "/api/diagnostics/datagrams"),
          this.request("GET", "/api/diagnostics/commands"),
        ]);
        this.systemChecks = checks;
        this.datagrams = datagrams;
        this.commandLog = commandLog;
      } catch (error) {
        this.systemError = `Diagnose konnte nicht geladen werden: ${error.message}`;
      } finally {
        this.diagnosticsBusy = false;
      }
    },

    // Ebenfalls kein `<a href>` mehr (siehe `downloadExport`). Zusaetzlich
    // faellt hier seit Review-Fix Fix 3 (2026-09-03) ein zweiter Fall an:
    // laeuft der Dienst ganz OHNE Token, verweigert er diese eine Route mit
    // 403 und einer Erklaerung im `detail` - die soll der Anwender lesen
    // koennen, statt eine heruntergeladene Datei zu bekommen, die in
    // Wahrheit eine Fehlermeldung ist.
    async downloadFabricBackup() {
      this.backupError = null;
      try {
        await this.download("/api/diagnostics/fabric-backup", "matter-fabric-backup.zip");
      } catch (error) {
        this.backupError = `Sicherung nicht möglich: ${error.message}`;
      }
    },

    // ---------------------------------------------------------------------
    // Live-Verbindung (Spec 8.3)
    // ---------------------------------------------------------------------

    connectLive() {
      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      const url = `${protocol}//${window.location.host}/api/live`;
      // Ein Browser-`WebSocket` kann keine eigenen Kopfzeilen tragen -
      // `Authorization` ist hier unmoeglich (Review-Fix Fix 1c,
      // 2026-09-03). Der einzige vom Browser beeinflussbare Kanal im
      // Handshake ist die Subprotokoll-Liste, die als
      // `Sec-WebSocket-Protocol: bearer, <Token>` auf die Leitung geht -
      // einem Query-Parameter vorzuziehen, weil der in Server-Logs,
      // Proxy-Logs und der Browser-History landet, ein Header nicht. Der
      // Server liest das Token dort aus und gibt NUR den Marker zurueck
      // (siehe `loxone.server.build_api_guard` und `api.live`). Ohne Token
      // wie bisher ganz ohne zweites Argument.
      const token = readStoredToken();
      let socket;
      try {
        socket = token
          ? new WebSocket(url, [WEBSOCKET_BEARER_MARKER, token])
          : new WebSocket(url);
      } catch {
        // Der Konstruktor wirft SYNCHRON, wenn ein Subprotokoll-Wert kein
        // gueltiges HTTP-Token ist - ein Token mit Leerzeichen, Komma oder
        // Nicht-ASCII also. Ohne dieses `catch` risse der Fehler `init()`
        // mitten heraus: die Statusanzeige bliebe fuer immer bei
        // "Verbinde...", und der Grund stuende nur in der
        // Entwicklerkonsole. Genau der Satz, den `.env.example` und beide
        // READMEs zum Zeichensatz sagen, gehoert stattdessen in die
        // Oberflaeche (Review-Fix Minor #1, 2026-09-03).
        this.authError =
          "Dieses Token lässt sich nicht über eine WebSocket-Verbindung übertragen – " +
          "es darf nur Zeichen ohne Leerzeichen, Komma und Umlaute enthalten. " +
          "Empfohlen: `openssl rand -hex 32`.";
        this.tokenEditing = true;
        return;
      }

      socket.addEventListener("open", () => {
        this.socketConnected = true;
        this.socketEverConnected = true;
        this.reconnectDelayMs = RECONNECT_DELAY_INITIAL_MS;
      });

      socket.addEventListener("message", (event) => {
        const message = JSON.parse(event.data);
        this.liveValues[message.key] = message.value;
      });

      // Sowohl ein sauberes Schliessen als auch ein Verbindungsfehler
      // sollen dieselbe Wiederverbindung ausloesen - eine Oberflaeche, die
      // eingefrorene Werte weiter als aktuell zeigt, ist schlimmer als
      // eine, die zugibt, dass sie die Verbindung verloren hat.
      // Die Pruefung `this.socket === socket` gehoert zu `restartLive()`
      // unten: eine bewusst verworfene Verbindung (Tokenwechsel) darf keine
      // Wiederverbindung planen, sonst laufen nach dem Neustart zwei
      // Verbindungen nebeneinander.
      socket.addEventListener("close", () => {
        if (this.socket === socket) {
          this.scheduleReconnect();
        }
      });
      socket.addEventListener("error", () => socket.close());

      this.socket = socket;
    },

    /**
     * Verwirft die laufende Live-Verbindung und baut sie sofort neu auf -
     * gebraucht nach einem Tokenwechsel, weil das Token im Handshake steckt
     * und eine bestehende Verbindung es nicht nachtraeglich aendern kann.
     */
    restartLive() {
      if (this.reconnectTimer !== null) {
        window.clearTimeout(this.reconnectTimer);
        this.reconnectTimer = null;
      }
      this.reconnectDelayMs = RECONNECT_DELAY_INITIAL_MS;
      const previous = this.socket;
      this.socket = null;
      this.socketConnected = false;
      if (previous) {
        previous.close();
      }
      this.connectLive();
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
        return "Live-Verbindung aktiv";
      }
      if (this.socketEverConnected) {
        return "Verbindung verloren – verbinde neu…";
      }
      if (this.initialConnectFailures >= INITIAL_CONNECT_FAILURES_BEFORE_GIVING_UP_ON_SILENCE) {
        return "Keine Verbindung zur Brücke möglich – verbinde weiter…";
      }
      return "Verbinde…";
    },

    // ---------------------------------------------------------------------
    // Formatierung
    // ---------------------------------------------------------------------

    formatTimestamp(isoTimestamp) {
      if (!isoTimestamp) {
        return "noch nie";
      }
      try {
        return new Date(isoTimestamp).toLocaleString("de-DE");
      } catch {
        return isoTimestamp;
      }
    },

    formatValue(value) {
      if (value === null || value === undefined) {
        return "-";
      }
      if (typeof value === "boolean") {
        return value ? "wahr" : "falsch";
      }
      return String(value);
    },
  };
}
