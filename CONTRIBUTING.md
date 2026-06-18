# Contributing to Retrace

Thanks for your interest! Retrace is a privacy-first, on-device macOS tool, and
contributions are welcome.

## Ground rules (please keep these intact)

These are the trust foundation of the project — PRs that weaken them won't be merged:

- **On-device only.** No network calls for capture, OCR, captioning, embeddings,
  search, or analytics. If a feature seems to need a cloud service, open an issue to
  discuss first.
- **Raw frames are never retained.** The full-resolution frame is processed then
  deleted in a `finally` block. Keep that invariant (and its tests) intact.
- **No telemetry.** Ever.
- **Off by default; privacy gates first.** Capture stays off until enabled; the
  denylist / private-window / sensitive-content / presence gates run before anything
  is stored.

## Dev setup

```bash
make setup            # venv (Python 3.13) + editable install
make build-helpers    # compile the Swift helpers (needs Xcode CLT)
make test             # run the suite
```

The test suite is hermetic — it uses a throwaway `RETRACE_HOME` and stubs the native
helpers, so `make test` runs without the Swift toolchain and never touches your real
`~/.retrace`. CI runs it on macOS for every PR.

## Adding an app plugin

The easiest contribution surface. Drop a `RetracePlugin` subclass in
`~/.retrace/plugins/*.py` (a user plugin) or `retrace/plugins/builtin/` (shipped). A
plugin can `enrich(context)` a live capture, `collect()` an app's data on a schedule,
and/or `poll()` for lightweight periodic sampling. See `retrace/plugins/builtin/` for
working examples (Claude Code, Spotify, system stats).

## Pull requests

- Keep PRs focused. Add/extend tests for new behavior.
- Match the surrounding style; run `make test` before pushing.
- Conventional Commit titles (`feat:`, `fix:`, `docs:`, …) are appreciated.

By contributing you agree your contributions are licensed under the project's
[Apache-2.0](LICENSE) license.
