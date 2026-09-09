# Release note: login instead of token entry

**What changes.** The UI now has a password login.
The field for the API token is gone.

**What to do — immediately after rollout.** Open the UI
(`http://<host>:8080/`) and set a password. Until that has happened,
no `/api` route serves any data, and the UI shows nothing but
the setup screen.

**Why immediately.** Initial setup requires no further proof — whoever
gets there first sets the password. So between the update and your own
login, anyone who can reach the bridge on the network can take it over.
This was deliberately decided this way so that setup is possible without a
shell on the host; the price is this window, and it should take minutes,
not days.

**What stays the same.** `LOXMATTER_API_TOKEN` still applies — as a path
for scripts and `curl`, no longer for the browser. Existing
automations do not break because of this update, not even before the
password is set. `/cmd` and `/resync` for the Miniserver remain reachable
without any protection whatsoever, as always.

**Forgotten password.** In the reference deployment (Docker), `docker
compose exec loxmatter loxmatter set-password` **inside the running
container** resets it; for an installation from source, the equivalent is
`uv run loxmatter set-password` on the host. Both log out all open
sessions in the process. **Important for a containerized installation:**
the database there typically lives in a named Docker volume and
is reachable via `LOXMATTER_STORE` only *inside* the container —
`set-password` on the host would hit a different, empty database there
and falsely report success without unlocking the actual bridge; since
that finding, the command therefore aborts with a clear error instead
of creating a new database.

**A note on the password.** The service speaks HTTP without encryption;
the password travels over the network in plain text when logging in. Pick
one you don't use anywhere else — and have it generated for you rather than
making one up. Behind the login also sits the fabric protection; eight
characters only carry that as long as they aren't guessable.
