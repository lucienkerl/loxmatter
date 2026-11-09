// node:events, from the `events` npm package, in both the shapes the
// bundled code imports it in.
const events = require("events/");
module.exports = events;
module.exports.default = events;
module.exports.EventEmitter = events.EventEmitter || events;
