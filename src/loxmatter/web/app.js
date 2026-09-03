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

// Der Heartbeat-Schluessel der Bruecke (siehe loxone/runtime.py,
// HEARTBEAT_KEY). Er gehoert zu keinem Geraet und kommt auch dann, wenn
// sich an keinem etwas aendert - damit ist er das einzige verlaessliche
// Lebenszeichen, das diese Oberflaeche hat.
const HEARTBEAT_KEY = "bridge_alive";

// Laufende Nummer fuer Kurzmeldungen. Modulweit statt im Zustand, weil
// sie nur die Meldungen auseinanderhalten muss und niemanden sonst
// interessiert.
let toastCounter = 0;

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
    // Methode ein zweites Mal auf, und mit ihr `connectLive()`: jeder
    // offene Tab hielt zwei Live-Verbindungen, von denen nur die zuletzt
    // geoeffnete in `this.socket` landete. Die andere war damit auch fuer
    // `restartLive()` nicht mehr erreichbar (das schliesst `this.socket`)
    // und lief bis zum Schliessen des Tabs weiter.
    async init() {
      window.setInterval(() => {
        this.nowTick = Date.now();
      }, 1000);
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
      this.deviceActionError = null;
      this.signalsError = null;
      // Und die geraeteweisen Zwischenspeicher (2026-09-03). Bei einer 401
      // legt `loadControls`/`loadSignals` gar keinen Eintrag an - der
      // Zwischenspeicher bleibt leer, und ein leerer Eintrag ist von
      // "dieses Geraet hat keine Befehle" nicht zu unterscheiden. Ohne
      // dieses Leeren zeigte die Bedienung nach dem Eintragen des Tokens
      // dauerhaft "Keine bekannten Ausgangsbefehle", obwohl es drei gibt,
      // und nur ein Neuladen der Seite half. Genau die Sorte
      // stillschweigend falscher Zustand, die Spec 8.1 ausschliessen will.
      this.controlsByDevice = {};
      this.signalsByDevice = {};
      this.restartLive();
      await this.loadDevices();
      await this.selectView(this.view);
      // Ein aufgeklapptes Geraet haengt an keiner Ansicht - `selectView`
      // holt seine Bedienelemente nicht mit.
      if (this.expandedDeviceId !== null) {
        await Promise.all([
          this.loadControls(this.expandedDeviceId),
          this.loadSignals(this.expandedDeviceId),
        ]);
      }
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
        { key: "functional", title: "Funktional", collapsible: false, signals: this.functionalSignalsFor(deviceId) },
        { key: "expert", title: "Experte", collapsible: true, signals: this.expertSignalsFor(deviceId) },
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
      this.commandBusyKey = command.key;
      const value = command.takes_value ? this.commandValueDrafts[command.key] ?? "" : "1";
      try {
        await this.request("POST", `/api/commands/${command.key}`, { value: String(value) });
        this.showToast(`"${command.slug}" wurde an ${device.label} gesendet.`);
      } catch (error) {
        this.showToast(`"${command.slug}" ist fehlgeschlagen: ${error.message}`, true);
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
        return `vor ${seconds} s`;
      }
      const minutes = Math.round(seconds / 60);
      if (minutes < 60) {
        return `vor ${minutes} min`;
      }
      return `vor ${Math.round(minutes / 60)} h`;
    },

    /** Wann zuletzt IRGENDETWAS ueber die Leitung kam - der Heartbeat
     * eingeschlossen. Das ist die Angabe, die "nichts aendert sich" von
     * "nichts kommt an" unterscheidet. */
    heartbeatText() {
      return this.sinceText(this.lastHeartbeatAt);
    },

    /** Wann dieses eine Signal zuletzt einen Wert lieferte. */
    signalSeenText(signal) {
      return this.sinceText(this.liveSeenAt[signal.key]);
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

    // Frueher ein gewoehnlicher `<a href>`: der kann keinen
    // `Authorization`-Header tragen und wuerde bei gesetztem Token die
    // ganze Seite durch die rohe 401-Antwort ersetzen (Review-Fix Fix 1a,
    // 2026-09-03). Deshalb ueber `download()`, das denselben Header setzt
    // wie jeder andere Aufruf.
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
