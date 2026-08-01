# Security Policy

## Supported Versions

opentine-tui is a 0.x line and tracks the `opentine` release it is pinned to.
Only the most recent release receives security fixes, and fixes ship as a new
release rather than as a patch to an older tag.

| Version | opentine | Supported |
| --- | --- | --- |
| 0.5.x | >=0.5,<0.6 | Yes |
| 0.4.x | >=0.4,<0.5 | No |
| 0.3.x | >=0.3,<0.4 | No |

Upgrade before reporting — the issue may already be fixed.

## Reporting a Vulnerability

Report suspected security issues privately, before public disclosure:

1. GitHub's
   [private vulnerability report](https://github.com/0xcircuitbreaker/opentine-tui/security/advisories/new).
   If that page shows no reporting form, private reporting is not enabled; use
   the fallback below.
2. Email `0xcircuitbreaker@protonmail.com`.

Include a minimal reproduction, the opentine-tui and `opentine` versions, your
operating system, terminal emulator, and Python version.

## Scope

This is a **read-mostly dashboard** over local `.tine` artifacts and `.tine/`
repositories. The security properties of the artifacts themselves — integrity
digests, `tine-sig/1` signatures, redaction, the v3 object model — belong to
`opentine`; report those to
[that project](https://github.com/0xcircuitbreaker/opentine/security).

In scope here:

- Rendering an artifact leading to code execution, or to a write outside the
  runs directory. A run id is untrusted data and never becomes a path component
  unsanitized.
- The dashboard reporting a run as verified, signed, promoted, or scored when it
  is not, or hiding a verification failure.
- Destroying data without an explicit, informed confirmation — a signing key, an
  artifact, or a promotion ref.
- Leaking key material or credentials into a file, a log, or the display.

Out of scope:

- Anything that requires the attacker to already control your terminal, your
  runs directory's parent, or the Python environment.
- Launching an external agent harness runs a program you chose, with the
  arguments you supplied. That is the feature.
- A `signer` label on a signature is display-only and is documented as such; it
  is not an identity claim.
