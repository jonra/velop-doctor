# Third-party code

## Waveshare e-Paper driver

- Location: `probe/ethtester/vendor/waveshare_epd/`
- Upstream: https://github.com/waveshareteam/e-Paper (`RaspberryPi_JetsonNano/python/lib/waveshare_epd`)
- Licence: MIT, stated in the header of each vendored file, which are kept unchanged.
- Local modification: documented in `probe/ethtester/vendor/waveshare_epd/README.md`
  (a bounded wait on the BUSY pin instead of an endless loop).

Everything else in this repository is MIT-licensed under the top-level [LICENSE](LICENSE).
