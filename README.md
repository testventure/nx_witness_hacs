# NX Witness Integration for Home Assistant

Simple integration to add NX Witness cameras to Home Assistant.

## Features

- Automatic camera discovery
- Live camera snapshots
- Video streaming support
- Dynamic event sensors from `/rest/v4/events/log`
- Enriched event attributes: `event_type`, `classification` (Person, Vehicle, Face), and `area` (zone/rule name)
- Simple username/password setup — no webhooks or server-side rules needed

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

## Event Sensor Attributes

Each camera gets a `binary_sensor` entity (e.g. `binary_sensor.camera_1_event`) that turns `on` when an event is detected within the last 30 seconds. The following attributes are available for use in automations and templates:

| Attribute | Example | Description |
|---|---|---|
| `camera_id` | `{uuid}` | Internal NX Witness camera ID |
| `event_type` | `intrusion` | Human-readable event type (e.g. `motion`, `intrusion`, `object_detected`) |
| `classification` | `Person` | Detected object type for analytics events (if available) |
| `area` | `Front Yard Intrusion` | Rule/zone name from NX Witness (if available) |
| `event_state` | `detected` | `detected` or `stopped` |
| `event_description` | `Person detected on zone A` | Description from the event (if available) |
| `last_detection` | `2026-03-02T10:00:00` | ISO timestamp of the last event |

### Example Automation

```yaml
automation:
  - alias: "Person detected on front door camera"
    trigger:
      - platform: state
        entity_id: binary_sensor.front_door_event
        to: "on"
    condition:
      - condition: template
        value_template: "{{ state_attr('binary_sensor.front_door_event', 'classification') == 'Person' }}"
    action:
      - service: notify.mobile_app
        data:
          message: "Person detected!"
```

## Changelog

### 0.3.1
- Split intrusion event attributes into three distinct fields: `event_type`, `classification`, and `area`
- `event_type` now reflects the actual NX Witness event type ID (e.g. `intrusion`, `motion`) rather than the zone caption
- `classification` replaces `object_class` (e.g. `Person`, `Vehicle`)
- New `area` attribute surfaces the rule/zone caption (e.g. `Front Yard Intrusion`)
- Removed `last_event_type` backwards-compatibility attribute

### 0.3.0
- Enriched event sensor attributes: `event_type`, `event_state`, `event_description`, `object_class`
- `event_type` is now a clean, human-readable string (e.g. `motion` instead of `nx.base.MotionEvent`)
- Analytics object detection (Person, Vehicle, Face, etc.) surfaced via `object_class`

### 0.2.2
- Initial release with camera discovery, streaming, and event sensors

## Version

Current version: 0.3.1

## License

MIT License
