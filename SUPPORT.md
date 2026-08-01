# Support Policy

opentine-tui is an unfunded beta project. There is no support SLA, no guaranteed
response time, and no paid support channel. Issues are triaged when a maintainer
has time.

## Where to Ask

- Bugs and feature requests:
  [GitHub Issues](https://github.com/0xcircuitbreaker/opentine-tui/issues).
  Include the output of `opentine-tui --version` (it reports both this package
  and the `opentine` it resolved), your operating system, terminal emulator, and
  a minimal reproduction.
- Security vulnerabilities: do not open an issue. Follow
  [SECURITY.md](SECURITY.md).
- Contributing: [CONTRIBUTING.md](CONTRIBUTING.md).

## Which project is this?

This repository is the **terminal dashboard**. The `.tine` format, the v3
repository, the CLI (`tine`), and everything about how runs are recorded, priced,
signed and verified belong to
[opentine](https://github.com/0xcircuitbreaker/opentine) — report those there.

A rough test: if `tine show`, `tine verify` or `tine repo-log` reproduces the
problem without the dashboard running, it is an opentine issue.

## Compatibility

Each release pins one `opentine` minor line, stated in `pyproject.toml` and at
the top of the README. Supported Python versions are the classifiers in
`pyproject.toml`, and CI tests every one of them.
