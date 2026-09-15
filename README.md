# Zehnder ConnectBox for Home Assistant

Zehnder ConnectBox is an unofficial local Home Assistant custom integration
for discovering and controlling supported ventilation units connected to a
Zehnder ConnectBox gateway.

> [!IMPORTANT]
> Zehnder ConnectBox is an unofficial, independent community project. It is
> not affiliated with, endorsed by, sponsored by, or supported by Zehnder
> Group. Zehnder, ConnectBox, ComfoSpot, and ComfoAir are trade marks or
> product names of their respective owners and are used only to describe
> compatibility.

The integration communicates directly with the ConnectBox on the local
network. It does not use a Zehnder cloud account or send telemetry to the
project maintainers.

## Compatibility

This project integrates the **Zehnder ConnectBox CU-RF-ZMA**. It does not
integrate the separate ComfoConnect LAN C interface used by ComfoAir Q units.
Home Assistant already provides a
[ComfoConnect integration](https://www.home-assistant.io/integrations/comfoconnect/)
for that product family.

| Product | Status |
| --- | --- |
| Zehnder ConnectBox CU-RF-ZMA | Supported gateway |
| Zehnder ComfoSpot 50 | Confirmed on physical hardware |
| Zehnder ComfoAir 70 | Provisional support; not yet verified on physical hardware |
| Other devices attached to a ConnectBox | Detected, but not controlled until their profile is verified |

If you own a ComfoAir 70, testing and sanitized diagnostics are especially
welcome. Please open a
[device feedback issue](https://github.com/andyblenk/ha-zehnder-connectbox/issues/new?template=device_feedback.yml).

## Features

- Short, bounded discovery scan when adding the integration
- Manual IP address or hostname fallback
- Local pairing with physical confirmation on the ConnectBox
- Multiple ConnectBoxes as separate Home Assistant config entries
- Connected ventilation units represented as devices behind their gateway
- Automatic addition of supported devices paired later in the Zehnder app
- Fan levels 1–4 and device-level off where level 0 is reported as supported
- Global ventilation standby and wake
- Automatic, manual, antifreeze, and off operating modes
- Extract-air and incoming-air temperatures
- Supply- and exhaust-fan speeds
- Filter runtime, remaining runtime, maximum runtime, and filter warning
- Device fault indicator
- Local polling, automatic reconnect, and sanitized diagnostics

The standby switch and operating mode belong to the ConnectBox and therefore
apply to the complete system. Fan-level entities belong to the respective
ventilation unit and retain the other room-mode values when a level is changed.

## Requirements

Before installing this integration:

1. The ventilation unit must have the compatible wireless module installed.
2. A Zehnder ConnectBox CU-RF-ZMA must be connected to the same local network
   as Home Assistant.
3. The Zehnder Connect app must already be commissioned and working locally.
4. The ventilation unit and ConnectBox must already be linked in the app.

The app remains the supported way to commission or add a ventilation unit.
This integration discovers changes from the ConnectBox automatically; there
is no separate Home Assistant "add device" flow.

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

1. Open **Settings → Devices & services → Add integration**.
2. Search for **Zehnder ConnectBox**.
3. Wait for the local discovery scan. Select the desired ConnectBox if more
   than one is found.
4. If discovery cannot cross your network boundary, choose manual setup and
   enter the ConnectBox address.
5. Confirm pairing and briefly press the access-key button on the ConnectBox
   while its indicator is blinking.

Home Assistant creates one gateway device and adds each recognised ventilation
unit behind it. If another unit is linked later in the Zehnder app, it is added
during a subsequent refresh. Home Assistant areas can be assigned normally;
the integration does not create room entities.

## Entities

| Device | Entity | Notes |
| --- | --- | --- |
| ConnectBox | Ventilation switch | Global standby/wake; wake restores the last known active mode |
| ConnectBox | Operating mode select | Automatic, manual, antifreeze, or off |
| Supported ventilation unit | Fan | Levels 1–4 shown as 25–100%; off uses level 0 when the unit reports support |
| Supported ventilation unit | Air temperature sensors | Extract and incoming air |
| Supported ventilation unit | Fan-speed sensors | Supply and exhaust RPM |
| Supported ventilation unit | Filter sensors | Runtime, remaining runtime, maximum runtime, and warning |
| Supported ventilation unit | Fault binary sensor | Reports a currently signalled unit fault |

Filter reset, firmware management, installer functions, and an unverified
temporary boost mode are deliberately not exposed.

## Screenshots

Screenshots will be added after the first Home Assistant UI validation. Before
publishing screenshots, remove IP addresses, serial numbers, UUIDs, locations,
and other private information.

## Troubleshooting and diagnostics

- Home Assistant and the ConnectBox must be able to reach each other on the
  local network. Client isolation and VLAN firewall rules can block discovery
  or control.
- If discovery fails but routing is available, use manual setup with the
  ConnectBox IP address.
- The gateway certificate is trusted during deliberate physical pairing and
  pinned for later connections. A certificate change is treated as a security
  error instead of being accepted silently.
- Download diagnostics from the integration entry before opening an issue.
  Diagnostics intentionally omit host addresses, names, serial numbers, UUIDs,
  certificate fingerprints, pairing material, and raw protocol frames.

For defects, use [GitHub Issues](https://github.com/andyblenk/ha-zehnder-connectbox/issues).
For questions, ideas, and broader device feedback, use
[GitHub Discussions](https://github.com/andyblenk/ha-zehnder-connectbox/discussions).
Please review every screenshot and diagnostic attachment for private data.

## Safety and responsibility

Install and operate this integration at your own risk. It is not supported by
the manufacturer. Keep the original controls available and verify correct
ventilation operation after installation and after updates. The integration
does not replace required maintenance, safety checks, or filter changes.

## Versioning and releases

Releases follow [Semantic Versioning](https://semver.org/). HACS installs the
version published in a GitHub release. Pre-1.0 releases may still change as
additional hardware feedback is incorporated.

## License and acknowledgements

Copyright 2026 Andreas Blenk and contributors. Licensed under the
[Apache License 2.0](LICENSE).

The integration depends on `tlslite-ng` for a TLS profile scoped to the local
gateway connection. See [third-party notices](THIRD_PARTY_NOTICES.md). No
manufacturer application code, firmware, artwork, or network captures are
included in this repository.

If this integration is useful to you, you can
[buy me a coffee](https://buymeacoffee.com/andyblenk). Contributions and
constructive feedback are welcome.
