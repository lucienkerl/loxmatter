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

// Reads zigbee-herdsman-converters in Node and writes what is data in it as
// JSON: the catalogue (design 2026-11-09, section 5) and the golden table
// the Python matcher is tested against (section 6).
//
// The library's building blocks are functions that RETURN converters, so
// the arguments a definition passed to them are gone once the definition
// exists. Each exported building block is therefore wrapped before any
// vendor module is loaded, and the wrapper remembers its arguments on the
// object it returns. The Tuya value converters are made inside lib/tuya.js
// itself while it loads, so that one file is wrapped from the inside, by
// inserting one line before its table of named converters. If the anchor
// line ever moves, the build fails here instead of silently capturing less.
//
// Usage: node catalogue.cjs <herdsman dir> <out dir>

"use strict";

const fs = require("node:fs");
const path = require("node:path");
const Module = require("node:module");

const [herdsmanDir, outDir] = process.argv.slice(2);
const requireFrom = Module.createRequire(path.join(path.resolve(herdsmanDir), "package.json"));
const libraryDir = path.dirname(requireFrom.resolve("zigbee-herdsman-converters/package.json"));
const dist = path.join(libraryDir, "dist");
const tuyaFile = path.join(dist, "lib", "tuya.js");

const CAPTURED = Symbol.for("loxmatter.captured");

function tag(object, kind, args) {
  // The first capture wins: a building block that calls another exported one
  // returns the inner result, and the outer call is the one the definition made
  // - but the inner one is tagged first, so the outer one is set aside when it
  // returns the very same object.
  if (object && typeof object === "object" && !Object.prototype.hasOwnProperty.call(object, CAPTURED)) {
    try {
      Object.defineProperty(object, CAPTURED, { value: { kind, args }, enumerable: false, configurable: true });
    } catch {
      // A frozen object stays untagged and reads as code.
    }
  }
  return object;
}

function wrapAll(target, prefix) {
  for (const [name, original] of Object.entries(target)) {
    if (typeof original !== "function") continue;
    target[name] = function wrapped(...args) {
      return tag(original.apply(this, args), `${prefix}${name}`, args);
    };
  }
}

// lib/tuya.js, wrapped from the inside.
const ANCHOR = "exports.valueConverter = {";
const compile = Module.prototype._compile;
Module.prototype._compile = function patched(content, filename) {
  if (filename === tuyaFile) {
    if (!content.includes(ANCHOR)) throw new Error(`lib/tuya.js no longer contains "${ANCHOR}"`);
    content = content.replace(ANCHOR, `globalThis.__lxWrapBasic(exports.valueConverterBasic);\n${ANCHOR}`);
  }
  return compile.call(this, content, filename);
};
globalThis.__lxWrapBasic = (basic) => wrapAll(basic, "basic.");

const zhc = requireFrom("zigbee-herdsman-converters");
const quiet = { debug() {}, info() {}, warning() {}, error() {} };
requireFrom("zigbee-herdsman-converters/lib/logger").setLogger(quiet);
const modernExtend = requireFrom("zigbee-herdsman-converters/lib/modernExtend");
const tuya = requireFrom("zigbee-herdsman-converters/lib/tuya");
const Zcl = requireFrom("zigbee-herdsman/dist/zspec/zcl");
wrapAll(modernExtend, "m.");
wrapAll(tuya.modernExtend, "tuya.");

const namedConverters = new Map();
for (const [name, converter] of Object.entries(tuya.valueConverter)) {
  if (converter && typeof converter === "object") namedConverters.set(converter, name);
}

// Building blocks whose result this design reads as data, or that describe
// clusters loxmatter already reads natively (section 5). Anything else
// makes a definition an engine definition.
const DATA_BLOCKS = new Set([
  "m.numeric",
  "m.enumLookup",
  "m.binary",
  "m.deviceAddCustomCluster",
  "m.deviceEndpoints",
  "m.identify",
  "m.battery",
  "m.temperature",
  "m.humidity",
  "m.pressure",
  "m.illuminance",
  "m.occupancy",
  "m.iasZoneAlarm",
  "m.onOff",
  "m.light",
  "m.electricityMeter",
  "m.co2",
  "m.soilMoisture",
  "m.pm25",
  "m.deviceTemperature",
  "m.forcePowerSource",
  "m.quirkCheckinInterval",
  "m.quirkAddEndpointCluster",
  "m.bindCluster",
  "m.forceDeviceType",
  "m.skipDefaultResponse",
  "tuya.tuyaBase",
  "tuya.tuyaMagicPacket",
]);

// The library's own Tuya plumbing that a definition lists by hand; the
// catalogue does the same job from the data-point table.
const DATA_CONVERTERS = new Set(
  [tuya.fz.datapoints, tuya.tz.datapoints, tuya.fz.gateway_connection_status].filter((c) => c !== undefined),
);
const DATA_CONFIGURES = new Set([tuya.configureMagicPacket, tuya.configureQuery, tuya.configureBindBasic]);

function plain(value) {
  if (value === undefined) return null;
  // A fingerprint's `ieeeAddr` is a regular expression, which JSON would
  // write as `{}` - and `{}` would then match every device.
  return JSON.parse(JSON.stringify(value, (_key, v) => (v instanceof RegExp ? { regexp: v.source } : v)));
}

function hasFunction(value, depth = 0) {
  if (typeof value === "function") return true;
  if (depth > 4 || value === null || typeof value !== "object") return false;
  return Object.values(value).some((v) => hasFunction(v, depth + 1));
}

function tuyaValue(value) {
  if (value instanceof tuya.Enum) return { enum: value.valueOf() };
  if (value instanceof tuya.Bitmap) return { bitmap: value.valueOf() };
  if (typeof value === "number" || typeof value === "boolean" || typeof value === "string") return value;
  return undefined;
}

// A Tuya value converter as data, or null when it is code.
function dpConverter(converter) {
  if (!converter) return null;
  const named = namedConverters.get(converter);
  if (["trueFalseInvert", "fault", "localTemperatureCalibration", "localTemperatureCalibration_256"].includes(named)) {
    return { kind: named };
  }
  const captured = converter[CAPTURED];
  if (!captured) return null;
  const { kind, args } = captured;
  switch (kind) {
    case "basic.raw":
      return { kind: "raw" };
    case "basic.divideBy":
    case "basic.multiplyBy":
    case "basic.divideByFromOnly":
      return typeof args[0] === "number" ? { kind: kind.slice(6), factor: args[0] } : null;
    case "basic.divideByWithLimits":
      return args.every((a) => typeof a === "number") ? { kind: "divideByWithLimits", factor: args[0], min: args[1], max: args[2] } : null;
    case "basic.scale":
      return args.every((a) => typeof a === "number") ? { kind: "scale", range: args } : null;
    case "basic.twosComplementWithBitsizeScaled":
      return { kind: "twosComplement", bits: args[0], scale: args[1] ?? 1 };
    case "basic.trueFalse": {
      const value = tuyaValue(args[0]);
      return value === undefined ? null : { kind: "trueFalse", value };
    }
    case "basic.lookup": {
      if (typeof args[0] !== "object" || args[0] === null) return null;
      const entries = [];
      for (const [label, raw] of Object.entries(args[0])) {
        const value = tuyaValue(raw);
        if (value === undefined) return null;
        entries.push([label, value]);
      }
      const fallback = args[1] === undefined ? null : tuyaValue(args[1]);
      return { kind: "lookup", map: entries, fallback: fallback ?? null };
    }
    default:
      return null;
  }
}

function resolveAttribute(clusterKey, attributeKey, manufacturerCode, customClusters) {
  const cluster = Zcl.Utils.getCluster(clusterKey, manufacturerCode, customClusters);
  if (typeof attributeKey === "object") {
    return { cluster: cluster.ID, attribute: attributeKey.ID, type: attributeKey.type, manufacturer_code: manufacturerCode ?? null };
  }
  // A manufacturer code in the command options is the code the frame is sent
  // with; the attribute itself may be a standard one, which the lookup with
  // that code does not find.
  const attribute =
    Zcl.Utils.getClusterAttribute(cluster, attributeKey, manufacturerCode) ?? Zcl.Utils.getClusterAttribute(cluster, attributeKey, undefined);
  if (!attribute) throw new Error(`attribute ${attributeKey} not in ${cluster.name}`);
  return {
    cluster: cluster.ID,
    attribute: attribute.ID,
    type: attribute.type,
    manufacturer_code: manufacturerCode ?? attribute.manufacturerCode ?? null,
  };
}

const ACCESS = { STATE: 1, SET: 2, GET: 4, STATE_SET: 3, STATE_GET: 5, ALL: 7 };

function flatExposes(list) {
  const result = [];
  for (const expose of list) {
    const json = plain(expose);
    if (["light", "switch", "lock", "cover", "climate", "fan"].includes(json.type)) {
      for (const feature of json.features ?? []) {
        result.push({ ...exposeFields(feature), parent: json.type });
      }
    } else if (["numeric", "binary", "enum", "text"].includes(json.type)) {
      result.push(exposeFields(json));
    }
  }
  return result;
}

function exposeFields(json) {
  const out = { name: json.name, property: json.property, type: json.type, access: json.access ?? 1 };
  for (const key of ["endpoint", "unit", "value_min", "value_max", "value_step", "values", "value_on", "value_off", "label", "category"]) {
    if (json[key] !== undefined) out[key] = json[key];
  }
  return out;
}

const dummyDevice = { isDummyDevice: true, endpoints: [], getEndpoint: () => undefined };

function describe(file, index, definition) {
  const reasons = [];
  const prepared = zhc.prepareDefinition(definition);
  let exposes = [];
  try {
    exposes = flatExposes(typeof prepared.exposes === "function" ? prepared.exposes(dummyDevice, {}) : prepared.exposes);
  } catch (error) {
    reasons.push(`exposes: ${error.message}`);
  }
  let endpoints = null;
  try {
    endpoints = prepared.endpoint ? plain(prepared.endpoint(dummyDevice)) : null;
  } catch {
    endpoints = null;
  }

  if ((definition.fromZigbee ?? []).some((c) => !DATA_CONVERTERS.has(c))) reasons.push("own fromZigbee");
  if ((definition.toZigbee ?? []).some((c) => !DATA_CONVERTERS.has(c))) reasons.push("own toZigbee");
  if (typeof definition.configure === "function" && !DATA_CONFIGURES.has(definition.configure)) reasons.push("own configure");
  if (typeof definition.onEvent === "function") reasons.push("own onEvent");

  const customClusters = {};
  const attributes = [];
  let tuyaBase = null;
  for (const extend of definition.extend ?? []) {
    const captured = extend?.[CAPTURED];
    if (!captured) {
      reasons.push("vendor building block");
      continue;
    }
    if (!DATA_BLOCKS.has(captured.kind)) {
      reasons.push(captured.kind);
      continue;
    }
    if (captured.kind === "m.deviceAddCustomCluster") customClusters[captured.args[0]] = captured.args[1];
    if (captured.kind === "tuya.tuyaBase") {
      const args = captured.args[0] ?? {};
      if (hasFunction(args)) reasons.push("tuya.tuyaBase with code");
      tuyaBase = {
        query_on_configure: Boolean(args.queryOnConfigure),
        bind_basic_on_configure: Boolean(args.bindBasicOnConfigure),
        time_start: args.timeStart ?? "off",
      };
    }
  }
  if (definition.configure === tuya.configureMagicPacket || definition.configure === tuya.configureQuery) {
    tuyaBase = tuyaBase ?? { query_on_configure: false, bind_basic_on_configure: false, time_start: "off" };
    if (definition.configure === tuya.configureQuery) tuyaBase.query_on_configure = true;
  }

  for (const extend of definition.extend ?? []) {
    const captured = extend?.[CAPTURED];
    if (!captured || !["m.numeric", "m.enumLookup", "m.binary"].includes(captured.kind)) continue;
    const args = captured.args[0];
    const kind = captured.kind.slice(2);
    if (args.fzConvert || typeof args.scale === "function" || Array.isArray(args.name)) {
      reasons.push(`${captured.kind} with code`);
      continue;
    }
    const names = args.endpointNames ?? (args.endpointName ? [args.endpointName] : [null]);
    let resolved;
    try {
      resolved = resolveAttribute(args.cluster, args.attribute, args.zigbeeCommandOptions?.manufacturerCode, customClusters);
    } catch (error) {
      reasons.push(`${captured.kind}: ${error.message}`);
      continue;
    }
    for (const endpointName of names) {
      if (endpointName !== null && (endpoints === null || endpoints[endpointName] === undefined)) {
        reasons.push(`${captured.kind}: endpoint ${endpointName} unknown`);
        continue;
      }
      attributes.push({
        kind,
        name: args.name,
        property: endpointName === null ? args.name : `${args.name}_${endpointName}`,
        endpoint: endpointName === null ? null : endpoints[endpointName],
        ...resolved,
        access: ACCESS[args.access ?? "ALL"],
        scale: typeof args.scale === "number" ? args.scale : null,
        precision: typeof args.precision === "number" ? args.precision : null,
        lookup: kind === "enumLookup" ? Object.entries(args.lookup) : null,
        value_on: kind === "binary" ? args.valueOn : null,
        value_off: kind === "binary" ? args.valueOff : null,
        reporting: args.reporting ? plain(args.reporting) : null,
      });
    }
  }

  const datapoints = [];
  for (const entry of prepared.meta?.tuyaDatapoints ?? []) {
    const [dp, property, converter, options] = entry;
    const descriptor = dpConverter(converter);
    if (!property || descriptor === null || (options && hasFunction(options))) {
      reasons.push(`datapoint ${dp} with code`);
      continue;
    }
    datapoints.push({ dp, property, converter: descriptor, optimistic: options?.optimistic !== false });
  }
  if (datapoints.length > 0 && tuyaBase === null) {
    tuyaBase = { query_on_configure: false, bind_basic_on_configure: false, time_start: "off" };
  }

  return {
    id: `${file}#${index}`,
    vendor: definition.vendor,
    model: definition.model,
    description: definition.description,
    zigbee_model: definition.zigbeeModel ?? [],
    fingerprints: plain(definition.fingerprint ?? []),
    mode: reasons.length === 0 ? "catalogue" : "engine",
    reasons,
    endpoints,
    exposes,
    attributes,
    datapoints,
    tuya: tuyaBase,
    tuya_send_command: prepared.meta?.tuyaSendCommand ?? "dataRequest",
    multi_endpoint: Boolean(prepared.meta?.multiEndpoint),
  };
}

function loadDefinitions() {
  const files = fs
    .readdirSync(path.join(dist, "devices"))
    .filter((f) => f.endsWith(".js") && f !== "index.js")
    .sort();
  const all = [];
  for (const file of files) {
    const { definitions } = requireFrom(`zigbee-herdsman-converters/devices/${file.slice(0, -3)}`);
    definitions.forEach((definition, index) => all.push({ file, index, definition }));
  }
  return all;
}

// A stand-in device for the library's own `findDefinition`, built from one
// fingerprint or one zigbeeModel. The same shape is what the Python matcher
// receives in the golden test.
function fakeDevice(fields) {
  const endpoints = (fields.endpoints ?? []).map((e) => ({
    ID: e.ID,
    deviceID: e.deviceID,
    profileID: e.profileID,
    inputClusters: e.inputClusters ?? [],
    outputClusters: e.outputClusters ?? [],
  }));
  return {
    ieeeAddr: "0x0000000000000001",
    type: fields.type ?? "EndDevice",
    modelID: fields.modelID,
    manufacturerName: fields.manufacturerName,
    manufacturerID: fields.manufacturerID,
    applicationVersion: fields.applicationVersion,
    dateCode: fields.dateCode,
    hardwareVersion: fields.hardwareVersion,
    powerSource: fields.powerSource,
    softwareBuildID: fields.softwareBuildID,
    stackVersion: fields.stackVersion,
    zclVersion: fields.zclVersion,
    endpoints,
    getEndpoint: (id) => endpoints.find((e) => e.ID === id),
  };
}

function deviceFields(device) {
  const out = {};
  for (const [key, value] of Object.entries(device)) {
    if (typeof value === "function" || value === undefined || key === "ieeeAddr") continue;
    out[key] = value;
  }
  return out;
}

async function main() {
  fs.mkdirSync(outDir, { recursive: true });
  const all = loadDefinitions();
  const idOf = new Map();
  const definitions = {};
  for (const { file, index, definition } of all) {
    const described = describe(file, index, definition);
    idOf.set(definition, described.id);
    definitions[described.id] = described;
  }

  const modelsIndex = JSON.parse(fs.readFileSync(path.join(dist, "models-index.json"), "utf8"));
  const models = {};
  for (const [model, entries] of Object.entries(modelsIndex)) {
    models[model] = entries.map(([file, index]) => `${file}#${index}`);
  }

  const version = JSON.parse(fs.readFileSync(path.join(libraryDir, "package.json"), "utf8")).version;
  const catalogue = { library: "zigbee-herdsman-converters", version, models, definitions };
  fs.writeFileSync(path.join(outDir, "catalogue.json"), JSON.stringify(catalogue));

  const golden = [];
  const seen = new Set();
  for (const { definition } of all) {
    const candidates = [
      ...(definition.zigbeeModel ?? []).map((modelID) => ({ modelID, manufacturerName: "loxmatter" })),
      ...(definition.fingerprint ?? []),
    ];
    for (const fields of candidates) {
      const device = fakeDevice(fields);
      const key = JSON.stringify(deviceFields(device));
      if (seen.has(key)) continue;
      seen.add(key);
      const found = await zhc.findDefinition(device);
      golden.push({ device: deviceFields(device), expect: found ? (idOf.get(found) ?? null) : null });
    }
  }
  fs.writeFileSync(path.join(outDir, "golden.json"), JSON.stringify(golden));

  const engine = Object.values(definitions).filter((d) => d.mode === "engine").length;
  console.log(
    `catalogue: ${Object.keys(definitions).length} definitions, ${Object.keys(definitions).length - engine} as data, ${engine} for the engine; ${golden.length} golden cases`,
  );
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
