# NX Witness Integration for Home Assistant

Simple integration to add NX Witness cameras to Home Assistant.

## Features

- Automatic camera discovery
- Live camera snapshots
- Video streaming support
- Dynamic event sensors from `/rest/v4/events/log`
- Simple username/password setup

## Installation

### Via HACS (Custom Repository)

1. Open HACS → Integrations
2. Click the three-dot menu → Custom repositories
3. Add: `https://github.com/msupczenski/nx_witness_hacs`
4. Category: Integration
5. Click "Download"
6. Restart Home Assistant

### Manual

1. Copy `custom_components/nx_witness` to your Home Assistant `custom_components` folder
2. Restart Home Assistant

## Configuration

1. Go to Settings → Devices & Services
2. Click "+ Add Integration"
3. Search for "NX Witness"
4. Enter:
   - **Host**: Must be in format `https://IP_ADDRESS:7001` (e.g., `https://192.168.1.100:7001`)
   - **Username**: Your NX Witness username
   - **Password**: Your NX Witness password
5. Click Submit

All cameras will be automatically discovered.

## Requirements

- NX Witness Server with REST API v4 support
- Home Assistant 2024.1.0+
- Network access from Home Assistant to NX Witness server

## Troubleshooting

**Cannot Connect Error:**
- Verify NX Witness server is running
- Check that credentials are correct
- Ensure port 7001 is accessible
- Host must be in format: `https://192.168.1.100:7001` (including https:// and :7001)

**Cameras Not Appearing:**
- Check Home Assistant logs
- Verify user has camera viewing permissions in NX Witness
- Reload the integration

## Version

Current version: 0.1.0 (Camera support only)

Coming soon:
- Additional event metadata

## License

MIT License
