## What and why

<!-- What does this change, and what problem does it solve? -->

## Checklist

- [ ] Tests added or updated, and they pass (`cd monitor && PYTHONPATH=src:tests python3 -m unittest discover -s tests`, or `cd probe && pytest`)
- [ ] No new dependencies in `monitor/` (it is deliberately standard-library only)
- [ ] Docs updated if behaviour changed (`docs/`, the READMEs, `CHANGELOG.md`)
- [ ] No personal data in the diff: no device names, MAC addresses, IPs, serial numbers, passwords or tokens
- [ ] Anything that changes a setting on a Velop previews first and needs explicit confirmation
