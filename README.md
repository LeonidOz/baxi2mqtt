# BaxiMQTT

Baxi Connect+ to MQTT bridge with Home Assistant auto-discovery support.

## What It Does

- Connects to Baxi Connect+ over WebSocket
- Publishes heating state to MQTT
- Exposes Home Assistant climate entities through MQTT Discovery
- Provides `/health`, `/ready`, `/live`, and `/metrics` endpoints
- Runs cleanly in Docker Desktop / Docker Compose

## Quick Start

### Docker Compose

1. Create the main config:

```bash
cp config/config.example.yaml config/config.yaml
```

2. Adjust `config/config.yaml` for your Baxi device and MQTT broker.

3. Create local env overrides:

```bash
cp .env.example .env
```

At minimum, keep a unique `MQTT_CLIENT_ID` in `.env` if you run more than one instance against the same broker.

4. Build and start:

```bash
docker compose up -d --build
```

5. Check status:

```bash
docker compose ps
docker compose logs --tail=100
```

## Configuration Model

The app loads settings in this order:

1. `config/config.yaml`
2. Environment variables from the container

This means `.env` is a good place for machine-local overrides, while `config/config.yaml` stays as the shared base config.

## Useful `.env` Overrides

These are supported by the application:

- `MQTT_CLIENT_ID`
- `MQTT_HOST`
- `MQTT_PORT`
- `MQTT_USERNAME`
- `MQTT_PASSWORD`
- `MQTT_KEEPALIVE`
- `MQTT_QOS`
- `BAXI_WS_URL`
- `BAXI_USERNAME`
- `BAXI_PASSWORD`
- `BAXI_TIMEOUT`
- `LOG_LEVEL`
- `LOG_FILE`
- `HEALTH_PORT`
- `HEALTH_INTERVAL`
- `HEALTH_TIMEOUT`
- `HA_ENABLED`
- `HA_DISCOVERY_PREFIX`
- `TZ`

Recommended candidates for `.env`:

- `MQTT_CLIENT_ID`: machine-specific, avoids duplicate client disconnects
- `LOG_LEVEL`: useful per environment
- `HEALTH_PORT`: useful if `8080` is busy locally
- `MQTT_HOST` / `BAXI_WS_URL`: useful when the same repo is used against different devices
- `MQTT_USERNAME` / `MQTT_PASSWORD` / `BAXI_PASSWORD`: useful when you do not want secrets in the shared YAML

## Health Endpoints

The service exposes:

- `GET /health`: detailed component health
- `GET /ready`: readiness check
- `GET /live`: liveness check
- `GET /metrics`: Prometheus-style metrics

Docker health checks use `/live`, so temporary dependency startup delays do not flap container health.

## Home Assistant

Enable discovery in `config/config.yaml`:

```yaml
homeassistant:
  enabled: true
  discovery_prefix: "homeassistant"
```

## Development

### Local Python

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m pip install pytest pytest-asyncio pytest-cov
```

### Tests

```bash
.venv\Scripts\python.exe -m pytest -q
```

### Docker Build

```bash
docker compose build
```

## Notes

- If another instance is already connected to MQTT with the same `client_id`, the broker may disconnect one of them.
- A separate local `.env` is the easiest way to keep desktop and server instances from conflicting.

## License

MIT
