"""Run ``tine`` subcommands for features opentine only exposes on its CLI.

Format v4 migration, the offline verify report, replay fidelity labels, forks
with edited steps, rewind and the HTML viewer all live in opentine's CLI layer:
their library halves take an ``argparse.Namespace`` and print to a Rich console,
which would scribble over a running Textual screen. Calling the CLI in a child
process gives the dashboard byte-for-byte the behaviour (and the refusals) a user
gets from ``tine``, which is the whole point of mirroring it.

Safety rules every call here follows:

* argv is a list handed to ``subprocess.run`` — there is no shell, so nothing a
  user types or an artifact contains is ever interpreted as shell syntax;
* every path is made absolute first, and values that come from an artifact or
  from user input are passed as ``--opt=value``, so neither can become a flag;
* the child runs with ``-P``, so a package planted in the working directory
  cannot shadow opentine;
* the child gets a timeout, no stdin, ``NO_COLOR`` and a dumb terminal, and its
  output is bounded and stripped of control bytes before anything displays it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from opentine_tui.formatting import plain_text

#: The child imports opentine fresh; a big run can take a while to verify.
DEFAULT_TIMEOUT = 300.0
#: Bytes of stdout/stderr kept for display. A report is far smaller than this.
MAX_OUTPUT = 2 * 1024 * 1024

_LAUNCHER = "import sys; from opentine.cli import main; sys.argv[0] = 'tine'; main()"


@dataclass(slots=True)
class CliResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    def json(self) -> Any | None:
        """The ``--json`` payload, or ``None`` when stdout holds none."""
        text = self.stdout.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except ValueError:
            # Some commands print a warning line before the object.
            start = text.find("{")
            if start < 0:
                return None
            try:
                return json.loads(text[start:])
            except ValueError:
                return None

    @property
    def text(self) -> str:
        """stdout and stderr together, for showing a failure as it happened."""
        parts = [part.strip() for part in (self.stdout, self.stderr) if part.strip()]
        if self.timed_out:
            parts.append("(timed out)")
        return "\n".join(parts)


def arg_path(value: str | Path) -> str:
    """An absolute path, so it can never be parsed as an option."""
    return str(Path(value).expanduser().resolve())


def _environment() -> dict[str, str]:
    env = dict(os.environ)
    env["NO_COLOR"] = "1"
    env["TERM"] = "dumb"
    env["COLUMNS"] = "120"
    # Output is parsed, never rendered with Rich's markup.
    env.pop("FORCE_COLOR", None)
    return env


def _decode(data: bytes | None) -> str:
    if not data:
        return ""
    clipped = data[:MAX_OUTPUT]
    text = clipped.decode("utf-8", "replace")
    if len(data) > MAX_OUTPUT:
        text += "\n… (output truncated)"
    return plain_text(text)


def run_tine(
    args: list[str],
    *,
    cwd: str | Path | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    env: dict[str, str] | None = None,
) -> CliResult:
    """Run ``tine ARGS`` with the interpreter the dashboard itself runs on.

    Using ``sys.executable`` rather than whatever ``tine`` is first on PATH pins
    the child to the opentine this dashboard imported and checked.
    """
    # -P (safe path, Python 3.11+): with plain `python -c` the working directory
    # is first on sys.path, so an `opentine/` package planted wherever the
    # dashboard was started would be imported and run in place of the real one.
    # -P drops that entry but, unlike -I, keeps the user site and PYTHONPATH a
    # `pip install --user` or a dev checkout relies on.
    argv = [sys.executable, "-P", "-c", _LAUNCHER, *[str(arg) for arg in args]]
    environment = _environment()
    if env:
        environment.update(env)
    try:
        completed = subprocess.run(
            argv,
            cwd=str(cwd) if cwd is not None else None,
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return CliResult(-1, _decode(exc.stdout), _decode(exc.stderr), timed_out=True)
    except OSError as exc:
        return CliResult(-1, "", f"{type(exc).__name__}: {exc}")
    return CliResult(completed.returncode, _decode(completed.stdout), _decode(completed.stderr))
