// node:assert, as far as zigbee-herdsman and its converters use it.
function fail(message) {
  throw new Error(message || "assertion failed");
}
function assert(value, message) {
  if (!value) fail(message);
}
assert.ok = assert;
assert.fail = fail;
assert.equal = (a, b, message) => assert(a == b, message);
assert.strictEqual = (a, b, message) => assert(a === b, message);
assert.notStrictEqual = (a, b, message) => assert(a !== b, message);
assert.deepStrictEqual = (a, b, message) => assert(JSON.stringify(a) === JSON.stringify(b), message);
module.exports = assert;
module.exports.default = assert;
