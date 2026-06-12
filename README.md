# Home Assistant integration for Spypoint Trail Cameras

Custom component integration for [Spypoint](https://www.spypoint.com) cellular trail cameras using the Spypoint cloud API.

## Installation

1. Copy this repository into your Home Assistant `custom_components` directory as `custom_components/spypoint`.
2. Restart Home Assistant.
3. Add the integration via **Settings → Devices & services → Add integration** and search for **Spypoint Trail Cameras**.

## Configuration

You will need your Spypoint account email and password. The integration connects to the Spypoint cloud API and discovers all cameras linked to your account.

## Features

- One device per trail camera with battery, temperature, signal, and photo plan sensors
- Latest photo image entity per camera, refreshed on each camera poll
- On-demand photo and video request buttons (cameras that support instant mode)
- Capture-at-next-sync button
- `spypoint.get_photos` service to fetch photo metadata on demand
- `spypoint.request_hdvideo` service to request an HD version of a photo/video

## Services

### `spypoint.get_photos`

Fetch photo metadata from the Spypoint API. Does not download image files.

| Field | Required | Description |
|-------|----------|-------------|
| `config_entry_id` | yes | Spypoint config entry to use |
| `device_id` | no | A single Spypoint camera device id; omit for all cameras |
| `date_start` | no | Start of date range (defaults to 7 days before `date_end`) |
| `date_end` | no | End of date range (defaults to now) |
| `limit` | no | Max photos to return (1–1000, default 20) |

Example:

```yaml
service: spypoint.get_photos
data:
  config_entry_id: "<your config entry id>"
  device_id: "<spypoint camera device id>"
  date_start: "2026-05-01T00:00:00"
  date_end: "2026-05-20T23:59:59"
  limit: 50
response_variable: photos
```

### `spypoint.request_hdvideo`

Request an HD version of a photo or video from the Spypoint API.

| Field | Required | Description |
|-------|----------|-------------|
| `config_entry_id` | yes | Spypoint config entry to use |
| `camera_id` | yes | Spypoint API camera id |
| `photo_id` | yes | Spypoint photo id |

Example:

```yaml
service: spypoint.request_hdvideo
data:
  config_entry_id: "<your config entry id>"
  camera_id: "6848483aecf1b7daf6841ef9"
  photo_id: "6a16f905bba401404d164b28"
response_variable: hdvideo
```

## API

This integration uses the unofficial Spypoint REST API at `https://restapi.spypoint.com/api/v3`. Spypoint does not publish a public API, so endpoints may change without notice.

## Development

This integration follows the same layout as the [hass2n](https://github.com/reedr/hass2n) custom component.
