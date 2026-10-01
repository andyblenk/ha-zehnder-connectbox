# Changelog

All notable changes to this project will be documented in this file. The
project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Configure each room's level for the app's situations (at home, asleep,
  away, frost protection) from Home Assistant.
- Show when a temporary change of a room ends, such as a level changed on the
  unit's control panel.
- Show the active situation and select the situation at home or away, which
  switches to the manual mode.
- Show when a room's running boost ends.
- Show the role of each ComfoSpot 50 in the summer ventilation (supply,
  exhaust, or both).

## [0.3.0-beta.2] - 2026-10-01 (pre-release)

### Added

- Control the configured summer ventilation interval from a gateway switch
  when all attached units have a validated profile and report that capability,
  with state and end time read back from the ConnectBox.
- Configure the global summer ventilation function and its 1–24 hour duration
  from separate gateway controls, with read-back and preservation of the other
  setting.
- Show the relative humidity and CO2 concentration measured by the sensor
  board of ComfoSpot 50 units that report them.
- Include the current level, the humidity and CO2 readings, and readings of
  not yet mapped sensor types in the diagnostics.

### Fixed

- Keep an already running summer interval visible and stoppable when its global
  function setting is disabled in the official app.

### Changed

- Create the extract-air temperature entity only after the unit reports a
  usable value. If an earlier version already created it and it remains
  unavailable after updating, the stale entity may need to be removed from
  Home Assistant's entity registry.
- Show the level a unit currently runs at, so a level changed on the unit's
  own control panel appears in Home Assistant. After a level is set from Home
  Assistant, wait briefly until the unit reports it.

## [0.3.0-beta.1] - 2026-10-01 (pre-release)

### Added

- Offer an `auto` ventilation preset that selects sensor-controlled operation
  for ComfoSpot 50 units with a sensor board. It is offered only when the unit
  reports an available humidity or CO2 sensor, or when its room already uses
  sensor-controlled operation in one of its situations.
- Show sensor-controlled operation as the `auto` preset instead of an unknown
  fan state.
- Include the per-situation ventilation values and sensor-mode availability in
  the diagnostics.

## [0.2.1-beta.2] - 2026-10-01 (pre-release)

### Changed

- Refresh fan speeds, temperatures, and fault values every minute while
  refreshing filter counters every 15 minutes.
- Use the next scheduled refresh for missing read-only device values instead
  of repeatedly reading rooms for up to five seconds. Filter resets still
  require a confirmed read-back.
- Read all properties when a ventilation unit is newly discovered or returns
  after an absence, so its filter values appear without a 15-minute delay.

### Added

- Show diagnostic availability indicators for the ComfoSpot 50 extract-air,
  incoming-air, humidity, and CO2 sensors.
- Group fan speeds, filter runtime, and remaining filter runtime with the
  primary device sensors.

### Fixed

- Keep the last valid device-property value when a refresh temporarily reports
  an empty value, preventing brief unavailable states for telemetry sensors.
- Do not present an extract-air or incoming-air temperature as a valid reading
  when its sensor reports that it is unavailable.

## [0.2.1-beta.1] - 2026-09-30 (pre-release)

### Added

- Allow a custom ConnectBox pairing name during setup, while keeping
  "Home Assistant" as the default.

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

[Unreleased]: https://github.com/andyblenk/ha-zehnder-connectbox/compare/v0.3.0-beta.2...HEAD
[0.3.0-beta.2]: https://github.com/andyblenk/ha-zehnder-connectbox/releases/tag/v0.3.0-beta.2
[0.3.0-beta.1]: https://github.com/andyblenk/ha-zehnder-connectbox/releases/tag/v0.3.0-beta.1
[0.2.1-beta.2]: https://github.com/andyblenk/ha-zehnder-connectbox/releases/tag/v0.2.1-beta.2
[0.2.1-beta.1]: https://github.com/andyblenk/ha-zehnder-connectbox/releases/tag/v0.2.1-beta.1
[0.2.0]: https://github.com/andyblenk/ha-zehnder-connectbox/releases/tag/v0.2.0
