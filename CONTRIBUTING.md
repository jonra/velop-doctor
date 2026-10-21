# Contributing

Thanks for helping. The most valuable contribution is **data from a Velop that
is not a WHW03 v2**: other models, other firmware, other failure modes.

## Run the tests

```bash
cd monitor
PYTHONPATH=src:tests python3 -m unittest discover -s tests -v
```

No third-party packages are needed, and none should be added to `monitor/`
without a strong reason: easy installation is a feature.

## MCP server

`monitor/tests/test_mcp.py` covers the protocol layer and every tool against a real dashboard server
(demo data). To check against the official SDK client, see `monitor/scripts/mcp_sdk_check.py`. A new tool
needs a schema with `additionalProperties: false`, honest `readOnlyHint`/`destructiveHint` annotations, and,
if it changes anything, the preview-then-`confirm` flow.

## The probe

```bash
cd probe
python3 -m venv .venv && .venv/bin/pip install pillow pytest
.venv/bin/pytest
```

It runs on a Mac or Linux box without the Pi hardware; sample screens are written to
`probe/tests/output/`. Pi-side install and the display are covered in `probe/README.md`.

## Try your change against the UI

```bash
PYTHONPATH=src python3 -m velop_doctor serve --demo --port 8777
```

The demo backend (`monitor/src/velop_doctor/demo.py`) produces a mesh with
deliberate faults, so you can see how a new check looks without breaking your
own network.

## Adding a check

1. Add the logic to `analyzer.evaluate` as a finding: a stable `key`,
   a severity, a plain-language title and detail, and a `min_seconds` so that
   brief blips do not alarm anyone.
2. Add a test in `monitor/tests/test_analyzer.py` built from a demo snapshot.
3. Describe it in `docs/checks.md`.

Findings should say what is wrong **and** what to try. Avoid claiming a cause
you cannot prove from the data; say "may" or "probably".

## Privacy: please read before opening an issue

Device lists contain your devices' names, MAC addresses and IPs, and API
responses can contain Wi-Fi network names and keys. **Never paste raw dumps into
an issue.** Redact names, MAC addresses and IPs first, and remove any field
containing `passphrase`, `key` or `password`.

## Security issues

If you find a way to make the dashboard act on a network it should not, please
report it privately rather than in a public issue: see [SECURITY.md](SECURITY.md).
