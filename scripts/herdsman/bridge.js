// loxmatter - connects Matter devices to a Loxone Miniserver.
// Copyright (C) 2026 Lucien Kerl
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU General Public License for more details.
//
// You should have received a copy of the GNU General Public License
// along with this program.  If not, see <https://www.gnu.org/licenses/>.

// The engine side of the bridge between zigbee-herdsman-converters and
// zigpy (design 2026-11-09, section 9). Bundled by build.mjs together with
// the library and run inside quickjs-ng by `loxmatter/zigbee/herdsman/engine.py`.
//
// What it does, in one sentence: it keeps zigbee-herdsman's OWN device and
// endpoint model alive with a fake adapter, so that every converter calls
// the real `Endpoint.read`, `write`, `command`, `configureReporting`, and
// every frame those produce is handed to Python as bytes, encoded by
// zigbee-herdsman's own ZCL codec. zigpy only sends and receives bytes.
//
// Every function it offers Python is a global named `lx_*`; the asynchronous
// ones answer through `__host_done`.
//
// The engine never calls into Python. quickjs-ng refuses a call into Python
// while a time limit is set, and the time limit is what keeps a converter
// that loops from holding the engine (design 2026-11-09, section 9.4). So
// every `__host_*` function below only queues its arguments in an outbox,
// and engine.py collects the outbox with `lx_outbox` after each call and
// acts on it on the event loop.

"use strict";

const outbox = [];
globalThis.lx_outbox = () => {
  if (outbox.length === 0) return "[]";
  const text = JSON.stringify(outbox);
  outbox.length = 0;
  return text;
};
const host =
  (name) =>
  (...args) => {
    outbox.push([name, ...args]);
  };
const __host_log = host("log");
const __host_timer = host("timer");
const __host_clear_timer = host("clear_timer");
const __host_send = host("send");
const __host_bind = host("bind");
const __host_save = host("save");
const __host_publish = host("publish");
const __host_done = host("done");

// Transaction sequence numbers: Python hands over the next one zigpy would
// use before every call (`lx_tsn`), so the library's requests and
// loxmatter's own rarely share a number on the same device.
let tsn = 0;
globalThis.lx_tsn = (next) => {
  tsn = (Number(next) - 1) & 0xff;
};
const __host_tsn = () => {
  tsn = (tsn + 1) & 0xff;
  return tsn;
};

const zhc = require("zigbee-herdsman-converters");
const zhcLogger = require("zigbee-herdsman-converters/lib/logger");
const Zcl = require("zigbee-herdsman/dist/zspec/zcl");
const herdsmanLogger = require("zigbee-herdsman/dist/utils/logger");
const ZclFrameConverter = require("zigbee-herdsman/dist/controller/helpers/zclFrameConverter");
const zclTsn = require("zigbee-herdsman/dist/controller/helpers/zclTransactionSequenceNumber").default;
const Entity = require("zigbee-herdsman/dist/controller/model/entity").default;
const DeviceModule = require("zigbee-herdsman/dist/controller/model/device");
const Endpoint = require("zigbee-herdsman/dist/controller/model/endpoint").default;

const Device = DeviceModule.default;
const VENDOR_MODULES = globalThis.__lxVendorModules;

// ---------------------------------------------------------------- logging --

function makeLogger(prefix) {
  const out = (level) => (message, namespace) => {
    const text = typeof message === "function" ? message() : message;
    __host_log(level, `${prefix}${namespace ? `${namespace}: ` : ""}${text}`);
  };
  return { debug: out("debug"), info: out("info"), warning: out("warning"), error: out("error") };
}
zhcLogger.setLogger(makeLogger(""));
herdsmanLogger.setLogger(makeLogger(""));

// ------------------------------------------------------------------ timers --
//
// The library's timers are the host's: Python schedules them on its event
// loop and calls `lx_fire` on the engine thread when one is due. Node's timer
// objects have `unref` and friends, and library code calls them.

let nextTimer = 1;
const timers = new Map();

function makeTimer(callback, ms, args, repeat) {
  const id = nextTimer++;
  timers.set(id, { callback, args, repeat, ms: Math.max(0, Number(ms) || 0) });
  __host_timer(id, Math.max(0, Number(ms) || 0));
  return {
    id,
    unref() {
      return this;
    },
    ref() {
      return this;
    },
    hasRef() {
      return false;
    },
    refresh() {
      return this;
    },
    [Symbol.toPrimitive]() {
      return id;
    },
  };
}

function clearTimer(handle) {
  if (handle === undefined || handle === null) return;
  const id = typeof handle === "object" ? handle.id : Number(handle);
  if (timers.delete(id)) __host_clear_timer(id);
}

globalThis.setTimeout = (callback, ms, ...args) => makeTimer(callback, ms, args, false);
globalThis.setInterval = (callback, ms, ...args) => makeTimer(callback, ms, args, true);
globalThis.clearTimeout = clearTimer;
globalThis.clearInterval = clearTimer;
globalThis.setImmediate = (callback, ...args) => makeTimer(callback, 0, args, false);
globalThis.clearImmediate = clearTimer;

globalThis.lx_fire = (id) => {
  const timer = timers.get(id);
  if (timer === undefined) return;
  if (timer.repeat) {
    __host_timer(id, timer.ms);
  } else {
    timers.delete(id);
  }
  try {
    timer.callback(...timer.args);
  } catch (error) {
    __host_log("error", `timer failed: ${error && error.stack ? error.stack : error}`);
  }
};

// ------------------------------------------------------- the vendor modules --
//
// The library loads a vendor module with a computed `require("./devices/x")`
// (index.js, `getDefinitions`). esbuild cannot follow that, so build.mjs
// registers every vendor module behind a lazy loader, and esbuild's own
// `__require` falls back to this global `require` for the computed name.

globalThis.require = (name) => {
  const match = /(?:^|\/)devices\/([^/]+?)(?:\.js)?$/.exec(name);
  const load = match ? VENDOR_MODULES[`${match[1]}.js`] : undefined;
  if (load === undefined) throw new Error(`module not bundled: ${name}`);
  return load();
};

// The transaction sequence numbers come from Python - see `lx_tsn` above.
zclTsn.next = () => __host_tsn();

// -------------------------------------------------------------- the adapter --

let nextCall = 1;
const pendingResponses = new Map(); // `${ieee}|${tsn}` -> {resolve, reject, timer}
const pendingSends = new Map(); // callId -> {resolve, reject}

function responseKey(ieee, tsn) {
  return `${ieee}|${tsn}`;
}

function sendFrame(ieee, endpoint, frame, sourceEndpoint, profileId) {
  const callId = nextCall++;
  return new Promise((resolve, reject) => {
    pendingSends.set(callId, { resolve, reject });
    __host_send(
      callId,
      ieee,
      endpoint,
      frame.cluster.ID,
      frame.toBuffer().toString("hex"),
      sourceEndpoint ?? 1,
      profileId ?? 0x0104,
    );
  });
}

globalThis.lx_sent = (callId, error) => {
  const pending = pendingSends.get(callId);
  if (pending === undefined) return;
  pendingSends.delete(callId);
  if (error) pending.reject(new Error(error));
  else pending.resolve();
};

const adapter = {
  hasZdoMessageOverhead: false,
  async sendZclFrameToEndpoint(
    ieee,
    _networkAddress,
    endpoint,
    frame,
    timeout,
    disableResponse,
    _disableRecovery,
    sourceEndpoint,
    profileId,
  ) {
    const tsn = frame.header.transactionSequenceNumber;
    let waiting;
    if (!disableResponse) {
      const key = responseKey(ieee, tsn);
      waiting = new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
          pendingResponses.delete(key);
          reject(new Error(`Timeout - ${ieee} - ${endpoint} - ${tsn} - ${frame.cluster.ID} - ${frame.command.ID}`));
        }, timeout || 10000);
        pendingResponses.set(key, { resolve, reject, timer });
      });
    }
    try {
      await sendFrame(ieee, endpoint, frame, sourceEndpoint, profileId);
    } catch (error) {
      if (waiting !== undefined) {
        const key = responseKey(ieee, tsn);
        const entry = pendingResponses.get(key);
        if (entry) clearTimeout(entry.timer);
        pendingResponses.delete(key);
      }
      throw error;
    }
    return waiting === undefined ? undefined : await waiting;
  },
  async sendZclFrameToAll() {
    throw new Error("broadcast frames are not supported by loxmatter");
  },
  async sendZclFrameToGroup() {
    throw new Error("group frames are not supported by loxmatter");
  },
  async sendZdo() {
    throw new Error("ZDO requests go through loxmatter, not the library");
  },
  waitFor() {
    return { promise: new Promise(() => {}), cancel() {} };
  },
};
Entity.injectAdapter(adapter);

// Binding is a ZDO exchange zigpy already knows how to do, so the library's
// bind goes to Python instead of through `sendZdo`.
const pendingBinds = new Map();
function hostBind(kind, endpoint, clusterKey, target) {
  const cluster = Zcl.Utils.getCluster(clusterKey, undefined, endpoint.getDevice().customClusters);
  const isCoordinator =
    target !== undefined && typeof target === "object" && typeof target.getDevice === "function" && target.getDevice().type === "Coordinator";
  if (!isCoordinator) {
    __host_log("info", `${endpoint.deviceIeeeAddress}: ${kind} of ${cluster.name} to anything but the coordinator is not supported`);
    return Promise.resolve();
  }
  const callId = nextCall++;
  return new Promise((resolve, reject) => {
    pendingBinds.set(callId, { resolve, reject });
    __host_bind(callId, kind, endpoint.deviceIeeeAddress, endpoint.ID, cluster.ID);
  }).then(() => {
    if (kind === "bind") endpoint.addBindingInternal(cluster, target);
  });
}
Endpoint.prototype.bind = function bind(clusterKey, target) {
  return hostBind("bind", this, clusterKey, target);
};
Endpoint.prototype.unbind = function unbind(clusterKey, target) {
  return hostBind("unbind", this, clusterKey, target);
};
globalThis.lx_bound = (callId, error) => {
  const pending = pendingBinds.get(callId);
  if (pending === undefined) return;
  pendingBinds.delete(callId);
  if (error) pending.reject(new Error(error));
  else pending.resolve();
};

// zigbee-herdsman persists a device through its database. The fake one hands
// the record to Python, which keeps it beside zigpy's own database.
Entity.injectDatabase({
  update(record) {
    if (record.type !== "Coordinator") __host_save(record.ieeeAddr, JSON.stringify(record));
  },
  insert() {},
  remove() {},
  has() {
    return true;
  },
  newID() {
    return 0;
  },
  *getEntriesIterator() {},
});

// ----------------------------------------------------------------- devices --

const bound = new Map(); // ieee -> {device, definition, state, options}
let coordinator;

function done(callId, value, error) {
  __host_done(callId, value === undefined ? "null" : JSON.stringify(value), error ? String(error) : "");
}

function run(callId, task) {
  Promise.resolve()
    .then(task)
    .then(
      (value) => done(callId, value, ""),
      (error) => done(callId, undefined, (error && error.message) || String(error) || "failed"),
    );
}

function exposesOf(entry) {
  const { definition, device } = entry;
  const list = typeof definition.exposes === "function" ? definition.exposes(device, entry.options) : definition.exposes;
  return list.map((expose) => expose.toJSON?.() ?? JSON.parse(JSON.stringify(expose)));
}

function endpointMap(entry) {
  try {
    return entry.definition.endpoint ? entry.definition.endpoint(entry.device) : undefined;
  } catch {
    return undefined;
  }
}

function publish(entry, payload) {
  if (!payload || typeof payload !== "object") return;
  const clean = {};
  for (const [key, value] of Object.entries(payload)) {
    if (value === undefined) continue;
    clean[key] = value;
  }
  if (Object.keys(clean).length === 0) return;
  Object.assign(entry.state, clean);
  __host_publish(entry.device.ieeeAddr, JSON.stringify(clean));
}

globalThis.lx_coordinator = (ieee) => {
  const record = {
    id: 0,
    type: "Coordinator",
    ieeeAddr: ieee,
    nwkAddr: 0,
    manufId: 0,
    epList: [1],
    endpoints: {
      1: { profId: 0x0104, epId: 1, devId: 5, inClusterList: [], outClusterList: [], clusters: {}, binds: [], configuredReportings: [], meta: {} },
    },
    interviewCompleted: true,
    interviewState: "SUCCESSFUL",
    meta: {},
  };
  coordinator = Device.fromDatabaseEntry(record);
};

// Attaches one device: builds zigbee-herdsman's device from its record, finds
// its definition the way zigbee2mqtt does, runs the definition's "start"
// event (custom clusters are added there) and answers with the definition's
// identity and its exposes for this very device.
globalThis.lx_attach = (callId, recordJson, stateJson) => {
  run(callId, async () => {
    const record = JSON.parse(recordJson);
    const device = Device.fromDatabaseEntry(record);
    Device.devices.set(device.ieeeAddr, device);
    const definition = await zhc.findByDevice(device);
    if (definition === undefined) {
      Device.devices.delete(device.ieeeAddr);
      return null;
    }
    const entry = { device, definition, state: JSON.parse(stateJson || "{}"), options: {} };
    bound.set(device.ieeeAddr, entry);
    const event = {
      type: "start",
      data: { device, deviceExposesChanged() {}, state: entry.state, options: entry.options },
    };
    await zhc.onEvent(event);
    if (definition.onEvent) await definition.onEvent(event);
    return {
      model: definition.model,
      vendor: definition.vendor,
      description: definition.description,
      exposes: exposesOf(entry),
      endpoints: endpointMap(entry) ?? null,
    };
  });
};

globalThis.lx_detach = (ieee) => {
  bound.delete(ieee);
  Device.devices.delete(ieee);
};

// One frame the device sent. Mirrors zigbee-herdsman's
// `Controller.onZclPayload` (message type, data, the attribute cache) and
// zigbee2mqtt's receive path (which converters, publish). A frame that answers
// a request of the library resolves that request as well.
globalThis.lx_frame = (callId, ieee, endpointId, clusterId, hex, linkquality, destinationEndpoint) => {
  run(callId, async () => {
    const entry = bound.get(ieee);
    const data = Buffer.from(hex, "hex");
    const header = Zcl.Header.fromBuffer(data);
    if (header === undefined) return null;
    const pending = pendingResponses.get(responseKey(ieee, header.transactionSequenceNumber));
    if (pending !== undefined && header.frameControl.direction === Zcl.Direction.SERVER_TO_CLIENT) {
      clearTimeout(pending.timer);
      pendingResponses.delete(responseKey(ieee, header.transactionSequenceNumber));
      pending.resolve({
        clusterID: clusterId,
        header,
        data,
        address: ieee,
        endpoint: endpointId,
        linkquality,
        groupID: 0,
        wasBroadcast: false,
        destinationEndpoint,
      });
    }
    if (entry === undefined) return null;
    const { device, definition } = entry;
    let frame;
    try {
      frame = Zcl.Frame.fromBuffer(clusterId, header, data, device.customClusters);
    } catch (error) {
      __host_log("debug", `${ieee}: frame on cluster ${clusterId} not parsed: ${error}`);
    }
    device.updateLastSeen();
    if (frame?.cluster.name !== "genPollCtrl") device.implicitCheckin();
    device.linkquality = linkquality;
    let endpoint = device.getEndpoint(endpointId);
    if (endpoint === undefined) endpoint = device.createEndpoint(endpointId);

    let type;
    let payload = {};
    let clusterName;
    const meta = { rawData: data };
    if (frame) {
      clusterName = frame.cluster.name;
      meta.zclTransactionSequenceNumber = frame.header.transactionSequenceNumber;
      meta.manufacturerCode = frame.header.manufacturerCode;
      meta.frameControl = frame.header.frameControl;
      if (frame.header.isGlobal) {
        switch (frame.command.name) {
          case "report":
            type = "attributeReport";
            payload = ZclFrameConverter.attributeKeyValue(frame, device.manufacturerID, device.customClusters);
            break;
          case "read":
            type = "read";
            payload = ZclFrameConverter.attributeList(frame, device.manufacturerID, device.customClusters);
            if (device.customReadResponse?.(frame, endpoint)) type = undefined;
            break;
          case "write":
            type = "write";
            payload = ZclFrameConverter.attributeKeyValue(frame, device.manufacturerID, device.customClusters);
            break;
          case "readRsp":
            type = "readResponse";
            payload = ZclFrameConverter.attributeKeyValue(frame, device.manufacturerID, device.customClusters);
            break;
        }
        if (type === "readResponse" || type === "attributeReport") {
          if (frame.cluster.name === "genBasic") device.updateGenBasic(payload);
          endpoint.saveClusterAttributeKeyValue(frame.cluster.ID, payload);
        }
      } else if (frame.header.isSpecific) {
        type = `command${frame.command.name.charAt(0).toUpperCase()}${frame.command.name.slice(1)}`;
        payload = frame.payload;
      }
    } else {
      type = "raw";
      payload = data;
      const name = Zcl.Utils.getCluster(clusterId, device.manufacturerID, device.customClusters).name;
      clusterName = Number.isNaN(Number(name)) ? name : Number(name);
    }
    if (!type || !payload) return null;

    const message = { type, device, endpoint, data: payload, linkquality, groupID: 0, cluster: clusterName, meta };
    const converters = definition.fromZigbee.filter((converter) => {
      const types = Array.isArray(converter.type) ? converter.type : [converter.type];
      return converter.cluster === clusterName && types.includes(type);
    });
    const result = {};
    const later = (value) => publish(entry, value);
    for (const converter of converters) {
      try {
        const converted = await converter.convert(definition, message, later, entry.options, {
          device,
          logger: zhcLogger.logger,
          state: entry.state,
        });
        if (converted) Object.assign(result, converted);
      } catch (error) {
        __host_log("warning", `${ieee}: converter for ${clusterName}/${type} failed: ${error && error.stack ? error.stack : error}`);
      }
    }
    if (Object.keys(result).length > 0) {
      zhc.postProcessConvertedFromZigbeeMessage(definition, result, entry.options, device);
      publish(entry, result);
    }
    return null;
  });
};

function targetEndpoint(entry, endpointName) {
  const map = endpointMap(entry);
  const { device } = entry;
  if (map && endpointName && map[endpointName] !== undefined) {
    return device.getEndpoint(map[endpointName]) ?? device.endpoints[0];
  }
  if (map && map.default !== undefined) return device.getEndpoint(map.default) ?? device.endpoints[0];
  return device.endpoints[0];
}

function converterFor(entry, key, endpointName) {
  return entry.definition.toZigbee.find(
    (converter) => (!converter.key || converter.key.includes(key)) && (!converter.endpoints || converter.endpoints.includes(endpointName)),
  );
}

function setMeta(entry, key, value, endpointName) {
  return {
    endpoint_name: endpointName,
    options: entry.options,
    message: { [key]: value },
    logger: zhcLogger.logger,
    device: entry.device,
    state: entry.state,
    membersState: undefined,
    mapped: entry.definition,
    publish: (payload) => publish(entry, payload),
  };
}

// Sets one property the way zigbee2mqtt's publish path does: the first
// toZigbee converter that names the key, on the endpoint the definition maps
// the endpoint name to.
globalThis.lx_set = (callId, ieee, key, valueJson, endpointName) => {
  run(callId, async () => {
    const entry = bound.get(ieee);
    if (entry === undefined) throw new Error(`device ${ieee} is not attached`);
    const value = JSON.parse(valueJson);
    const converter = converterFor(entry, key, endpointName || undefined);
    if (converter === undefined || converter.convertSet === undefined) throw new Error(`no converter sets ${key}`);
    const meta = setMeta(entry, key, value, endpointName || undefined);
    const result = await converter.convertSet(targetEndpoint(entry, endpointName), key, value, meta);
    if (result && result.state) publish(entry, result.state);
    return true;
  });
};

globalThis.lx_get = (callId, ieee, key, endpointName) => {
  run(callId, async () => {
    const entry = bound.get(ieee);
    if (entry === undefined) throw new Error(`device ${ieee} is not attached`);
    const converter = converterFor(entry, key, endpointName || undefined);
    if (converter === undefined || converter.convertGet === undefined) return false;
    await converter.convertGet(targetEndpoint(entry, endpointName), key, setMeta(entry, key, undefined, endpointName || undefined));
    return true;
  });
};

// The definition's `configure`, against the coordinator's endpoint 1.
globalThis.lx_configure = (callId, ieee) => {
  run(callId, async () => {
    const entry = bound.get(ieee);
    if (entry === undefined) throw new Error(`device ${ieee} is not attached`);
    if (!entry.definition.configure) return false;
    if (coordinator === undefined) throw new Error("the coordinator is not known");
    await entry.definition.configure(entry.device, coordinator.getEndpoint(1), entry.definition);
    entry.device.save();
    return true;
  });
};
