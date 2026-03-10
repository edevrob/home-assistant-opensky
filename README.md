# OpenSky Home Assistant Integration

Custom Home Assistant integration for tracking nearby aircraft using the OpenSky Network REST API.

## What It Does

- Creates a sensor that shows how many flights are currently inside a configured area
- Tracks aircraft inside a radius around a selected latitude/longitude
- Optionally filters out flights above a configured altitude
- Fires Home Assistant events when flights enter or leave the tracked area

## Authentication

This version uses the OpenSky OAuth2 client credentials flow.

If OAuth credentials are configured:
- The integration requests a bearer token from OpenSky
- API calls are sent to `https://opensky-network.org/api/states/all`
- Data is refreshed every 90 seconds

If no OAuth credentials are configured:
- The integration still works in anonymous mode
- Data is refreshed every 15 minutes

Required credential fields:
- `client_id`
- `client_secret`

## How It Works

The integration requests `states/all` with a bounding box derived from:
- latitude
- longitude
- radius

Additional filtering is applied locally:
- ignore flights without callsign
- ignore flights with missing position
- ignore flights on the ground
- ignore flights above the configured altitude limit

## Repo Layout

- [`custom_components/opensky/api.py`](./custom_components/opensky/api.py): local OpenSky API client with OAuth token handling
- [`custom_components/opensky/config_flow.py`](./custom_components/opensky/config_flow.py): Home Assistant setup and options flow
- [`custom_components/opensky/coordinator.py`](./custom_components/opensky/coordinator.py): polling and flight boundary tracking
- [`custom_components/opensky/sensor.py`](./custom_components/opensky/sensor.py): flight count sensor entity
- [`custom_components/opensky/__init__.py`](./custom_components/opensky/__init__.py): integration setup entrypoint
