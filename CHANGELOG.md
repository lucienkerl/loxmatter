# Changes

This project uses [Semantic
Versioning](https://semver.org/) starting with 0.2.0. Each published version
has a section here, and the UI shows its text as
change notes before someone installs an update — it will be read by
people who don't know the code.

## [Unreleased]

### Added

- **A synced project file says when it was changed.** A project file
  that comes back from the sync with changes now carries that moment
  as its "last saved" date, in your local time, instead of the date it
  was last saved in Loxone Config.
- **The version card names the commit's date.** On the `dev` channel the
  version alone cannot say which state is installed; the commit line now reads
  "Commit a3f91c2 of 9/21/2026, 6:42:11 PM" as soon as the running image knows it.
- **Commissioning shows what it is doing.** The dialog follows the device
  from "searching" to "found", "connected" and "joined", lists the Matter
  devices advertising nearby, and warns when the Bluetooth chip reports
  errors or the Raspberry Pi reports undervoltage.
- **A failed commissioning says why.** "No device with discriminator 9 is in
  range" or "the Bluetooth connection broke off" instead of the raw error, and
  a code with a typo is caught before anything is sent.
- **The radios card shows the Thread network's name and channel.** After a
  start it warns when Thread devices exist but the border router has no
  network, instead of quietly creating a new one that would cut them off.

### Changed

- **Lights and plugs no longer report their state back to Loxone by
  default.** When Loxone is the only system that switches a device, its
  on/off, brightness and colour inputs only repeat what Loxone just sent.
  They are now unticked for every device — also for those already set up,
  once, with this update. Sensors, buttons, energy readings, the battery and
  the online input stay. If a device has a button or app of its own that can
  switch it — a plug, a switch — that is the case where you tick its values
  again in the signal dialog. Inputs already in your project file are listed
  as orphaned by the sync; remove them in Loxone Config.
- **The installer asks clearer questions, and all of them up front.** It lists
  the USB sticks it finds by name and asks which one is the Thread stick — or
  none, which is also the default when there are two it cannot tell apart. It
  names the Bluetooth adapters instead of asking for an id, no longer asks for
  a baud rate, and says where to find the Miniserver's address. Every question
  comes before the first package is installed, so you can walk away after
  answering.
- **The installer checks the Miniserver address.** It asks the Miniserver for
  its serial number right away. If nothing answers, you can enter another
  address or keep this one; the summary at the end then reminds you to check it.

### Fixed

- **The installer's hints work right after it installed Docker.** Until you
  log out and back in, your shell is not in the `docker` group; the commands
  the installer suggests for looking at the logs now say `sudo docker` in
  that case, instead of failing with "permission denied".
- **A fresh installation forms its own Thread network.** Before, nothing
  created one, and the border router watchdog restarted a healthy but
  unconfigured border router every 90 seconds.
- **Switching Thread on in the radios card no longer rolls back** on a border
  router that has no network yet.

## [0.4.2] — 2026-09-21

### Changed

- **Updates come only from the web interface.** Running the installer again
  no longer checks GitHub for new commits or offers to run an update script;
  its summary points to System → Version instead, which backs up first and
  rolls back a version that does not come up healthy.

### Fixed

- **The classic IKEA TRADFRI motion sensor (E1525, E1745) now reports
  motion.** This model has no motion-sensing cluster at all — it signals
  motion the same way a remote control signals a button press, by sending
  commands rather than reporting a value — and the bridge did not yet
  listen for that. It now binds to the sensor and turns those commands into
  an ordinary occupancy signal. The newer VALLHORN motion sensor, and every
  other brand's motion sensor built on the standard IAS Zone cluster, was
  never affected by this.
- **The updater's radio log keeps its history.** Pulling a new Thread border
  router image wrote hundreds of progress lines into the log shown for radio
  changes, pushing older entries out. The pull now only logs its errors.
- **A device that gains new functions while the bridge is running — typically
  after a firmware update — now offers its new controls straight away.** Its
  new values used to appear, but a button such as on/off only showed up after
  the bridge had been restarted. Seen with a Tasmota plug updated from 13.3
  to 15.6.
- **Such a device is now also named and sorted by its new layout.** A device
  that renumbered its parts in an update used to keep its old arrangement
  for good.

## [0.4.1] — 2026-09-14

### Before you update

- **System → Version asks once more to refresh the updater service — run the
  command it shows.** Right after that, the Thread border router is brought
  up to date on its own, and Thread devices go quiet for about a minute
  while it restarts. If you added a `crontab` line for the Thread watchdog
  when you set up 0.4.0, it is no longer needed; leaving it in place does no
  harm.

### Added

- **The Thread border router recovers on its own when it loses contact with
  the radio stick.** loxmatter now builds and ships its own border router
  image, built with OpenThread's RCP restoration turned on: on a lost
  connection to the USB stick, the agent tries to reconnect, up to twice,
  before it gives up — instead of exiting on the first dropped radio frame,
  which used to take the whole Thread network down with it. New
  installations get this image from the start; existing ones get it through
  the ordinary update.
- **A watchdog for the Thread border router is built in — nothing to set up
  by hand.** The updater service now checks the border router every minute
  on its own and restarts it if it hangs, the same recovery that used to
  need a `crontab` line added on the host.
- **The Thread border router keeps itself up to date.** When its image, USB
  device or radio settings fall behind what the bridge's configuration asks
  for — after a release moves to a newer border router image, for instance —
  the updater brings it up to date by itself: pulls the new image, recreates
  the container, and checks that Thread comes back up, rolling back to the
  previous image if it does not. The Radios card shows this while it
  happens.

### Changed

- **The Radios card says when Thread is off.** Whenever the updater service
  reports the current state, a line under the Thread choice tells you
  whether Thread is off, running, or switched on while its border router is
  not running. If switching Thread on fails and the card restores the
  previous setting, in which Thread was off, it now says exactly that:
  Thread is off, and Thread devices stay unreachable. Before, it only said
  "previous setting restored". A **Try again** button sends the same
  request again after the usual confirmation.
- **The installer no longer asks you to add a `crontab` line, or to edit
  `.env` to add Thread later.** Both now happen on their own — see "Added",
  above, for the watchdog and the upkeep, and turn Thread on from Settings →
  Radios in the web UI whenever you plug a radio module in.

### Fixed

- **Choosing the Thread stick by its stable name works.** The Radios card
  offers USB sticks by the name that survives a reboot
  (`/dev/serial/by-id/...`), but the Thread border router could not open a
  stick given that way and never started: its container does not get
  devices under that path. The stick is now always handed to it under one
  fixed name inside the container, whichever name `.env` uses. Nothing
  changes for an installation that uses `/dev/ttyUSB0`; the new mapping
  takes effect when the border router is brought up to date right after the
  updater refresh (see "Before you update").
- **An empty room sidebar explains itself.** With no device in a room yet,
  the sidebar used to be blank space that looked broken; it now says how to
  give a device a room. Device cards are a little wider again, and the page
  keeps a small margin at the window's edges.

## [0.4.0] — 2026-09-14

### Before you update

- **The database schema rises from 7 to 11.** The update backs the database
  up before it starts, as every update does, and nothing needs doing by
  hand. Going back to a 0.3 version afterwards is not tested: a 0.3 bridge
  does not know groups, Zigbee devices or the settings this release stores.
  If you ever need to go back, restore the backup the update made rather
  than running the old version on the new database.
- **Refresh the updater service once after this update.** It carries the new
  Radios card job and cannot replace itself. On the host, in the stack
  directory:
  `docker compose pull loxmatter-updater && docker compose up -d --no-deps loxmatter-updater`.
  System → Version shows the same command while the updater is behind.
- **If you run the Thread watchdog from cron,** it can now run every minute:
  `* * * * * /home/pi/matter-loxone/scripts/otbr-watchdog.sh >> /home/pi/otbr-watchdog.log 2>&1`
  (see "Thread watchdog" below).

### Added

- **Groups.** Several devices of the same kind — usually lamps — can now be
  switched together from a single block in Loxone. The bridge takes the one
  command and passes it on to each member itself, so the Miniserver no longer
  needs a virtual output per lamp and no longer fires them one after another.
- A group takes only one kind of device. A group of lights offers everything
  any of its lamps can do, and each lamp takes over what it supports: set
  blue at 60 % and the colour lamps turn blue while a warm-white lamp simply
  dims to 60 %; set a warm white and a colour lamp without its own white
  setting shows the nearest colour it can. If one member does not answer,
  the others are still switched and the log names the one that stayed
  dark — "five of six" is the useful answer when something is wrong.
- Groups sit as tiles beside the devices, carry their own room, and get their
  own template in the export and their own container in the project sync, just
  like a device. They only send: a group reports no state back, because six
  lamps have six brightnesses and any single number for them would be invented.
- These are the bridge's own groups, not Matter's. No Matter server this bridge
  can use offers group messaging, so nothing is created on the devices
  themselves.
- **Choose the Thread stick and Bluetooth adapter in the settings.** The new
  Radios card lists the USB sticks and Bluetooth adapters the host has,
  shows which ones are in use, and lets you switch the Thread stick, turn
  Thread off or on, or pick another Bluetooth adapter — no more editing
  `.env` on the host. Changing only the Bluetooth adapter leaves the Thread
  border router untouched, so it restarts and Thread devices go quiet for a
  minute or two only when the Thread half of the change actually asks for
  that. The updater service applies the change, checks that Thread or
  matter-server really come back, and restores the previous setting if they
  don't. The first time, the updater service itself needs one refresh from
  the console; the card shows the command.
- **Zigbee devices, alongside Matter.** A Zigbee lamp, plug or sensor can
  now be paired from the Devices view, on a Zigbee tab beside the Matter one,
  and its values reach Loxone the same way a Matter device's do, as signals
  in the same export. The tab says how to put a device into pairing mode,
  keeps the network open only while you are searching, and says what is
  happening at each step, including when a battery device has fallen asleep
  and needs its button pressed. Nothing reaches Loxone until you press
  **Add**. Zigbee devices get their own badge on the tile.
- **Matter is still required.** Zigbee comes in addition to Matter, not
  instead of it: the bridge runs with matter-server exactly as before, and a
  missing or failing Zigbee stick leaves your Matter devices alone.
- **Zigbee needs a second USB stick**, a Zigbee coordinator of its own. Pick
  it in the new Zigbee row of the Radios card. The bridge opens it itself,
  so choosing one restarts neither matter-server nor the Thread border
  router. The stick your Thread network runs on is listed but can never be
  chosen for Zigbee while Thread uses it; the row says why, and turning
  Thread off frees it. The Thread row, in turn, refuses the Zigbee stick.
  Which stick Thread uses is reported by the updater service: while it has
  not reported recently, no stick can be chosen for Zigbee at all, and the
  row says so. A Zigbee stick already set up keeps working through a
  restart, but is not opened while the bridge has no report at all, or if
  Thread now runs on it. The row shows the firmware the stick reports once
  it has connected.
- **What Zigbee costs.** The bridge's image is larger. Measured while the
  feature was being designed, not on this release's image, the Zigbee
  libraries added about 33 MB of installed Python packages and about 13 MB
  to the compressed download; the image now also ships them precompiled,
  which adds somewhat more. With a Zigbee stick set up, the bridge also
  needs time after every start to load what it knows about individual
  Zigbee devices: an estimated 9 to 15 seconds on a Raspberry Pi 4, worked
  out on a faster machine and not yet measured on a Pi. The web interface
  and your Matter devices do not wait for it, and without a Zigbee stick it
  does not happen at all. To open a stick chosen in the browser, the bridge
  may now open any USB serial device on the host; the README's Updating
  section says exactly what that grants.
- **Firmware updates for your Zigbee devices stay off.** The Zigbee library
  can update a lamp's firmware from the internet on its own schedule;
  loxmatter switches that off, so nothing on your Zigbee network changes
  without you.
- **Removing a Zigbee device works without its stick.** A device the
  Zigbee stick no longer knows — after a fresh network, or another stick —
  is simply removed. With no Zigbee stick set up at all, the device's tile
  says the device cannot be asked to leave its network and offers **Remove
  from loxmatter only**: the device leaves the device list and the export,
  and the bridge stops sending its values to Loxone, but the device itself
  is not told — factory-reset it before pairing it anywhere else. Matter
  devices are still always removed through matter-server first.
- **Colour in XY form.** Colour lamps get a second colour output in the
  export, `color_xy`, which sends the colour as XY coordinates, the colour
  command Matter makes mandatory for full-colour lamps. Matter and Zigbee
  lamps get it alike. The existing colour output, its wiring in Loxone and
  the colour picker in the browser are unchanged. A lamp that takes colour
  only as XY — it reports XY but neither hue and saturation nor a colour
  temperature — now gets a colour control for the first time, through this
  output, and the picker in the browser uses it; the picker cannot show
  where such a lamp currently is, and says so. Tunable-white lamps still get
  no colour control. A colour lamp that reports XY and a colour temperature
  but not hue and saturation looks exactly like a tunable-white one by those
  reports alone; it gets its colour control when it says it is a colour lamp
  (an "extended colour light"), and none otherwise. A lamp that reports hue
  and saturation gets its colour control whatever kind of lamp it says it
  is.
- **Your existing colour lamps show one change in the export.** The bridge
  records the new `color_xy` output for colour lamps you commissioned before
  this release, the next time it starts. The export view then marks those
  lamps as changed once, until you export them again; nothing in Loxone
  changes unless you do.
- **Zigbee lamps and plugs are recognised as lamps and plugs.** The common
  Zigbee 3.0 colour, tunable-white and plug devices get the lamp or plug
  icon and category, and can join a group with Matter lamps or plugs of the
  same kind.
- **Thread or IP at a glance.** Every device tile now carries a small badge on
  its icon showing whether the device talks to the bridge over Thread or over
  your IP network (Wi-Fi or Ethernet). Hover it for the name. A device that
  does not say how it is connected gets no badge rather than a guess.
- **Rooms move to a sidebar.** The row of room chips above the device grid is
  now a sidebar beside it, staying in view as you scroll and giving rooms
  room to grow without crowding the top of the page. The device grid itself
  is denser too, laying out more tiles per row on a wide screen.
- **Expert Settings, for anyone who wants the technical details.** A new
  entry in each device's kebab menu opens a modal with the things loxmatter
  itself doesn't wire into Loxone: vendor, model, firmware version and
  serial number, how the device is reached (Matter over Thread or IP, or
  Zigbee) and its address, and every endpoint with the clusters it
  exposes. Vendor, model, firmware and serial number show up for devices
  you commission from now on; for devices you already have, they appear
  after the bridge's next restart, once it has asked each device once more.

### Changed

- **The bridge's own log is in the container log.** `docker logs loxmatter`
  used to show only the web server's lines; the bridge's own — a warning, a
  device that would not answer, a radio that would not open — were only in
  the System tab, which keeps the last 500. They now appear in both. A
  connection that keeps failing the same way is explained once and then
  counted, instead of repeating the whole explanation every minute.
- **A Loxone command gives up after 10 seconds.** A command to a device that
  does not answer used to wait for as long as matter-server did; the bridge
  now answers Loxone with an error after 10 seconds. On a slow Thread device
  matter-server may still deliver the command a little later, after Loxone
  has been told it failed.
- **A dragged slider no longer queues up commands.** Loxone sends a new
  value about once a second while a slider moves — a colour, for example —
  without waiting for the lamp. Each lamp now gets one command at a time,
  and of the values that arrive while it is busy only the newest waits; the
  ones in between are skipped. The lamp still ends on the value the slider
  stopped at. This takes load off the Thread border router software, which
  could previously be overwhelmed by such a burst and give up. Clicks in the
  web interface wait in the same queue as Loxone's commands. On, off and
  toggle are never skipped. A command waiting behind a lamp that has not
  answered anything for 10 seconds gives up like any other; a lamp that is
  slow but still answering does not make the commands behind it fail.
- **Removing a device waits longer, and a second removal no longer fails.**
  Removal now waits up to two minutes for matter-server to reach the
  device, instead of giving up after 10 seconds while matter-server was
  still working on it. A device matter-server has already forgotten counts
  as removed instead of failing with an error.
- **Some Matter sensor readings are no longer suggested for export.** For
  contact and water-leak sensors, occupancy sensors and light sensors, the
  bridge now names the one reading that matters — open or closed, occupied,
  the light level — and suggests only that one. Their other readings, such
  as a light sensor's minimum and maximum, move to the "Expert" section of
  "Edit signals…". What you have already exported is not touched; only
  devices you commission from now on start with the shorter list.
- **Thread's exclusive stick lock is available, and off.** OpenThread can
  lock its USB stick so no other program opens it by accident. It is not yet
  known whether the border router image on every installation accepts that
  setting, and one that does not would keep Thread down, so it is off unless
  `.env` sets `OTBR_RADIO_URL_EXTRA=&uart-exclusive`. Nothing changes for an
  installation that does not.
- If you script against the API with a token: `GET /api/devices` no longer
  returns `node_id`. Each device now reports `technology`, `address` and
  `transport` instead, to make room for device types beyond Matter.
- **Thread watchdog: every minute, and never on top of itself.** The
  watchdog script restarts the Thread border router when its network
  interface is gone. It now takes a lock, so a run that is still waiting for
  Thread to come back is never interrupted by the next one; it leaves a
  border router alone for 90 seconds after it started, so it does not cut
  short a start that is still attaching; and every Docker call it makes has
  a time limit, so a hanging call is logged instead of blocking every later
  run. That makes a check every minute safe, which keeps an outage to about
  a minute instead of up to five.
- **The Radios card waits longer for Thread, and keeps the evidence.** After
  switching the Thread stick or turning Thread on, the updater service now
  gives the border router 150 seconds to attach, and restarts it only after
  60 seconds without a network - it used to restart it after 30, in the
  middle of a normal start, and then give up. If Thread still does not come
  back and the previous setting is restored, the border router's log is
  saved to the update directory first instead of being deleted with the
  container. While the card changes Thread, the watchdog does not step in.
- **The Thread border router image can be chosen in `.env`.** Setting
  `OTBR_IMAGE` runs another image, for example one built with OpenThread's
  RCP recovery (`-DOT_RCP_RESTORATION_MAX_COUNT=2`), which the official image
  does not include: without it, a single message lost between the host and
  a USB Thread stick ends the border router. Left empty, nothing changes.
- **Project file sync creates new devices without asking.** A device the
  uploaded project does not know yet now always gets its own virtual input
  and output in the patched file. This used to be an experimental checkbox;
  it has worked reliably in Loxone Config, so the checkbox is gone and there
  is one file to download.

## [0.3.10] — 2026-09-10

### Fixed

- While an update on the **Development** channel is building, the card
  now says so. It used to show the step for downloading, because the
  build had no state of its own — so anyone watching waited for a
  download that was not happening and wondered why their connection was
  so slow. The step reads "Building the image" on that channel and
  "Loading the image" on **Stable**, which is what each one does.

## [0.3.9] — 2026-09-10

### Changed

- On the **Development** channel the bridge is now built on the device
  instead of being downloaded. The code is already there — a development
  update is a checkout away — so waiting for a build to finish elsewhere
  was the whole cost of following that channel. Measured on a Raspberry
  Pi 4: about twenty seconds when only the source changed, about thirty
  when the dependencies did. Faster than the download it replaces.
- The **Stable** channel is unchanged. It downloads published versions,
  as it always has, and that is what an installation nobody is working
  on should be following.

## [0.3.8] — 2026-09-10

### Fixed

- Switching to the **Development** channel is no longer a one-way trip.
  Going back to **Stable** was refused every time, with a message about
  the running version not stating a version, and the only way back was
  the console. It works from the interface now, and the card says
  beforehand what the move means: the release you land on is the newest
  published one, it may be older than the development build you are
  running, and the newer changes reach a release only later.

## [0.3.7] — 2026-09-10

### Added

- The **update channel** can be chosen again in the interface, under
  Version & updates. *Stable* follows published releases, the way this
  bridge has always updated. *Development* follows every change as it
  lands — new things arrive sooner, and so do their rough edges. The
  control has existed since 0.3.0 and was hidden because choosing
  *Development* could not actually install anything; it can now.

### Fixed

- Choosing the development channel and pressing update no longer fails
  every time. Three separate reasons it could not work are gone: the
  update it offered did not name a version the bridge could install, the
  image it would have downloaded was never published under that name,
  and the check for "is this actually newer" compared against the wrong
  thing — the copy of the source on the device rather than the version
  running. That last one also stopped the check being fooled after an
  update from the console.
- The green **"Now running: …"** notice no longer stays on the System
  tab forever. It reports the update you just watched and is gone after
  a page reload. A *failed* update still says so after a reload — that is
  something still waiting to be dealt with, not news.

## [0.3.6] — 2026-09-10

### Fixed

- **An update through the web interface could leave the bridge unable to
  reach your Miniserver — and report success.** The bridge was restarted
  without the settings file that holds the Miniserver's address and the
  API token, so it came back configured with neither. Its health check
  answers regardless of whether it can reach anything, so the update
  reported itself finished and the tile turned green while the house
  went quiet. Nothing on disk was lost: only the running container was
  rebuilt without those values. If this has happened to you,
  `docker inspect loxmatter --format '{{json .Config.Cmd}}'` shows an
  empty entry after `--miniserver`, and recreating the container from
  the console restores it.

  Two things follow from the same cause, and are fixed with it: the
  version an update wrote down was never actually deployed — the
  `stable` image was reinstalled every time, whichever version you
  chose — and a rollback brought the same failed image back while
  reporting that the previous version was running again.

- **Updating from the console failed after any update run from the web
  interface**, with a message about local changes when there were none.
  The web updater leaves the checkout pinned to an exact version, and
  the console script had no way back from that. It now recovers on its
  own, whatever left it that way.

- **The interface could claim the updater had crashed while it was
  working normally**, and advise restarting it — which, during the step
  it usually appeared in, would have interrupted a healthy update. The
  updater now keeps reporting for duty during its longer steps.

- **A failed update whose rollback also failed was reported as if the
  rollback had worked.** That is the one outcome that needs a person, and
  it now says so plainly and names the file that explains what to do.

- **An update begun on a full disk could empty the settings file and
  report success.** The write is now checked before the old file is
  replaced.

- **The file written when an update is interrupted printed paths that do
  not exist on your machine**, so the commands in it failed when pasted.
  It now prints the paths as you see them.

### Changed

- The updater writes its "still working" signal five times less often
  during long steps, which matters on an SD card, and reacts faster when
  a step finishes.

## [0.3.5] — 2026-09-10

### Fixed

- **The "updater is out of date" warning could fire on a release that
  never touched the updater at all.** It compared version numbers, and
  every release stamps a version number into the updater image whether
  or not that image actually changed — so the warning could tell you to
  refresh something that was already current. It now compares the actual
  image running against what is currently published, and only speaks up
  when they genuinely differ.
- **The command shown to refresh the updater named a directory that may
  not exist.** It printed a path taken from the documentation
  (`~/loxmatter/...`), not from your own checkout — so on any checkout
  not named exactly that, the command failed at the first step. It now
  asks the updater for its own real location and prints that instead;
  if it can't be determined, the message says so rather than guessing.

## [0.3.4] — 2026-09-09

### Fixed

- **The updater's version could go missing, or go stale, on the System
  tab.** The card that says when the updater has fallen behind the bridge
  only updated when an update actually ran — so a freshly installed
  updater could sit there for minutes with no version shown at all, and an
  updater later replaced by a newer one could keep reporting the old
  version long after it was gone. The updater now reports its own version
  on every check-in, not only when it does work, so the card always
  reflects what is actually running.

## [0.3.3] — 2026-09-09

### Changed

- **The updater no longer tries to update itself.** After installing an
  update for the bridge, it used to also try refreshing its own container
  — and that was measured to fail: a container cannot correctly replace
  itself while it is the very thing running the command that would do it.
  On a real test this left the installation with no updater running at
  all, and a second, half-finished container next to it that only the
  console could clean up — the worst outcome for a feature whose whole
  point is to avoid the console. The updater now leaves itself alone and
  only ever installs updates for the bridge, exactly as reliably as
  before.

### Added

- **System → Version now says when the updater itself is out of date.**
  Since the updater no longer refreshes itself (see above), it can quietly
  fall behind the bridge it serves. The System tab now compares the two
  and, only when they actually disagree, shows the one command that brings
  the updater back in step. An installation whose updater predates this
  check simply sees nothing extra — no false alarm over a fact it cannot
  yet report.

## [0.3.2] — 2026-09-09

### Fixed

- **A successful update no longer reports itself as failed.** After an
  update finished, the updater replaced its own container — and being shut
  down for that replacement looked, from the inside, exactly like being
  interrupted. It then overwrote the finished result with a failure, so the
  System tab announced "Update failed" beside a bridge that had updated
  perfectly well. A finished update now stays finished, whatever happens to
  the updater afterwards. This showed up on the first update whose release
  also rebuilt the updater — which is most of them.

## [0.3.1] — 2026-09-09

### Fixed

- **A rollback now names the version it restored.** When an update does not
  come up healthy, the bridge returns to the version that was running before
  — and said so on screen by naming the update channel it came from, usually
  "stable", rather than the version itself. The channel is not a version, and
  this is the one message that has to be exact. The plain-text report the
  updater leaves behind was already correct; the screen now agrees with it.

## [0.3.0] — 2026-09-09

### Added

- **System → Version can now install updates by itself.** It shows what
  is running, tells you when a newer published version is available, and
  a button installs it after you confirm — a backup of the signal
  database is taken first. The bridge is unreachable for about a minute
  while it restarts; the page says so and reconnects on its own once it
  answers again. If the new version does not report itself healthy within
  two minutes, it is rolled back automatically and the version you had
  keeps running — this happens once per attempt, not repeatedly, and the
  database is left as it was, not rolled back with it.
- This needs the new `loxmatter-updater` service from the compose file.
  An installation from before this release does not have it yet and
  needs the console once to bring it in: `git pull && ./scripts/update.sh`.
  Afterwards, updates work from the browser like any newer installation.
  Without that service — or after removing it — the System tab still
  names the running version and points back to the console path.
- **Device cards show when a device was last heard from**, as a coarse age
  that never counts seconds. A device that has never reported says so.

### Changed

- The bridge now talks to matter.js (`matterjs-server`) through
  `matter-python-client`, replacing `python-matter-server`. **This has not
  been run on real hardware yet.** It does not reach an existing
  installation by itself: every update path recreates only the bridge and
  the updater, never matter-server, so the change sits in your checkout
  until you deliberately bring the whole stack up with
  `docker compose up -d`. Read `deploy/testhost/docker-compose.yml` before
  you do — the new container runs unprivileged and needs a one-time
  ownership change on its data directory.

### Fixed

- **The bridge notices when its link to matter-server drops and rebuilds
  it.** Until now a matter-server restart left the bridge quietly inert
  while diagnostics still reported a healthy connection. The heartbeat to
  the Miniserver now falls silent while there is no Matter link, instead
  of pretending everything is fine.

## [0.2.0] — 2026-09-08

### Added

- The UI shows which version is running in the System tab.
- Ready-made images are available at `ghcr.io/lucienkerl/loxmatter`
  (`arm64` and `amd64`). An update now downloads such an image instead of
  building it on the Raspberry Pi itself — the local build took
  five to ten minutes, and downloading a ready-made image
  should speed that up significantly. This hasn't been measured yet: there is
  no device on which an update has taken this path yet.

### Changed

- `scripts/update.sh` pulls the image instead of building locally. `--build`
  restores the old behavior.
- `install.sh` now also starts the stack without `--build`: `docker
  compose up -d` pulls the published image. A `--build` would have
  tagged the result with the name `ghcr.io/lucienkerl/loxmatter:stable`,
  even if built locally - a fresh installation would then have had a
  local image that reported itself as `dev`.
- The stack runs from a published image. **This one
  change needs the console once:** `git pull &&
  ./scripts/update.sh` on the device running the bridge.
