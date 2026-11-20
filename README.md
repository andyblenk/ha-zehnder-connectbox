# Zehnder ConnectBox for Home Assistant

Zehnder ConnectBox is an unofficial Home Assistant custom integration for
locally controlling supported ventilation units through a standard Zehnder
ConnectBox gateway.

> [!IMPORTANT]
> **This is a software-only integration.** It communicates directly with the
> existing ConnectBox over the local network. No ESP, replacement control
> board, additional bridge, wiring change, or other hardware modification is
> required.

The integration runs locally and does not contact a Zehnder cloud service or
send telemetry to the project maintainers. It also exposes more technical
information than the official app, including fan speeds, temperatures, filter
counters, radio signal strength, and device diagnostics.

![Zehnder ConnectBox and connected ComfoSpot 50 in Home Assistant](docs/images/connectbox-overview.png)

## Support and feedback

If this integration is useful to you, please consider supporting its continued
development through [☕ Buy Me a Coffee](https://buymeacoffee.com/andyblenk).

Suggestions and ideas for improvement are always welcome. Please use
[GitHub Issues](https://github.com/andyblenk/ha-zehnder-connectbox/issues) for
feedback, feature requests, and reproducible problems.

> [!NOTE]
> This is an independent community project. It is not affiliated with,
> endorsed by, sponsored by, or supported by Zehnder Group. Zehnder,
> ConnectBox, ComfoSpot, and ComfoAir are trade marks or product names of their
> respective owners and are used only to describe compatibility.

## Compatibility

This integration is for the **Zehnder ConnectBox CU-RF-ZMA**. It is not for the
separate ComfoConnect LAN C interface used by ComfoAir Q units. Home Assistant
already provides a
[ComfoConnect integration](https://www.home-assistant.io/integrations/comfoconnect/)
for that product family.

| Product | Status |
| --- | --- |
| Zehnder ConnectBox CU-RF-ZMA | Supported gateway |
| Zehnder ComfoSpot 50 | Confirmed on physical hardware |
| Zehnder ComfoAir 70 | Sensor readings and Auto/manual switching confirmed by a device owner with a VOC board; other capabilities need separate validation |
| Other devices connected to a ConnectBox | Detected, but not controlled until their profile is verified |

ComfoAir 70 feedback confirms humidity and CO2-equivalent readings and
switching between sensor-controlled Auto and manual levels against the
official app on an installation with a VOC board. A dedicated CO2 board and
other capabilities have not been validated by this test. Further feedback
is welcome through a
[device feedback issue](https://github.com/andyblenk/ha-zehnder-connectbox/issues/new?template=device_feedback.yml)
with sanitized integration diagnostics.

## What the integration provides

- Automatic local discovery, with manual address entry as a fallback
- Physical pairing with the existing ConnectBox
- One Home Assistant gateway device for each ConnectBox
- Separate Home Assistant devices for supported connected ventilation units
- Automatic detection of supported units linked later in the official app
- Native fan, switch, select, number, sensor, binary-sensor, and button
  entities
- The functions of the official app's unit pages: boost, summer ventilation
  and unit settings
- Local polling, automatic reconnect, and privacy-preserving diagnostics
- Multiple ConnectBoxes as separate Home Assistant config entries

The integration reads gateway state every 10 seconds, fan speeds, temperatures,
and fault values every minute, and filter counters every 15 minutes. If a
device value is not yet available, it is checked again during the next
scheduled refresh.

The official Zehnder app is still used to commission the system and link
ventilation units to the ConnectBox. It does not need to remain open for this
integration to operate.

## Supported entities

### ConnectBox gateway

| Entity | Home Assistant type | Function |
| --- | --- | --- |
| Central ventilation | Switch | Places the complete connected system in standby or restores the last active mode |
| Operating mode | Select | Automatic, manual, antifreeze, or off |
| Situation | Select | Active situation of the official app; selecting At home or Away switches to the manual mode |
| Summer ventilation | Switch | Starts or stops a summer interval for capable attached units |
| Enable summer ventilation | Switch | Enables or disables the ConnectBox summer function |
| Summer ventilation duration | Number | Sets the duration of future intervals to 1–24 hours |

Summer ventilation is a ConnectBox-wide command, not a separate control on
each ventilation unit. The switch appears when every attached unit has a
validated profile and reports this capability. The function must be enabled
before an interval can be started. Its duration can be changed even while the
function is disabled. Configuration changes are unavailable during an active
interval. The start/stop switch becomes available in automatic or manual mode;
its attributes show the configured duration and reported end time. If the
function is disabled in the official app during an active interval, the
start/stop switch remains available so the running interval can be stopped.

The enable switch and duration number are configuration entities on the
ConnectBox gateway device; the start/stop switch remains a regular control.

While the situation is frost protection or a temporary change, the start/stop
switch is unavailable as well. Whether a unit supplies air, exhausts air, or
both during an interval is set per unit in the official app and shown by its
summer ventilation role sensor.

The ConnectBox firmware version is shown in its Home Assistant device
information when reported by the gateway.

Schedules, the absence planner, Wi-Fi setup, pairing and removing apps or
units, rooms, system settings, the history, push notifications, the firmware
update and the service partner access stay in the official app.

### Supported ventilation unit

| Entity | Home Assistant type | Function |
| --- | --- | --- |
| Ventilation | Fan | Levels 1–4 as 25–100%, named level presets, sensor-controlled Auto where a sensor board is detected, and off where supported |
| Extract air temperature | Sensor | Temperature of air extracted from the room; created when a usable value is reported |
| Incoming air temperature | Sensor | Temperature of incoming outdoor air |
| Humidity | Sensor | Relative humidity reported by the sensor board on ComfoSpot 50 and ComfoAir 70 |
| CO2 | Sensor | CO2 concentration reported by the sensor board in ppm, a CO2 equivalent on units with a VOC board (see [Sensor boards](#sensor-boards)); on ComfoSpot 50 and ComfoAir 70 units that report it |
| Extract air temperature sensor | Diagnostic binary sensor | Whether the extract-air temperature sensor is available |
| Incoming air temperature sensor | Diagnostic binary sensor | Whether the incoming-air temperature sensor is available |
| Humidity sensor | Diagnostic binary sensor | Whether the humidity sensor is available |
| CO2 sensor | Diagnostic binary sensor | Whether the CO2 sensor is available |
| Exhaust fan speed | Sensor | Exhaust fan speed in rpm |
| Supply fan speed | Sensor | Supply fan speed in rpm |
| Supply-only operation | Binary sensor | On while the unit runs in supply-only operation with its exhaust fan switched off; only on ComfoSpot 50 units that report it |
| Filter runtime | Sensor | Days elapsed since the last filter reset |
| Remaining filter runtime | Sensor | Days remaining before filter maintenance is due |
| Maximum filter runtime | Diagnostic sensor | Configured filter maintenance interval in days |
| Filter replacement required | Diagnostic binary sensor | Indicates that filter maintenance is due |
| Fault | Diagnostic binary sensor | Indicates a currently reported device fault |
| Signal strength | Diagnostic sensor | Radio signal strength between the unit and ConnectBox |
| Level at home, asleep, away, frost protection | Configuration select | Configured level of the unit's room for each situation of the official app |
| Temporary change until | Sensor | End of a temporary change, such as a level changed on the unit's control panel; unknown while none is active |
| Boost until | Sensor | End of a running boost; unknown while none is running |
| Summer ventilation role | Configuration select and diagnostic sensor | Whether the unit supplies, exhausts, or supplies and exhausts air during the summer ventilation, as on the unit's page in the app; created when a ComfoSpot 50 reports it |
| Fan status | Sensor | What the unit does right now (central off, standby, level 1–4, sensor mode, boost, summer ventilation), with level, situation, fan speeds, end times and fault codes as attributes |
| Boost | Switch | Starts a boost of the unit's room for its duration or ends it early, like the app's Boost button; a boost started in the app is shown |
| Boost duration | Configuration number | Duration of the room's boost, 15–120 minutes in steps of 15 |
| Level 1 runtime per hour | Configuration number | Minutes per hour the unit runs in level 1 (15, 30, 45 or 60), as on the unit's page in the app |
| Bath function, Automatic operation sensors | Diagnostic sensors | Settings the app shows read-only on the unit's page |
| Error log | Diagnostic sensor | The unit's last error codes with their operating hours |
| Reset filter timer | Configuration button | Resets the filter counter after filter maintenance; currently verified for ComfoSpot 50 |

The ventilation fan entity provides Home Assistant's standard power and speed
controls. Levels 1–4 can be selected directly as named presets or set as
25–100% fan speed. It shows the level the unit currently runs at, including a
level changed on the unit's own control panel.

Without an optional sensor board, the available presets are Level 1 through
Level 4:

<img src="docs/images/ventilation-dialog.png" alt="Ventilation level control with presets in Home Assistant" width="430">

When the unit reports humidity or CO2 readings, or a ComfoSpot 50 reports
an available humidity or CO2 sensor, the additional
**Auto (sensor)** preset is shown:

<img src="docs/images/ventilation-dialog-with-sensor.png" alt="Ventilation control with the Auto sensor preset in Home Assistant" width="430">

Firmware and valid hardware versions are displayed in the ventilation unit's
device information. Optional values are shown only when the connected product
actually reports them.

The four sensor-availability indicators are currently provided for the
physically tested ComfoSpot 50 profile. They show **Available** or **Not
available** when the ConnectBox reports a recognized status. A missing or
unrecognized status leaves the indicator unavailable. A temperature entity is
unavailable when its sensor reports that it is not available.

If an earlier version already created an extract-air temperature entity and it
remains unavailable after updating, remove the stale entity from Home
Assistant's entity registry under **Settings → Devices & services → Entities**.

Installer-only settings stay in the official app. The boost, its duration,
the level 1 runtime and the summer ventilation role were checked against the
official app on ComfoSpot 50 units and are offered only for that profile.

Supply-only operation is switched on and off at the unit itself: in levels 1–4,
touch (+) for about five seconds. Below an outdoor temperature of 13 °C, the
unit switches the exhaust fan back on. Home Assistant shows the operation but
cannot switch it. The ConnectBox confirms a write of the unit's exhaust-fan
flag, but the unit does not change.

<img src="docs/images/comfospot50-entities.png" alt="ComfoSpot 50 entities in Home Assistant" width="430">

See the [screenshot gallery](docs/screenshots.md) for pairing and device views.

### Sensor boards

Zehnder offers three optional sensor boards for the ComfoSpot 50: humidity,
humidity + CO2, and humidity + VOC. The ConnectBox reports the CO2 and the VOC
board in the same way: both send their reading in ppm under the CO2 sensor
type, and both report an available CO2 sensor. The integration therefore
cannot tell the two boards apart and shows both readings as **CO2**.

On a unit with a VOC board, this value is the board's CO2 equivalent, not a
measured CO2 concentration. Unlike a CO2 reading, it also rises with other
volatile compounds, for example alcohol vapour. This matches the
manufacturer's documentation, which controls both boards along the same ppm
curve. If you want the difference to be visible, rename the entity of such a
unit in Home Assistant, for example to "Air quality (VOC)".

This was verified on a ComfoSpot 50 with a humidity + VOC board, compared with
a unit with a humidity + CO2 board.

### Sensor-controlled operation (Auto)

ComfoSpot 50 and ComfoAir 70 units with a sensor board can
let the unit choose its fan level from its sensors. The ventilation entity
offers this as the `auto` preset only when a sensor board is detected:

- the unit reports a humidity or CO2 reading, including a VOC board's CO2
  equivalent, or
- a ComfoSpot 50 reports an available humidity or CO2 sensor, or
- the room already uses sensor-controlled operation in one of its situations,
  or as its current level, for example because it was selected in the app.

Units without any of these indications do not offer the preset. ComfoAir 70
sensor-status properties are not requested or interpreted.

CO2 and VOC boards cannot be distinguished from the reported CO2 sensor type.
On a VOC board, the reading is a CO2 equivalent rather than a measured CO2
concentration; both are displayed as **CO2**. A device owner confirmed the same
interpretation for a ComfoAir 70 with a VOC board against the official app.

The preset selects sensor-controlled operation for the room's currently active
situation, the same choice the official app offers instead of a fixed fan
level. While it is active, the unit chooses its own fan level, so Home
Assistant shows the preset but no percentage. Selecting a level or a
percentage returns the room to a fixed level.

The gateway's **Automatic** operating mode is a separate, system-wide setting
and does not select sensor-controlled operation.

```yaml
# Example: return to sensor control in the morning after a fixed night level
action: fan.set_preset_mode
target:
  entity_id: fan.comfospot_50_ventilation
data:
  preset_mode: auto
```

## Requirements

Before installing this integration:

1. The ventilation unit must have a compatible wireless module installed.
2. A Zehnder ConnectBox CU-RF-ZMA must be connected to the same local network
   as Home Assistant.
3. The official Zehnder Connect app must already be commissioned and working
   locally.
4. The ventilation unit and ConnectBox must already be linked in the app.

No additional controller, adapter, microcontroller, or cloud service is
required.

## Installation with HACS

The repository is not yet part of the default HACS catalogue. Install it as a
custom repository:

1. Open **HACS** in Home Assistant.
2. Open **Integrations**, then the three-dot menu.
3. Select **Custom repositories**.
4. Add `https://github.com/andyblenk/ha-zehnder-connectbox` with category
   **Integration**.
5. Download **Zehnder ConnectBox** and restart Home Assistant.

## Setup

1. Open **Settings -> Devices & services -> Add integration**.
2. Search for **Zehnder ConnectBox**.
3. Wait for the short local discovery scan and select the desired ConnectBox.
4. If discovery cannot cross your network boundary, choose manual setup and
   enter the ConnectBox address.
5. Confirm pairing. Optionally choose the name shown for this instance in the
   ConnectBox's paired applications; the default is **Home Assistant**.
6. Briefly press the access-key button on the ConnectBox while its indicator
   is blinking.

The pairing name is sent during physical pairing, including reauthentication.
Changing it for an existing entry requires pairing that instance again.

Home Assistant creates the gateway device and each recognized ventilation
unit behind it. If another supported unit is linked later in the Zehnder app,
it is added during a subsequent refresh. Assign Home Assistant areas normally;
the integration does not create duplicate room entities.

## Filter timer reset

Only use **Reset filter timer** after the filters have actually been cleaned or
replaced. The integration first acknowledges the filter-replacement state,
waits for the ConnectBox to confirm the write, resets the current runtime to
zero, and reads the value back.

Home Assistant button entities cannot require a confirmation dialog on their
device page. To protect this action on a dashboard, use a confirmation action
and replace the example entity ID with your own:

```yaml
type: button
entity: button.comfospot_50_reset_filter_timer
name: Reset filter timer
icon: mdi:air-filter
tap_action:
  action: perform-action
  perform_action: button.press
  target:
    entity_id: button.comfospot_50_reset_filter_timer
  confirmation:
    title: Reset filter timer?
    text: Confirm only after the filters have been cleaned or replaced.
    confirm_text: Reset
    dismiss_text: Cancel
```

Direct presses from the device page, Developer Tools, scripts, or automations
execute the reset without that dashboard confirmation.

## Troubleshooting and diagnostics

- Home Assistant and the ConnectBox must be able to reach each other on the
  local network. Client isolation and VLAN firewall rules can block discovery
  or control.
- If discovery fails but routing is available, use manual setup with the
  ConnectBox IP address.
- The gateway certificate is trusted during deliberate physical pairing and
  pinned for later connections. A certificate change is treated as a security
  error rather than being accepted silently. Home Assistant asks you to pair
  again. Continue only if you expect the change; confirm using the access-key
  button on the same ConnectBox. The existing entry, device and entity IDs,
  names, areas and disabled-entity settings are kept. The confirmation form
  also allows correcting the address. Only the same ConnectBox is accepted,
  including when UDP discovery is unavailable.
- Expected command refusals show a translated explanation in the interface
  and automation traces. Connection failures and changes not confirmed by the
  gateway have separate messages; check the actual device state before retrying.
- Download diagnostics from the integration entry before opening an issue.
  Diagnostics omit host addresses, names, serial numbers, UUIDs, certificate
  fingerprints, pairing material, and raw protocol frames.

For defects, questions, ideas, and device feedback, use
[GitHub Issues](https://github.com/andyblenk/ha-zehnder-connectbox/issues).
Please review every screenshot and diagnostic attachment for private data.

## Safety and responsibility

Install and operate this integration at your own risk. It is not supported by
the manufacturer. Keep the original controls available and verify correct
ventilation operation after installation and updates. The integration does
not replace required maintenance, safety checks, or filter changes.

## Versioning and releases

Releases follow [Semantic Versioning](https://semver.org/). HACS installs the
version published in a GitHub release. Pre-1.0 releases may still change as
additional hardware feedback is incorporated.

See the [changelog](CHANGELOG.md) for release details and upgrade notes.
Version 0.3.1 adds ComfoAir 70 sensor readings and sensor-controlled Auto,
confirmed by a device owner with a VOC board. It includes the 0.3.0 features:
ComfoSpot 50 sensor-controlled Auto and readings, summer ventilation, situation
controls and certificate-change recovery. Supply-only control and starting or
stopping a boost are not included in this release.

## License and acknowledgements

Copyright 2026 Andreas Blenk and contributors. Licensed under the
[Apache License 2.0](LICENSE).

The integration depends on `tlslite-ng` for a TLS profile scoped to the local
gateway connection. See [third-party notices](THIRD_PARTY_NOTICES.md). No
manufacturer application code, firmware, artwork, or network captures are
included in this repository.

Contributions and constructive feedback are welcome.
