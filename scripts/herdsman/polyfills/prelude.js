// Globals Node provides and a plain JavaScript engine does not.
import { Buffer } from "buffer/";

globalThis.Buffer = Buffer;
if (globalThis.process === undefined) {
  globalThis.process = {
    env: {},
    platform: "linux",
    version: "v22.0.0",
    versions: {},
    nextTick: (callback, ...args) => Promise.resolve().then(() => callback(...args)),
    hrtime: Object.assign(() => [0, 0], { bigint: () => 0n }),
  };
}
if (globalThis.console === undefined) {
  const quiet = () => {};
  globalThis.console = { log: quiet, info: quiet, warn: quiet, error: quiet, debug: quiet };
}
