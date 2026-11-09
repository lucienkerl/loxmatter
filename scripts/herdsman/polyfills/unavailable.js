// Node modules the bundled code imports but loxmatter never reaches: OTA
// files, install-code hashing, the library's own file indexer. Calling one
// fails loudly instead of doing something half.
function unavailable(name) {
  return () => {
    throw new Error(`${name} is not available inside loxmatter's engine`);
  };
}
module.exports = new Proxy(
  {},
  {
    get(_target, name) {
      if (name === "__esModule") return false;
      if (name === "default") return module.exports;
      if (name === "sep") return "/";
      if (name === "performance") return { now: () => Date.now() };
      return unavailable(String(name));
    },
  },
);
