# Device Expert Settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-device "Expert Settings" modal (vendor, model, firmware,
serial number, node address, endpoint/cluster list), backed by a new
`GET /api/devices/{id}/expert` endpoint that resolves these from data
already sitting in the signal store — no new device-stack integration.

**Architecture:** One new backend endpoint derives vendor/model/firmware/
serial by matching known Matter Basic Information cluster attributes
(cluster 40, element ids 1/3/10/15) against a device's existing signals —
the exact same signals Matter and Zigbee devices already register today —
and a new `cluster_name()` catalog lookup (mirroring the existing
`element_name()` in the same module) turns cluster ids into display names
for the endpoint list. The frontend adds one new `<dialog>` modal, following
the existing signals modal's own open/close pattern, and a new kebab menu
entry to open it.

**Tech Stack:** FastAPI + Pydantic (backend), Alpine.js 3 + plain CSS
(frontend), pytest + httpx `ASGITransport` (backend tests), the same
raw-HTML-substring test idiom used throughout `tests/api/test_web.py`
(frontend tests).

## Global Constraints

- Everything in this repository — code, comments, test names, commit
  messages, and design documents — is written in English (`CLAUDE.md`).
  The one exception this plan touches: `src/loxmatter/i18n/strings.yaml`'s
  `de:` values, which carry real German UI text, not translated comments —
  write correct German there, not placeholders.
- Every user-facing string added by this plan goes through
  `strings.yaml` with both `en` and `de` values, resolved via `t(...)` —
  never hardcoded English in `index.html` or `app.js` (`CLAUDE.md`).
- Run `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`,
  `uv run pytest -v`, and `uv run python scripts/check_language.py` before
  considering any task done (`docs/DEVELOPMENT.md`).
- Every response model in `src/loxmatter/api/models.py` uses
  `model_config = ConfigDict(frozen=True)` — new models follow the same
  convention.
- A field the device does not report is `null` (blank in the UI), never a
  fabricated value, an empty-string placeholder, or the em dash — the em
  dash is reserved for the "coming later" section, which has no backing
  data at all (design doc section 5.1).
- Design doc: `docs/superpowers/specs/2026-09-13-device-dashboard-sidebar-and-expert-settings-design.md`,
  sections 5 and 5.1.

**Note on the design doc's JSON shape:** section 5.1 sketches an
`address_label` field ("Node-ID") directly in the API response. This plan
drops that field: every other technology-dependent label in this API
(`DeviceOut.transport`, consumed by `app.js`'s `transportBadge()` via
`t('web.devices.transport_' + device.transport)`, `app.js:1865-1870`) is
resolved to display text on the frontend via `t()`, keyed by the raw
backend value — never baked into the JSON as English prose. Task 3 follows
that exact existing pattern (`t('web.devices.expert_address_' + technology)`)
instead. Everything else in section 5's endpoint shape is unchanged.

---

## Task 1: `cluster_name()` — Cluster Id to Display Name

**Files:**
- Modify: `src/loxmatter/profiles/catalog.py`
- Test: `tests/profiles/test_catalog.py`

**Interfaces:**
- Produces: `cluster_name(cluster_id: int) -> str | None`, for Task 2 to
  turn a `StoredSignal.ref.cluster_id` into a label like `"Basic Information"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/profiles/test_catalog.py`:

```python
def test_a_standard_cluster_gets_its_specification_name():
    """Cluster 47 is PowerSource in the standard - the same dependency
    `element_name` already reads from (see the test above), just indexed
    by cluster id alone instead of (cluster_id, element_id, kind)."""
    from loxmatter.profiles.catalog import cluster_name

    assert cluster_name(47) == "Power Source"


def test_an_unknown_cluster_id_has_no_name():
    from loxmatter.profiles.catalog import cluster_name

    assert cluster_name(4711) is None


def test_a_multi_word_cluster_name_gets_a_space_before_every_interior_capital():
    """The chip SDK's cluster class names are PascalCase
    (`BasicInformation`); nothing in this codebase names clusters for a
    human otherwise (Expert Settings design, 2026-09-13)."""
    from loxmatter.profiles.catalog import cluster_name

    assert cluster_name(40) == "Basic Information"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/profiles/test_catalog.py -v`
Expected: FAIL with `ImportError: cannot import name 'cluster_name'`.

- [ ] **Step 3: Implement `cluster_name()`**

In `src/loxmatter/profiles/catalog.py`, add `import re` to the existing
imports (`functools`, `inspect`):

```python
from __future__ import annotations

import functools
import inspect
import re

from loxmatter.matter.models import SignalKind, SignalRef
```

Then add, after `_catalog()` and before `element_name()`:

```python
@functools.cache
def _cluster_catalog() -> dict[int, str]:
    """Builds the mapping cluster_id -> display name once, the same way
    and for the same reason as `_catalog()` above: read once behind
    `functools.cache`, never rebuilt on every call to `cluster_name`.

    Same fallback contract as `_catalog()`: an unavailable or
    unexpectedly-shaped chip SDK yields an empty mapping, never an
    exception - `cluster_name` then returns `None` for everything, and
    the caller falls back to a generic label (Expert Settings design,
    2026-09-13, section 5.1)."""
    try:
        import chip.clusters.Objects as chip_objects
    except ImportError:
        return {}

    mapping: dict[int, str] = {}
    try:
        clusters = [
            cls
            for _, cls in inspect.getmembers(chip_objects, inspect.isclass)
            if hasattr(cls, "id") and hasattr(cls, "Attributes")
        ]
        for cluster in clusters:
            cluster_id = cluster.id
            if not isinstance(cluster_id, int):
                continue
            mapping[cluster_id] = _display_name(cluster.__name__)
    except Exception:  # noqa: BLE001 — the catalog is not an operational resource
        # (see `_catalog()`'s docstring above): any unexpected shape of a future SDK
        # release stays without consequence instead of stopping the tool.
        return {}
    return mapping


def _display_name(class_name: str) -> str:
    """`BasicInformation` -> `Basic Information`: a space before every
    interior capital letter."""
    return re.sub(r"(?<!^)(?=[A-Z])", " ", class_name)


def cluster_name(cluster_id: int) -> str | None:
    """Human-readable name of a cluster per the chip SDK catalog.

    `None` on the same conditions as `element_name` above - the caller
    falls back to a generic "Cluster {id}" label."""
    return _cluster_catalog().get(cluster_id)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/profiles/test_catalog.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/profiles/catalog.py tests/profiles/test_catalog.py
git commit -m "$(cat <<'EOF'
feat(profiles): add cluster_name(), a display name per cluster id

Mirrors the existing element_name() lookup one level up: the chip SDK
already carries a human name for every cluster class (the same
dependency element_name() reads for attribute/event names), so there is
no reason to maintain a second table by hand. The device expert
settings endpoint needs this to label a device's endpoints by the
clusters present on them.
EOF
)"
```

---

## Task 2: `GET /api/devices/{id}/expert`

**Files:**
- Modify: `src/loxmatter/api/models.py` (add `EndpointClustersOut`, `DeviceExpertOut`, after `DeviceOut`)
- Modify: `src/loxmatter/api/devices.py` (add the route, alongside `get_signals`)
- Test: `tests/api/test_devices.py`

**Interfaces:**
- Consumes: `cluster_name()` (Task 1); `Store.device()`, `Store.signals()`
  (`model/store.py:1463-1471`, `:2308-2328`); `RuntimeValues.last_values_for()`
  (`api/devices.py:151-166`); `transport_for()` (already imported in
  `devices.py`).
- Produces: `DeviceExpertOut` (Pydantic model) and the route
  `GET /api/devices/{device_id}/expert`, returning it. Frontend (Task 3)
  fetches this exact shape.

- [ ] **Step 1: Write the failing tests**

Add to `tests/api/test_devices.py`, near the top alongside the other
imports (`Store`, `extract_commands`, `build_app`, etc. — match whatever
this file already imports for `load_snapshot`/`authenticate`/`httpx`):

```python
from loxmatter.zigbee.translate import DeviceFacts, EndpointFacts, build_snapshot

_ZLL_PROFILE = 0xC05E


@pytest.fixture
async def zigbee_api(tmp_path, no_invoke, fake_runtime, fake_client, fake_otbr):
    """A Zigbee device, built the same way as
    `tests/zigbee/test_zigbee_translate.py`'s `_lamp()` helper: there is
    no Zigbee stick on the test Pi (design 2026-09-12, section 10.3), so
    every Zigbee-side test in this repo builds `DeviceFacts` by hand
    rather than loading a recorded snapshot."""
    store = Store(tmp_path / "t.sqlite")
    facts = DeviceFacts(
        ieee="00:12:4b:00:1c:a1:b2:c3",
        manufacturer="IKEA of Sweden",
        model="TRADFRI bulb",
        is_mains_powered=True,
        available=True,
        quirk_applied=False,
        endpoints=(
            EndpointFacts(
                endpoint=1,
                profile_id=_ZLL_PROFILE,
                device_type=0x0210,
                in_cluster_ids=frozenset({0x0006, 0x0008}),
                attributes={(0x0006, 0x0000): True, (0x0008, 0x0000): 254},
            ),
        ),
    )
    snapshot = build_snapshot(facts)
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)
    store.register_commands(device_id, extract_commands(snapshot))
    app = build_app(
        store, no_invoke, fake_runtime(store), client=fake_client, thread_dataset_source=fake_otbr
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        await authenticate(store, c)
        yield c, store, device_id
    store.close()


async def test_expert_reads_vendor_model_and_firmware_from_basic_information(api):
    """`tests/fixtures/nodes/ikea_grillplats_plug.json` carries
    0/40/1="IKEA of Sweden", 0/40/3="GRILLPLATS Plug", 0/40/10="1.4.6",
    and no 0/40/15 at all - a real device, both the normal case and the
    "device never reported this" case in one fixture."""
    client, _, device_id, _ = api

    response = await client.get(f"/api/devices/{device_id}/expert")

    assert response.status_code == 200
    data = response.json()
    assert data["technology"] == "matter"
    assert data["address"] == "3"
    assert data["vendor"] == "IKEA of Sweden"
    assert data["model"] == "GRILLPLATS Plug"
    assert data["firmware"] == "1.4.6"
    assert data["serial"] is None


async def test_expert_endpoints_list_matter_clusters_by_endpoint(api):
    """Endpoint 0 of the plug fixture carries clusters
    {29, 31, 40, 42, 48, 49, 51, 53, 60, 62, 63} (11 total), endpoint 1
    {3, 4, 6, 29} (4 total), endpoint 2 {29, 144, 145, 156} (4 total) -
    counts read directly off the fixture JSON. Only the cluster ids this
    plan independently verified against the installed chip SDK are
    asserted by name; the rest are covered by the count."""
    client, _, device_id, _ = api

    response = await client.get(f"/api/devices/{device_id}/expert")

    by_endpoint = {e["endpoint"]: e["clusters"] for e in response.json()["endpoints"]}
    assert "Basic Information" in by_endpoint[0]
    assert "Descriptor" in by_endpoint[0]
    assert len(by_endpoint[0]) == 11
    assert "On Off" in by_endpoint[1]
    assert len(by_endpoint[1]) == 4
    assert "Electrical Power Measurement" in by_endpoint[2]
    assert "Electrical Energy Measurement" in by_endpoint[2]
    assert len(by_endpoint[2]) == 4


async def test_expert_labels_a_zigbee_devices_address_as_its_ieee_address(zigbee_api):
    """Zigbee's `address` is already `facts.ieee` (`zigbee/translate.py`)
    and its manufacturer/model are written to the same cluster-40 paths
    Matter uses (`_VENDOR_NAME_PATH`/`_PRODUCT_NAME_PATH`,
    `zigbee/translate.py:431-436`) - no separate code path needed for
    either. Firmware and serial are always null for Zigbee: nothing in
    `zigbee/translate.py` ever writes a SoftwareVersionString (0/40/10)
    or SerialNumber (0/40/15) signal."""
    client, _, device_id = zigbee_api

    response = await client.get(f"/api/devices/{device_id}/expert")

    assert response.status_code == 200
    data = response.json()
    assert data["technology"] == "zigbee"
    assert data["address"] == "00:12:4b:00:1c:a1:b2:c3"
    assert data["vendor"] == "IKEA of Sweden"
    assert data["model"] == "TRADFRI bulb"
    assert data["firmware"] is None
    assert data["serial"] is None


async def test_expert_yields_404_for_an_unknown_device(api):
    client, _, _, _ = api
    assert (await client.get("/api/devices/999/expert")).status_code == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/api/test_devices.py -k expert -v`
Expected: FAIL — `404 Not Found` (no such route) for every test.

- [ ] **Step 3: Add the response models**

In `src/loxmatter/api/models.py`, immediately after the `DeviceOut` class
(the block ending `category_rank: int`), add:

```python
class EndpointClustersOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    endpoint: int
    clusters: list[str]


class DeviceExpertOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    technology: str
    transport: str | None
    address: str
    vendor: str | None
    model: str | None
    firmware: str | None
    serial: str | None
    endpoints: list[EndpointClustersOut]
```

- [ ] **Step 4: Add the route**

In `src/loxmatter/api/devices.py`, add the import next to the other
`profiles` imports:

```python
from loxmatter.profiles.catalog import cluster_name
```

and add `DeviceExpertOut`, `EndpointClustersOut` to the existing
`from loxmatter.api.models import (...)` block.

Then add these two module-level helpers right after `_device_out`
(`devices.py:225-261`):

```python
_BASIC_INFO_CLUSTER = 40
_VENDOR_NAME_ATTR = 1
_PRODUCT_NAME_ATTR = 3
_SOFTWARE_VERSION_STRING_ATTR = 10
_SERIAL_NUMBER_ATTR = 15


def _basic_info_value(
    signals: list[StoredSignal], values: dict[str, float | bool], element_id: int
) -> str | None:
    """The current value of a Basic Information attribute (vendor, model,
    firmware, serial) - `None` if the device never reported it, which is
    always true of firmware and serial for a Zigbee device (Expert
    Settings design, 2026-09-13, section 5.1).

    Matches on `(cluster_id, element_id)`, not on `signal.title`: the
    title is user-renameable (`PATCH /api/signals/{key}`), so matching on
    it would silently break after a rename."""
    signal = next(
        (
            s
            for s in signals
            if s.ref.cluster_id == _BASIC_INFO_CLUSTER and s.ref.element_id == element_id
        ),
        None,
    )
    if signal is None:
        return None
    value = values.get(signal.key)
    if value is None:
        return None
    # `str(value)` first, then check emptiness on the result - not
    # `value == ""` on `value` itself, which mypy's `strict_equality`
    # (this project runs `strict = true`, `pyproject.toml:114`) rejects as
    # a non-overlapping comparison against the declared `float | bool`
    # value type, even though a Basic Information value is a string at
    # runtime.
    text = str(value)
    return text or None


def _endpoints_summary(signals: list[StoredSignal]) -> list[EndpointClustersOut]:
    """One entry per endpoint, naming the clusters present on it -
    every signal's cluster, not just functional ones, since this is a
    structural inventory of the device, not a preview of what it does."""
    by_endpoint: dict[int, set[int]] = {}
    for signal in signals:
        by_endpoint.setdefault(signal.ref.endpoint, set()).add(signal.ref.cluster_id)
    return [
        EndpointClustersOut(
            endpoint=endpoint,
            clusters=[cluster_name(cid) or f"Cluster {cid}" for cid in sorted(cluster_ids)],
        )
        for endpoint, cluster_ids in sorted(by_endpoint.items())
    ]
```

Then add the route itself, inside `build_device_router`, directly after
`get_signals`:

```python
    @router.get("/devices/{device_id}/expert")
    async def get_expert(device_id: int) -> DeviceExpertOut:
        device = _require_device(device_id)
        signals = store.signals(device_id)
        values = runtime.last_values_for(device_id)
        return DeviceExpertOut(
            technology=device.technology,
            transport=transport_for(device.technology, device.network_features),
            address=device.address,
            vendor=_basic_info_value(signals, values, _VENDOR_NAME_ATTR),
            model=_basic_info_value(signals, values, _PRODUCT_NAME_ATTR),
            firmware=_basic_info_value(signals, values, _SOFTWARE_VERSION_STRING_ATTR),
            serial=_basic_info_value(signals, values, _SERIAL_NUMBER_ATTR),
            endpoints=_endpoints_summary(signals),
        )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/api/test_devices.py -k expert -v`
Expected: PASS on all five.

- [ ] **Step 6: Run the full backend checks**

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest tests/api/ tests/profiles/ -v
```

Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add src/loxmatter/api/models.py src/loxmatter/api/devices.py tests/api/test_devices.py
git commit -m "$(cat <<'EOF'
feat(api): add GET /api/devices/{id}/expert

Resolves vendor, model, firmware, serial number, and a per-endpoint
cluster list from signals a device already registers today - the
Matter Basic Information cluster's attributes, which a Zigbee device
writes to the identical paths. Matches by (cluster_id, element_id),
never by a signal's user-renameable title. A field the device never
reported comes back null, not a placeholder string.
EOF
)"
```

---

## Task 3: Expert Settings Modal (Frontend)

**Files:**
- Modify: `src/loxmatter/i18n/strings.yaml` (new `web.devices.expert_*` and `web.devices.menu_expert` keys)
- Modify: `src/loxmatter/web/index.html` (kebab menu entry, new `<dialog>`)
- Modify: `src/loxmatter/web/app.js` (new state + `openExpertModal`/`closeExpertModal`/`expertModalDeviceObject`)
- Modify: `src/loxmatter/web/style.css` (new `.expert-modal` rules)
- Test: `tests/api/test_web.py`

**Interfaces:**
- Consumes: `GET /api/devices/{id}/expert` (Task 2); `this.request(method, url)`
  (existing helper, used exactly as in `commitRenameRoom`, `app.js:2363`);
  `isBackdropEvent(event, el)` (`app.js:4387-4398`); `closeTileMenu($el)`
  (`app.js:2282-2287`); the existing `#i-close` and `#i-warn` icon symbols
  (`index.html:65-75`, `161-164`).
- Produces: `openExpertModal(device)`, `closeExpertModal()`,
  `expertModalDeviceObject()` on the Alpine `app()` object; no other task
  in this or the sidebar plan depends on them.

- [ ] **Step 1: Write the failing tests**

Add to `tests/api/test_web.py`:

```python
async def test_the_tile_menu_offers_expert_settings(api):
    client, _, _ = api
    page = (await client.get("/")).text
    assert 'class="tile-menu"' in page
    menu = page.split('class="tile-menu"', 1)[1].split("</details>", 1)[0]
    assert "openExpertModal(device)" in menu


async def test_the_expert_modal_reuses_the_signals_modals_backdrop_pattern(api):
    """Same `isBackdropEvent`-based click-outside-to-close pattern as the
    signals modal (`app.js:4338-4348`), not a second implementation of
    it."""
    client, _, _ = api
    page = (await client.get("/")).text
    assert 'x-ref="expertModal"' in page
    modal = page.split('x-ref="expertModal"', 1)[1].split("</dialog>", 1)[0]
    assert "isBackdropEvent($event, $el)" in modal
    assert "closeExpertModal()" in modal


async def test_the_expert_modal_marks_signal_strength_and_ip_as_not_yet_available(api):
    """The "coming later" section shows an em dash, never a fabricated
    value - there is no live data behind either field yet (design doc
    section 5.1)."""
    client, _, _ = api
    page = (await client.get("/")).text
    modal = page.split('x-ref="expertModal"', 1)[1].split("</dialog>", 1)[0]
    assert "web.devices.expert_signal_strength" in modal
    assert "web.devices.expert_ip_address" in modal
    assert modal.count("—") == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/api/test_web.py -k expert -v`
Expected: FAIL — none of this markup exists yet.

- [ ] **Step 3: Add the i18n strings**

Append to `src/loxmatter/i18n/strings.yaml`:

```yaml
web.devices.menu_expert:
  en: "Expert settings…"
  de: "Experteneinstellungen…"
web.devices.expert_heading:
  en: "Expert settings — {device}"
  de: "Experteneinstellungen — {device}"
web.devices.expert_loading:
  en: "Loading…"
  de: "Wird geladen…"
web.devices.expert_load_error:
  en: "Could not load: {message}"
  de: "Konnte nicht geladen werden: {message}"
web.devices.expert_section_device:
  en: "Device"
  de: "Gerät"
web.devices.expert_vendor:
  en: "Vendor"
  de: "Hersteller"
web.devices.expert_model:
  en: "Model"
  de: "Modell"
web.devices.expert_firmware:
  en: "Firmware"
  de: "Firmware"
web.devices.expert_serial:
  en: "Serial number"
  de: "Seriennummer"
web.devices.expert_section_address:
  en: "Address & technology"
  de: "Adresse & Technologie"
web.devices.expert_technology_matter:
  en: "Matter"
  de: "Matter"
web.devices.expert_technology_zigbee:
  en: "Zigbee"
  de: "Zigbee"
web.devices.expert_address_matter:
  en: "Node ID"
  de: "Node-ID"
web.devices.expert_address_zigbee:
  en: "IEEE address"
  de: "IEEE-Adresse"
web.devices.expert_endpoint_line:
  en: "Endpoint {endpoint} — {clusters}"
  de: "Endpoint {endpoint} — {clusters}"
web.devices.expert_section_later:
  en: "Coming with a later update"
  de: "Folgt mit einem späteren Update"
web.devices.expert_signal_strength:
  en: "Signal strength"
  de: "Signalstärke"
web.devices.expert_ip_address:
  en: "IP address"
  de: "IP-Adresse"
```

- [ ] **Step 4: Add the kebab menu entry**

In `src/loxmatter/web/index.html`, find (`index.html:1672-1677`):

```html
                        <hr class="tile-menu-sep" />
                        <button
                          class="tile-menu-item"
                          @click="closeTileMenu($el); openSignalsModal(device)"
                          x-text="t('web.devices.menu_signals')"
                        ></button>
```

Insert a new item right after it, before the "Export" button:

```html
                        <hr class="tile-menu-sep" />
                        <button
                          class="tile-menu-item"
                          @click="closeTileMenu($el); openSignalsModal(device)"
                          x-text="t('web.devices.menu_signals')"
                        ></button>
                        <button
                          class="tile-menu-item"
                          @click="closeTileMenu($el); openExpertModal(device)"
                          x-text="t('web.devices.menu_expert')"
                        ></button>
```

- [ ] **Step 5: Add the modal markup**

In `src/loxmatter/web/index.html`, find the signals modal's opening
(`index.html:3003-3010`):

```html
    <dialog
      x-ref="signalsModal"
      class="signals-modal"
      aria-labelledby="signals-modal-heading"
      @close="signalsModalDevice = null; expandedSignalKey = null"
      @mousedown="signalsModalBackdropMousedown = isBackdropEvent($event, $el)"
      @click.self="if (signalsModalBackdropMousedown && isBackdropEvent($event, $el)) $el.close(); signalsModalBackdropMousedown = false"
    >
```

then find that `<dialog>`'s matching `</dialog>` (search forward from
there for the next top-level `</dialog>`) and insert the new modal
immediately after it:

```html
    <dialog
      x-ref="expertModal"
      class="expert-modal"
      aria-labelledby="expert-modal-heading"
      @close="expertModalDevice = null; expertData = null; expertError = null"
      @mousedown="expertModalBackdropMousedown = isBackdropEvent($event, $el)"
      @click.self="if (expertModalBackdropMousedown && isBackdropEvent($event, $el)) $el.close(); expertModalBackdropMousedown = false"
    >
      <template x-if="expertModalDeviceObject()">
        <div class="expert-modal-body">
          <div class="expert-modal-head">
            <h2
              id="expert-modal-heading"
              x-text="t('web.devices.expert_heading', { device: expertModalDeviceObject().label })"
            ></h2>
            <span style="flex: 1 1 auto"></span>
            <button
              class="expert-modal-close"
              :title="t('web.signals.modal_close')"
              :aria-label="t('web.signals.modal_close')"
              @click="closeExpertModal()"
            ><svg class="icon" aria-hidden="true"><use href="#i-close"></use></svg></button>
          </div>
          <p class="hint" x-show="!expertData && !expertError" x-text="t('web.devices.expert_loading')"></p>
          <p class="banner danger" x-show="expertError" x-cloak x-text="expertError"></p>
          <template x-if="expertData">
            <div>
              <section class="expert-section">
                <h3 x-text="t('web.devices.expert_section_device')"></h3>
                <div class="expert-kv">
                  <span class="k" x-text="t('web.devices.expert_vendor')"></span><span x-text="expertData.vendor ?? ''"></span>
                  <span class="k" x-text="t('web.devices.expert_model')"></span><span x-text="expertData.model ?? ''"></span>
                  <span class="k" x-text="t('web.devices.expert_firmware')"></span><span x-text="expertData.firmware ?? ''"></span>
                  <span class="k" x-text="t('web.devices.expert_serial')"></span><span x-text="expertData.serial ?? ''"></span>
                </div>
              </section>
              <section class="expert-section">
                <h3 x-text="t('web.devices.expert_section_address')"></h3>
                <div class="expert-kv">
                  <span class="k" x-text="t('web.devices.expert_technology_' + expertData.technology)"></span><span></span>
                  <span class="k" x-text="t('web.devices.expert_address_' + expertData.technology)"></span><span class="mono" x-text="expertData.address"></span>
                </div>
                <div class="expert-endpoints">
                  <template x-for="ep in expertData.endpoints" :key="ep.endpoint">
                    <p
                      class="expert-endpoint-line mono"
                      x-text="t('web.devices.expert_endpoint_line', { endpoint: ep.endpoint, clusters: ep.clusters.join(', ') })"
                    ></p>
                  </template>
                </div>
              </section>
              <section class="expert-section expert-section-later">
                <h3><svg class="icon" aria-hidden="true"><use href="#i-warn"></use></svg><span x-text="t('web.devices.expert_section_later')"></span></h3>
                <div class="expert-kv">
                  <span class="k" x-text="t('web.devices.expert_signal_strength')"></span><span>—</span>
                  <span class="k" x-text="t('web.devices.expert_ip_address')"></span><span>—</span>
                </div>
              </section>
            </div>
          </template>
        </div>
      </template>
    </dialog>
```

- [ ] **Step 6: Add the Alpine state and methods**

In `src/loxmatter/web/app.js`, add next to the existing
`signalsModalDevice`/`signalsError` state (`app.js:942-943`):

```js
    expertModalDevice: null,
    expertModalBackdropMousedown: false,
    expertData: null,
    expertError: null,
```

and, right after `signalsModalDeviceObject`/`openSignalsModal`/`closeSignalsModal`
(`app.js:4317-4364`), add:

```js
    expertModalDeviceObject() {
      return this.devices.find((device) => device.id === this.expertModalDevice) || null;
    },

    async openExpertModal(device) {
      this.expertError = null;
      this.expertData = null;
      this.expertModalDevice = device.id;
      this.$nextTick(() => this.$refs.expertModal.showModal());
      try {
        this.expertData = await this.request("GET", `/api/devices/${device.id}/expert`);
      } catch (error) {
        this.expertError = t("web.devices.expert_load_error", { message: error.message });
      }
    },

    closeExpertModal() {
      this.$refs.expertModal.close();
    },
```

- [ ] **Step 7: Add the modal's CSS**

In `src/loxmatter/web/style.css`, add after the signals modal's own rules
(`.signals-modal-close:hover`, `style.css:2693-2696`):

```css
.expert-modal {
  width: min(38rem, 92vw);
  max-height: 85vh;
  overflow: auto;
  padding: 0;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface);
  color: var(--text);
}

/* Hardcoded, not a theme variable - same reasoning as `.signals-modal::backdrop`:
 * `::backdrop` sits in the top layer, outside the document tree, and does
 * not reliably inherit `:root` custom properties there. */
.expert-modal::backdrop {
  background: rgba(0, 0, 0, 0.45);
}

.expert-modal-body {
  padding: 1rem 1.2rem 1.2rem;
}

.expert-modal-head {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.expert-modal-head h2 {
  margin: 0;
}

.expert-modal-close {
  flex: none;
  background: none;
  border: 1px solid transparent;
  color: var(--text-muted);
  cursor: pointer;
  padding: 0.25rem;
  line-height: 0;
}

.expert-modal-close:hover {
  color: var(--text);
  border-color: var(--border);
}

.expert-section {
  margin-bottom: 1rem;
}

.expert-section h3 {
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--text-muted);
  margin: 0 0 0.5rem;
  display: flex;
  align-items: center;
  gap: 0.35rem;
}

.expert-kv {
  display: grid;
  grid-template-columns: 9rem 1fr;
  row-gap: 0.4rem;
  font-size: 0.85rem;
}

.expert-kv .k {
  color: var(--text-muted);
}

.expert-endpoints {
  margin-top: 0.5rem;
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}

.expert-endpoint-line {
  font-size: 0.78rem;
  color: var(--text-muted);
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 0.35rem 0.6rem;
  margin: 0;
}

.expert-section-later {
  border: 1px dashed var(--border);
  border-radius: 8px;
  padding: 0.7rem 0.8rem;
  background: var(--bg);
}
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `uv run pytest tests/api/test_web.py -k expert -v`
Expected: PASS on all three.

- [ ] **Step 9: Run the full project checks**

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest -v
uv run python scripts/check_language.py
```

Expected: all green.

- [ ] **Step 10: Manual browser check**

Per this project's convention that web tests prove markup delivery, not
Alpine bindings (`webui-browser-verification`), open the running app in a
browser once, commission or use an existing device, open its kebab menu,
click "Expert settings…", and confirm: the modal opens, vendor/model/
firmware render or stay blank correctly, the endpoint list reads
sensibly, and the "coming later" row shows two em dashes.

- [ ] **Step 11: Commit**

```bash
git add src/loxmatter/i18n/strings.yaml src/loxmatter/web/index.html src/loxmatter/web/app.js src/loxmatter/web/style.css tests/api/test_web.py
git commit -m "$(cat <<'EOF'
feat(web): add the Expert Settings modal

New kebab menu entry opens a per-device modal fed by
GET /api/devices/{id}/expert: vendor, model, firmware, serial and
address up top, a visually distinct "coming with a later update"
section for signal strength and IP address at the bottom - both show
an em dash today since neither exists in the backend yet.
EOF
)"
```

---

## Self-Review

**Spec coverage** (design doc section 5): "Device" section (Task 3, step
5's first `<section>`), "Address & Technology" section including the
endpoint/cluster list (Task 3, step 5's second `<section>`, backed by
Task 2's `_endpoints_summary`), "Coming later" section with an em dash
per field, never a fabricated value (Task 3, step 5's third `<section>`,
verified by its own test in step 1). Section 5.1's backend requirement
(a new endpoint resolving these from existing signal data, `null` for an
unreported field) is Task 2 in full, including its own test for the
"unreported" case using a real fixture rather than a fabricated one.
i18n (design doc section 6): every new string added in Task 3 step 3 goes
through `strings.yaml` with real `en`/`de` values.

**Placeholder scan:** no TBD/TODO; every step's code is complete and
copy-ready; the one deviation from the design doc's literal JSON sketch
(dropping `address_label`) is explained and justified in Global
Constraints, not left ambiguous.

**Type consistency:** `DeviceExpertOut`/`EndpointClustersOut` (Task 2,
step 3) are used with the exact same field names in the route (step 4)
and read with the exact same names in the frontend (Task 3, step 5's
`expertData.vendor`/`.model`/`.firmware`/`.serial`/`.technology`/`.address`/
`.endpoints`, and each endpoint's `.endpoint`/`.clusters`). `cluster_name()`
(Task 1) is imported and called with the same signature
(`cluster_name(cid) -> str | None`) everywhere it's used (Task 2, step 4).
`openExpertModal`/`closeExpertModal`/`expertModalDeviceObject` (Task 3,
step 6) are called with matching names from the markup added in steps 4-5.
