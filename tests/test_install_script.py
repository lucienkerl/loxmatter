"""Verhaltenstests fuer install.sh.

Das Skript veraendert fremde Rechner: es installiert Pakete, ruft sudo,
klont und startet Container. Geprueft wird deshalb, WELCHE Befehle es
waehlt - nicht, was sie bewirken. Dazu laeuft es gegen einen versiegelten
PATH aus zwei Verzeichnissen:

  bin/  gefaelschte Binaries (docker, git, sudo, apt-get, curl, uname, ip,
        hostname, usermod). Jedes protokolliert seinen Aufruf nach $STUB_LOG
        und endet erfolgreich. Ein Werkzeug "fehlt" schlicht dadurch, dass
        sein Stub nicht angelegt wird - deshalb darf im PATH nichts liegen,
        was es auf dem Testrechner echt gibt.
  sys/  Symlinks auf genau die echten Werkzeuge, die das Skript legitim
        braucht (sh, awk, sed, grep, ...). `id` steht bewusst NICHT dabei,
        sondern ist ein Stub - sonst haenge das Verhalten davon ab, ob die
        Testsuite gerade als root laeuft.

Die Stubs liegen zusaetzlich unveraendert in templates/. Der apt-get-Stub
kopiert von dort nach bin/, und der curl-Stub gibt fuer get.docker.com ein
Skript aus, das dasselbe fuer `docker` tut. Damit verhaelt sich ein Lauf, in
dem ein Werkzeug fehlt und nachinstalliert wird, wie auf einem echten Host:
danach ist es da.

Die Kindprozesse laufen mit start_new_session=True, also ohne
kontrollierendes Terminal. Damit schlaegt jedes Oeffnen von /dev/tty fehl
und der nicht-interaktive Zweig ist deterministisch - unabhaengig davon, ob
pytest gerade in einem Terminal oder in der CI laeuft.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALLER = REPO_ROOT / "install.sh"

# Echte Werkzeuge, die das Skript benutzen darf. Alles andere kommt aus
# einem Stub oder gilt als nicht installiert.
SYSTEM_TOOLS = (
    "sh",
    "cat",
    "grep",
    "sed",
    "awk",
    "tr",
    "od",
    "mkdir",
    "rm",
    "mv",
    "sleep",
    "chmod",
    "cp",
    "printf",
    "true",
    "false",
)

_UNAME = """case "${1-}" in
  -m) echo x86_64 ;;
  *) echo Linux ;;
esac
"""

_IP = 'echo "default via 10.0.1.1 dev eth0 proto dhcp src 10.0.1.56"\n'

_HOSTNAME = 'echo "10.0.1.56"\n'

# Nicht als echtes Werkzeug: sonst haengt jeder root-Test davon ab, als wer
# die Testsuite laeuft.
_ID = "echo 1000\n"

_OPENSSL = """case "${1-} ${2-}" in
  "rand -hex") echo "aa11bb22cc33dd44ee55ff66aa77bb88cc99dd00ee11ff22aa33bb44cc55dd66" ;;
esac
exit 0
"""

# Holt die "installierten" Pakete aus templates/ nach bin/ - danach sind sie
# wirklich da, so wie nach einem echten apt-get.
_APT_GET = """for pkg in "$@"; do
  if [ -f "$STUB_TEMPLATES/$pkg" ]; then
    cp "$STUB_TEMPLATES/$pkg" "$STUB_BIN/$pkg"
    chmod 755 "$STUB_BIN/$pkg"
  fi
done
exit 0
"""

_SUDO = """cmd=$1
shift
exec "$cmd" "$@"
"""

_DOCKER = """if [ "${1-}" = "compose" ] && [ "${2-}" = "version" ]; then
  echo "Docker Compose version v2.30.0"
fi
exit 0
"""

# Legt beim `clone` ein Checkout an, das die ECHTEN Stack-Dateien enthaelt -
# so laufen die Tests gegen die tatsaechliche docker-compose.yml und .env.example.
_GIT = """if [ "${1-}" = "-C" ]; then shift 2; fi
case "${1-}" in
  clone)
    for a in "$@"; do target="$a"; done
    mkdir -p "$target/deploy/testhost" "$target/scripts"
    : > "$target/Dockerfile"
    cp "$LOXMATTER_REPO/deploy/testhost/docker-compose.yml" "$target/deploy/testhost/"
    cp "$LOXMATTER_REPO/deploy/testhost/.env.example" "$target/deploy/testhost/"
    cp "$LOXMATTER_REPO/scripts/update.sh" "$target/scripts/"
    ;;
  rev-list) echo "${FAKE_BEHIND-0}" ;;
esac
exit 0
"""

# Die get.docker.com-Ausgabe wird vom Skript in `sh` gepipet - sie legt
# deshalb den docker-Stub an, statt nur erfolgreich zu sein.
_CURL = """for a in "$@"; do last="$a"; done
case "$last" in
  *get.docker.com*)
    echo "cp '$STUB_TEMPLATES/docker' '$STUB_BIN/docker' && chmod 755 '$STUB_BIN/docker'"
    ;;
  *health*) echo '{"status":"ok"}' ;;
esac
exit 0
"""

DEFAULT_STUBS = {
    "uname": _UNAME,
    "ip": _IP,
    "hostname": _HOSTNAME,
    "sudo": _SUDO,
    "docker": _DOCKER,
    "git": _GIT,
    "curl": _CURL,
    "openssl": _OPENSSL,
    "id": _ID,
    "apt-get": _APT_GET,
    "usermod": "exit 0\n",
}


class Result:
    def __init__(self, proc, home, log):
        self.returncode = proc.returncode
        self.output = proc.stdout + proc.stderr
        self.home = home
        self._log = log

    @property
    def calls(self):
        if not self._log.exists():
            return []
        return [line for line in self._log.read_text().splitlines() if line]

    def called(self, prefix):
        return any(call.startswith(prefix) for call in self.calls)

    @property
    def env_file(self):
        return self.home / "loxmatter" / "deploy" / "testhost" / ".env"


@pytest.fixture
def installer(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    sysdir = tmp_path / "sys"
    sysdir.mkdir()
    templates = tmp_path / "templates"
    templates.mkdir()
    log = tmp_path / "stub.log"

    for tool in SYSTEM_TOOLS:
        real = shutil.which(tool, path="/usr/bin:/bin:/usr/sbin:/sbin")
        if real is not None:
            (sysdir / tool).symlink_to(real)

    def run(*args, env=None, omit=(), stubs=None):
        active = dict(DEFAULT_STUBS)
        active.update(stubs or {})
        for name in omit:
            active.pop(name, None)
        for stale in list(bindir.iterdir()):
            stale.unlink()
        for stale in list(templates.iterdir()):
            stale.unlink()

        def write(directory, name, body):
            path = directory / name
            path.write_text(f'#!/bin/sh\nprintf \'%s\\n\' "{name} $*" >> "$STUB_LOG"\n{body}')
            path.chmod(0o755)

        # templates/ kennt alles, bin/ nur das, was auf diesem Host "da" ist.
        for name, body in dict(DEFAULT_STUBS, **(stubs or {})).items():
            write(templates, name, body)
        for name, body in active.items():
            write(bindir, name, body)

        full_env = {
            "PATH": f"{bindir}:{sysdir}",
            "HOME": str(home),
            "STUB_LOG": str(log),
            "STUB_BIN": str(bindir),
            "STUB_TEMPLATES": str(templates),
            "LOXMATTER_REPO": str(REPO_ROOT),
            "LOXMATTER_DIR": str(home / "loxmatter"),
        }
        full_env.update(env or {})
        proc = subprocess.run(
            ["/bin/sh", str(INSTALLER), *args],
            env=full_env,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            timeout=120,
            check=False,
        )
        return Result(proc, home, log)

    return run


def test_hilfe_endet_erfolgreich(installer):
    result = installer("--help")
    assert result.returncode == 0
    assert "--dry-run" in result.output


def test_unbekanntes_argument_bricht_ab(installer):
    result = installer("--nope")
    assert result.returncode == 2
    assert "Unknown argument" in result.output


def test_macos_wird_abgewiesen(installer):
    result = installer(stubs={"uname": "echo Darwin\n"})
    assert result.returncode == 2
    assert "needs Linux" in result.output
    assert not (result.home / "loxmatter").exists()


def test_fremde_architektur_wird_abgewiesen(installer):
    riscv = 'case "${1-}" in\n  -m) echo riscv64 ;;\n  *) echo Linux ;;\nesac\n'
    result = installer(stubs={"uname": riscv})
    assert result.returncode == 2
    assert "riscv64" in result.output
