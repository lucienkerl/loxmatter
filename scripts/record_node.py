"""Speichert das Abbild eines echten Geräts als Fixture.

Aufruf: uv run python scripts/record_node.py 12 tests/fixtures/nodes/ikea_bulb.json
Mit abweichendem matter-server:
       uv run python scripts/record_node.py 3 tests/fixtures/nodes/ikea_plug.json \\
           --url ws://10.0.1.56:5580/ws
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from loxmatter.matter.client import BridgeMatterClient


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("node_id", type=int, help="Node-ID am matter-server")
    parser.add_argument("target", type=Path, help="Zieldatei für die Fixture")
    parser.add_argument(
        "--url",
        default="ws://localhost:5580/ws",
        help="Adresse von matter-server (Default: ws://localhost:5580/ws)",
    )
    return parser.parse_args()


async def main() -> None:
    args = _parse_args()

    client = BridgeMatterClient(args.url)
    await client.connect()
    try:
        snapshot = await client.snapshot(args.node_id)
    finally:
        await client.disconnect()

    args.target.write_text(
        json.dumps(
            {"node_id": snapshot.node_id, "attributes": dict(snapshot.attributes)},
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"{args.target} geschrieben, {len(snapshot.attributes)} Attribute")


if __name__ == "__main__":
    asyncio.run(main())
