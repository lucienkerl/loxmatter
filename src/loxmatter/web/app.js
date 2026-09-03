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

/**
 * Fehler eines Aufrufs ohne gueltige Sitzung - eigene Klasse, damit die
 * Oberflaeche diesen Fall von jedem anderen Fehlschlag unterscheiden kann,
 * ohne auf einen Meldungstext zu pruefen.
 */
class UnauthorizedError extends Error {
  constructor() {
    super("Die Sitzung ist abgelaufen – bitte erneut anmelden.");
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
    throw new Error("Die Brücke ist nicht erreichbar – sie läuft möglicherweise nicht.");
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
    throw new Error(await readErrorDetail(response));
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

    // Ruft Alpine von sich aus genau EINMAL auf, sobald `x-data="app()"`
    // ausgewertet ist. `index.html` traegt deshalb bewusst kein
    // `x-init="init()"` (Review-Fix Fix 2, 2026-09-03) - das rief die
    // Methode ein zweites Mal auf, und mit ihr (nach einer angemeldeten
    // Sitzung) `startApp()`: jeder offene Tab hielt dann zwei
    // Live-Verbindungen, von denen nur die zuletzt geoeffnete in
    // `this.socket` landete. Die andere blieb unsichtbar und lief bis zum
    // Schliessen des Tabs weiter.
    async init() {
      await this.loadAuthInfo();
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
      } catch (error) {
        this.authError = error.message;
      } finally {
        this.authReady = true;
      }
    },

    /**
     * Alles, was eine angemeldete Sitzung voraussetzt. Getrennt von `init`,
     * weil es nach dem Login ein zweites Mal laufen muss - dann ohne
     * Neuladen der Seite.
     */
    async startApp() {
      await this.loadDevices();
      this.connectLive();
    },

    async submitSetup() {
      if (this.passwordDraft !== this.passwordRepeatDraft) {
        this.authError = "Die beiden Eingaben stimmen nicht überein.";
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
    // Baum steht in der Signale-Ansicht. Ohne diese Deckelung waere sie bei
    // einem Geraet mit hundert exportierbaren Signalen (kein Einzelfall,
    // siehe IKEA-GRILLPLATS-Testvorlage) keine Kurzliste mehr, sondern
    // derselbe volle Baum ein zweites Mal.
    //
    // Frueher hiessen diese drei Helfer `KEY_SIGNAL_LIMIT`/`keySignalsFor`/
    // `remainingKeySignalCount` und die Ueberschrift daneben "Wichtigste
    // Werte" (Review-Fix Fix 9, 2026-09-03). Beides versprach eine
    // Rangfolge, die es nicht gibt: `GET /api/devices/{id}/signals` liefert
    // die Zeilen in `Store.signals`-Reihenfolge (ORDER BY endpoint,
    // cluster_id, element_id, kind), ein `slice(0, 6)` darauf ergibt "die
    // sechs mit der kleinsten Cluster-Nummer" - fuer die Steckdose aus der
    // Testvorlage NetworkCommissioning und BasicInformation, nicht Ein/Aus
    // und nicht die Leistung. Eine echte Rangfolge braeuchte eine
    // Bewertung je Cluster, also eine weitere Tabelle; die gibt es nicht,
    // und sie zu erfinden waere schlechter als ein ehrlicher Name.
    SIGNAL_PREVIEW_LIMIT: 6,

    exportableSignalsFor(deviceId) {
      const signals = this.signalsByDevice[deviceId];
      return signals ? signals.filter((signal) => signal.exportable) : [];
    },

    firstSignalsFor(deviceId) {
      return this.exportableSignalsFor(deviceId).slice(0, this.SIGNAL_PREVIEW_LIMIT);
    },

    remainingSignalCount(deviceId) {
      return Math.max(0, this.exportableSignalsFor(deviceId).length - this.SIGNAL_PREVIEW_LIMIT);
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
      const confirmed = window.confirm(
        `Gerät "${device.label}" wirklich entfernen? Das kann nicht rückgängig gemacht werden.\n\n` +
          "In Loxone bleiben danach verwaist:\n" +
          `• alle virtuellen Ein- und Ausgänge mit dem Schlüssel-Präfix "d${device.id}_"\n` +
          `• die importierten Vorlagen "VIU_d${device.id}_….xml" und "VO_d${device.id}_….xml"\n\n` +
          "Diese in Loxone Config von Hand löschen.",
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
        // Der Satz zur Subscription ist kein Schmuck (Review-Fix Fix 3,
        // 2026-09-03, siehe Spec 12.3): `BridgeMatterClient.subscribe()`
        // laeuft genau einmal beim Start der Bruecke und meldet nur die
        // damals bekannten (Node, Pfad)-Paare an. Ein gerade eingelerntes
        // Geraet geht ueber das NODE_ADDED-Ereignis sofort auf "online"
        // und erscheint gruen - bekommt aber bis zum naechsten Neustart
        // keinen einzigen Attributwert. Ohne diesen Hinweis sieht der
        // Anwender ein gruenes Geraet, dessen Signale alle auf "-" stehen,
        // und sucht den Fehler bei sich.
        this.commissionMessage =
          `${device.label} wurde eingelernt. Live-Werte erscheinen erst nach einem ` +
          "Neustart der Brücke – bis dahin zeigt das Gerät zwar „online“, aber jedes " +
          "Signal „-“ (bekannte Grenze, Spec 12.3). Der Export der Vorlagen " +
          "funktioniert davon unabhängig schon jetzt.";
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
        bridge_ip: this.exportBridgeIp.trim(),
        port: String(this.exportPort),
        listen: String(this.exportListenPort),
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
      if (!this.exportBridgeIp.trim()) {
        this.exportError = "Bitte zuerst die IP der Bruecke (aus Sicht des Miniservers) eingeben.";
        return;
      }
      try {
        await this.download(this.downloadUrl(), "loxmatter-export.zip");
      } catch (error) {
        this.exportError = `Download fehlgeschlagen: ${error.message}`;
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

    // Ebenfalls kein `<a href>` mehr (siehe `downloadExport`): ein
    // Fehler (z. B. 503 ohne eingehaengtes Datenverzeichnis) soll als
    // lesbare Meldung erscheinen, statt eine heruntergeladene Datei zu
    // ergeben, die in Wahrheit eine Fehlermeldung ist.
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
      await this.loadAuthInfo();
      if (!this.authenticated) {
        this.authError = "Die Sitzung ist abgelaufen – bitte erneut anmelden.";
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
