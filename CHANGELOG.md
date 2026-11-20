# Changelog

All notable changes to this project will be documented in this file. The
project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Add a fan status sensor per unit that shows what the unit is doing right
  now (central off, standby, level 1–4, sensor mode, boost, summer
  ventilation) with a matching icon, and carries the level, situation, fan
  speeds, supply-only operation, end times and active fault codes as
  attributes for dashboards.
- List the active fault codes on the fault sensor.
- Add icons for the ventilation levels, fan speeds, filter values and end
  times.
- Start and stop a room's boost and set its duration, set the runtime of
  level 1 per hour and the summer ventilation role of a unit, and show its
  bath function, automatic operation sensors and error log, as on the unit's
  page in the official app (ComfoSpot 50).
- Select the active weekly schedule, as on the app's plan page, and show the
  active weekly and daily schedule.
- Add the app's absence planner: a switch with start and end, and an
  indicator while the absence runs.
- Add services to add, change and remove daily schedules (switching times
  and timed boost or summer ventilation) and weekly schedules.
- Switch between at home and away while the schedule keeps running, like the
  house on the app's start screen.
- Show the app's history (Verlauf) and the radio duty cycle the ConnectBox
  logs.
- Change the system name and language, switch the access over the internet,
  and change each room's name and type, as on the app's settings pages.
- Show the paired apps (Benutzer), the country and network connection, the
  system version and the firmware update state of the units, and register
  the ConnectBox's serial number.
- Install a newer firmware package of the official app with a firmware update
  entity; the package is copied into the folder `zehnder_connectbox`.

### Changed

- Show the ventilation levels with numbered icons (1–4), clearly apart from
  the sensor-controlled `auto` preset.
- **Breaking:** the ventilation presets are now `level_1` to `level_4` and
  `auto`, so the frontend shows them translated ("Stufe 1" …). Automations
  that call `fan.set_preset_mode` with `Level 1` to `Level 4` must be updated.

### Fixed

- Stop passing the deprecated `via_device` device information and use
  `UnitOfRatio.PARTS_PER_MILLION` for CO2, both removed in Home Assistant
  2027.8. The link to the ConnectBox is still set when devices are registered;
  versions that do not accept the device ID for the link get the identifier.
- Report the filter runtime, remaining runtime and maintenance interval in
  days instead of hours; the unit counts days (for example a 90-day
  interval).
- Show only the active fault codes on the fault sensor; the unit's error log
  is history and now has its own sensor.
- Read the slowly changing unit settings with the filter values instead of on
  every poll, and skip a request the unit rejects until the next reload.

## [0.3.3] - 2026-11-20

### Fixed

- Declare Home Assistant 2025.3.0 as the minimum version in HACS because
  earlier versions lack the entity setup callback used by the integration.

## [0.3.2] - 2026-11-19

### Changed

- Distribute the integration as a dedicated ZIP release asset for HACS and
  Home Assistant Marketplace installations and updates.

## [0.3.1] - 2026-11-17

### Added

- Expose humidity and CO2/CO2-equivalent readings and the sensor-controlled
  Auto preset for ComfoAir 70 units with a detected sensor board (#16).
  A device owner confirmed readings and switching between Auto and manual
  levels against the official app on a unit with a VOC board. CS50-only
  sensor-status properties and unrelated capabilities remain unchanged.

## [0.3.0] - 2026-11-17

### Added

- Start and stop the configured summer ventilation interval, enable or disable
  the global function, and configure its 1–24 hour duration. Controls require
  all attached units to have a validated profile and report the capability (#2).
- Offer sensor-controlled Auto for ComfoSpot 50 units with a detected sensor
  board, and show humidity and CO2 readings when reported. VOC boards report
  a CO2 equivalent through the same CO2 entity.
- Show diagnostic availability indicators for the ComfoSpot 50 temperature,
  humidity and CO2 sensors.
- Include current and per-situation levels, sensor-mode availability, sensor
  readings and readings of unmapped sensor types in the diagnostics.
- Allow choosing the name shown for this Home Assistant instance during pairing.
- Configure each room's level for the app's situations (at home, asleep,
  away, frost protection) from Home Assistant.
- Show when a temporary change of a room ends, such as a level changed on the
  unit's control panel.
- Show the active situation and select the situation at home or away, which
  switches to the manual mode.
- Show when a room's running boost ends.
- Show the role of each ComfoSpot 50 in the summer ventilation (supply,
  exhaust, or both).
- Show when a ComfoSpot 50 runs in supply-only operation, switched on at the
  unit's control panel. The operation can only be read, not switched (#1).

### Changed

- Refresh temperatures, fan speeds and fault values every minute, and filter
  counters every 15 minutes. Newly discovered or returning units get a full
  property refresh.
- Reflect the current ventilation level reported by the unit, including
  changes made on its control panel, and sensor-controlled operation as Auto.
- Group fan speeds and filter runtime sensors with the primary device sensors.
- Use scheduled refreshes for missing read-only values instead of repeatedly
  polling for them; filter resets still require confirmed read-back.
- Create the extract-air temperature entity only after a usable value is
  reported. An unavailable entity left from an earlier version may need to be
  removed from Home Assistant's entity registry.

### Fixed

- Start a Home Assistant reauthentication flow when the ConnectBox certificate
  changes, with deliberate physical pairing to the same gateway. Update the
  existing entry without losing its device/entity customizations. Verify a
  known gateway address over TLS when UDP discovery is blocked (#15).
- Show translated validation messages for expected command refusals, including
  summer ventilation, missing sensor boards, filter reset and situation
  selection. Distinguish communication failures and unconfirmed changes (#15).
- Keep a running summer interval visible and stoppable when its global
  function is disabled in the official app.
- Retain valid telemetry across empty property refreshes and preserve core
  telemetry when an optional property request is rejected.
- Do not display temperature readings as valid when their sensors report
  that they are unavailable.

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

[0.3.3]: https://github.com/andyblenk/ha-zehnder-connectbox/compare/v0.3.2...v0.3.3
[0.3.2]: https://github.com/andyblenk/ha-zehnder-connectbox/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/andyblenk/ha-zehnder-connectbox/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/andyblenk/ha-zehnder-connectbox/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/andyblenk/ha-zehnder-connectbox/releases/tag/v0.2.0
