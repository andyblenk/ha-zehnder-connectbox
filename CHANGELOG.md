# Changelog

All notable changes to this project will be documented in this file. The
project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.0] - 2026-09-15

### Changed

- Refine the integration icon with centered ventilation and wireless symbols.
- Expand the user documentation with the software-only architecture, a full
  entity reference, and Home Assistant screenshots.

### Fixed

- Keep periodically refreshed ventilation-unit telemetry available across the
  faster room-state polls and command read-backs.
- Accept Home Assistant's positional fan turn-on parameters and default to
  ventilation level 1 when no speed is supplied.
- Hide an unset hardware version reported by a ventilation unit as zero.
- Present the filter warning as a clear filter-replacement requirement.

### Added

- Initial HACS-compatible Home Assistant integration structure.
- Local ConnectBox discovery, physical pairing, and certificate pinning.
- ConnectBox and attached ventilation-unit device model.
- Fan-level, standby, operating-mode, temperature, fan-speed, filter, and fault
  entities for the supported single-room ventilation profile.
- Confirmed ComfoSpot 50 and provisional ComfoAir 70 profile identification.
- Privacy-preserving Home Assistant diagnostics.
- Named fan presets for ventilation levels 1–4.
- Device firmware, hardware, and radio signal information.
- Filter warnings derived from the remaining or maximum runtime when the
  device does not report a dedicated warning flag.
- ComfoSpot 50 filter-timer reset button with gateway acknowledgements, state
  readback, and documented dashboard confirmation.

[Unreleased]: https://github.com/andyblenk/ha-zehnder-connectbox/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/andyblenk/ha-zehnder-connectbox/releases/tag/v0.2.0
