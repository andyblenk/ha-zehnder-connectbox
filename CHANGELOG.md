# Changelog

All notable changes to this project will be documented in this file. The
project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed

- Keep periodically refreshed ventilation-unit telemetry available across the
  faster room-state polls and command read-backs.

### Added

- Initial HACS-compatible Home Assistant integration structure.
- Local ConnectBox discovery, physical pairing, and certificate pinning.
- ConnectBox and attached ventilation-unit device model.
- Fan-level, standby, operating-mode, temperature, fan-speed, filter, and fault
  entities for the supported single-room ventilation profile.
- Confirmed ComfoSpot 50 and provisional ComfoAir 70 profile identification.
- Privacy-preserving Home Assistant diagnostics.
