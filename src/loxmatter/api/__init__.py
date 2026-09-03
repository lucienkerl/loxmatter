"""HTTP-API der WebUI (Spec 8) - getrennt vom Loxone-Endpunkt in `loxone.server`.

`loxone.server` bedient den Miniserver (`/cmd`, `/resync`, `/health`); dieses
Paket bedient die Single-Page-App, die Geraete einlernt, benennt und ihre
Signale verwaltet.
"""

from __future__ import annotations
