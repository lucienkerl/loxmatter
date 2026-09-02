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
  const response = await fetch(path, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) {
    throw new Error(await readErrorDetail(response));
  }
  if (response.status === 204) {
    return null;
  }
  return response.json();
}

function app() {
  return {
    // --- Ansicht ---------------------------------------------------------
    view: "devices",

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

    // --- Live-Verbindung (Spec 8.3) --------------------------------------
    liveValues: {},
    socket: null,
    socketConnected: false,
    socketEverConnected: false,
    reconnectDelayMs: RECONNECT_DELAY_INITIAL_MS,
    reconnectTimer: null,

    async init() {
      await this.loadDevices();
      this.connectLive();
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
        this.devices = await requestJson("GET", "/api/devices");
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
        this.controlsByDevice[deviceId] = await requestJson(
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
        const updated = await requestJson("PATCH", `/api/devices/${device.id}`, { label });
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
        await requestJson("DELETE", `/api/devices/${device.id}`);
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
        await requestJson("POST", `/api/commands/${command.key}`, { value: String(value) });
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
        const device = await requestJson("POST", "/api/devices/commission", body);
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
        this.signalsByDevice[deviceId] = await requestJson(
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
        const updated = await requestJson("PATCH", `/api/signals/${signal.key}`, { title });
        Object.assign(signal, updated);
      } catch (error) {
        this.signalsError = `Titel konnte nicht gespeichert werden: ${error.message}`;
      }
    },

    async toggleExported(signal) {
      try {
        const updated = await requestJson("PATCH", `/api/signals/${signal.key}`, {
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
        await requestJson("POST", `/api/signals/${signal.key}/write`, { value: String(value) });
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
        const rows = await requestJson("GET", "/api/export/status");
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
        this.exportPreview = await requestJson("GET", `/api/export/preview?${params}`);
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

    // Ohne diese Pruefung wuerde ein Klick auf "ZIP herunterladen" bei
    // leerer Miniserver-IP die ganze Seite durch die rohe 422-Fehlerantwort
    // des Backends ersetzen (Pflichtparameter `bridge_ip`, siehe
    // `api/export.py`) - fuer ein Diagnosewerkzeug, das gerade in
    // schwierigen Momenten benutzt wird, ist das eine schlechtere Antwort
    // als eine Fehlermeldung an derselben Stelle, an der schon die
    // Vorschau ihre Fehler zeigt.
    onDownloadClick(event) {
      if (!this.exportBridgeIp.trim()) {
        event.preventDefault();
        this.exportError = "Bitte zuerst die IP der Bruecke (aus Sicht des Miniservers) eingeben.";
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
          requestJson("GET", "/api/diagnostics/system"),
          requestJson("GET", "/api/diagnostics/datagrams"),
          requestJson("GET", "/api/diagnostics/commands"),
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

    // ---------------------------------------------------------------------
    // Live-Verbindung (Spec 8.3)
    // ---------------------------------------------------------------------

    connectLive() {
      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      const socket = new WebSocket(`${protocol}//${window.location.host}/api/live`);

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
      socket.addEventListener("close", () => this.scheduleReconnect());
      socket.addEventListener("error", () => socket.close());

      this.socket = socket;
    },

    scheduleReconnect() {
      this.socketConnected = false;
      if (this.reconnectTimer !== null) {
        return;
      }
      this.reconnectTimer = window.setTimeout(() => {
        this.reconnectTimer = null;
        this.connectLive();
      }, this.reconnectDelayMs);
      this.reconnectDelayMs = Math.min(this.reconnectDelayMs * 2, RECONNECT_DELAY_MAX_MS);
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
