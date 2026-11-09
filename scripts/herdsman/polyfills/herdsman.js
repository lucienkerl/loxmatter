// The part of zigbee-herdsman that zigbee-herdsman-converters uses: its ZCL
// codec and cluster tables, not the stack. Its package entry would pull in the
// serial port and native bindings (design 2026-11-09, section 4.2).
exports.Zcl = require("zigbee-herdsman/dist/zspec/zcl");
exports.ZSpec = require("zigbee-herdsman/dist/zspec");
exports.Zdo = require("zigbee-herdsman/dist/zspec/zdo");
exports.getTimeClusterAttributes = require("zigbee-herdsman/dist/utils/timeService").getTimeClusterAttributes;
