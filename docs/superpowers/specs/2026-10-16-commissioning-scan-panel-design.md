# The scan panel in the commissioning dialog

Date: 2026-10-16. Builds on `2026-10-02-commissioning-queue-design.md`.

## 1. Problem

A Bluetooth scan takes ten seconds (`SCAN_SECONDS`). Until now the dialog
showed it as one grey hint line, `Scanning over Bluetooth (10 s) …`, between
the disclosures and the cards. Nothing on the page moved, so a user who had
just opened the dialog could not tell whether the bridge was searching or
had found nothing.

## 2. Design

The hint line becomes a panel in the same place, chosen from screen design
variant A (a countdown ring). It has four states:

| State      | When                                      | Look                               |
|------------|-------------------------------------------|------------------------------------|
| `scanning` | `scan.state == "scanning"`                | copper; ring counting down, pulsing Bluetooth mark |
| `found`    | idle, at least one `found` card           | green; check mark                  |
| `none`     | idle, no `found` card                     | plain; "Scan again" as the primary button |
| `blocked`  | a device is being commissioned            | grey; pause mark, button disabled  |

"Scan again" moves from the room row into the panel, beside the result it
repeats. While a scan runs the button is hidden, and a dashed placeholder
under the cards shows where the devices will appear.

The Bluetooth adapter warning stays the banner it was: it can come with any
of the four states.

## 3. The countdown

`GET /api/commissioning` already sends `scan.last_scan_age`, the time since
the latest scan started. It now also sends `scan.seconds`, the scan's length,
so the page does not keep a second copy of the number. The page polls every
second while the dialog is open. Each answer sets the ring's
`stroke-dashoffset`, and a one-second linear CSS transition moves the ring
smoothly between two answers. The page keeps no timer of its own.

The number in the ring never shows 0 while the scan still runs, because 0
would read as finished. A scan that runs past its length stays at 1 until
the bridge reports it idle.

Under `prefers-reduced-motion` the transition and the pulse go. The ring
and the number still count down.
