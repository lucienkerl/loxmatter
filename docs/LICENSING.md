# Licensing

[Back to the README](../README.md)

## Third-party software

All dependencies are permissively licensed and compatible with GPL-3.0:

| | |
|---|---|
| `python-matter-server`, chip SDK | Apache-2.0 |
| FastAPI, Pydantic, Typer, PyYAML | MIT |
| Starlette, uvicorn, websockets | BSD-3-Clause |
| Alpine.js (bundled) | MIT |

Alpine.js ships as an unmodified copy under
[`../src/loxmatter/web/vendor/`](../src/loxmatter/web/vendor/) — with its own
license text alongside it, as the MIT license requires. This project's GPL
does not extend to Alpine.js itself.

Apache-2.0 is one-directionally compatible with GPL-3.0: Apache-licensed code
may be taken into a GPL-3.0 work, not the other way around.

## Notices in source files

Every source file carries the GPL notice at its head, as prescribed by the
"How to Apply These Terms" section of the GPL — except
[`../src/loxmatter/web/vendor/`](../src/loxmatter/web/vendor/), which is under
MIT and keeps its own notice.

The notice is in the Free Software Foundation's English wording, even though
this project otherwise uses German prose. That is deliberate: it is a legal
reference to [`../LICENSE`](../LICENSE), and a home-made translation would be
an interpretation open to argument.
