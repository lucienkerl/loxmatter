# Loxone templates as touchstones

Three files, with different evidentiary weight — that's the reason for this
note.

## `VO_working.xml` — the gold standard for virtual outputs

**Written by Loxone Config itself**, after an import that provably worked
(user, September 3, 2026). What's here is not a derivation and not a
guess.

Two rules come from this that we had gotten wrong twice before:

- `Analog="false"` exactly when an **off command** is set. That's the
  digital output, where Config sets the "Use as digital output" checkbox
  and only then offers the field for the off command at all. An output
  with only one command carries `Analog="true"` — even without a value.
- Config writes the four scaling attributes (`SourceValLow`, `DestValLow`,
  `SourceValHigh`, `DestValHigh`) **only** for outputs without an off
  command.

The BOM and CRLF were added afterward: the content came through as text
via the clipboard. The content itself is unchanged.

## `VO_reference.xml` — only for structure and attribute names now

A **manually cleaned-up derivation** from a real installation (phase 3).
It's useful for attribute names, their order in the document, and the
structure — but its `Analog` value contradicts what Config writes above.
When in doubt, Config wins.

Do **not** use this for the `Analog` value. That's exactly where a fix
went in the wrong direction on September 3, 2026.

## `VIU_reference.xml` — virtual UDP inputs

Also a cleaned-up derivation. Contains no digital example; what's known
about digital inputs comes from observations on the Miniserver and lives
in `src/loxmatter/export/signals.py`, at `LoxoneInput`.
