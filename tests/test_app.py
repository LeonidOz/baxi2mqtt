import json
from unittest.mock import Mock

import pytest

import app
from config_validator import AppConfig


class _FakeWebSocket:
    def __init__(self, messages):
        self._messages = iter(messages)
        self.sent_messages = []

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._messages)
        except StopIteration:
            raise StopAsyncIteration

    async def send(self, message):
        self.sent_messages.append(message)


@pytest.mark.asyncio
async def test_ws_loop_continues_after_failed_zone(monkeypatch):
    cfg = AppConfig(
        baxi={"ws_url": "ws://192.168.1.100/ws", "username": "test_user"},
        mqtt={"host": "192.168.1.10"},
        homeassistant={"enabled": True},
    )

    mqtt_client = Mock()
    mqtt_client.publish.return_value.rc = app.mqtt.MQTT_ERR_SUCCESS
    monkeypatch.setattr(app.mqtt, "Client", Mock(return_value=mqtt_client))

    daemon = app.BaxiMQTTDaemon(cfg)
    daemon.authenticated = True
    daemon.heating_ids = [20494, 20603]
    daemon._availability_published = {20494, 20603}
    daemon.ws = _FakeWebSocket([
        json.dumps({"id": 20494, "req_state": 0, "failed": 1, "type": 16}),
        json.dumps({"c": 17.0, "m": "off", "id": 20603, "type": 16, "name": "DHW"}),
    ])

    publish_availability = Mock()
    monkeypatch.setattr(app, "publish_availability", publish_availability)

    with pytest.raises(ConnectionError, match="closed"):
        await daemon.ws_loop()

    publish_availability.assert_called_once_with(
        mqtt_client, 20494, False, qos=cfg.mqtt.qos.value
    )
    mqtt_client.publish.assert_any_call(
        "baxi/heating/20603/current_temperature", 17.0, retain=True, qos=cfg.mqtt.qos
    )
    assert 20494 not in daemon._availability_published


@pytest.mark.asyncio
async def test_ws_loop_suppresses_repeated_failed_zone_warning_until_recovery(monkeypatch):
    cfg = AppConfig(
        baxi={"ws_url": "ws://192.168.1.100/ws", "username": "test_user"},
        mqtt={"host": "192.168.1.10"},
        homeassistant={"enabled": True},
    )

    mqtt_client = Mock()
    mqtt_client.publish.return_value.rc = app.mqtt.MQTT_ERR_SUCCESS
    monkeypatch.setattr(app.mqtt, "Client", Mock(return_value=mqtt_client))

    daemon = app.BaxiMQTTDaemon(cfg)
    daemon.authenticated = True
    daemon.heating_ids = [20494]
    daemon._availability_published = {20494}
    daemon.ws = _FakeWebSocket([
        json.dumps({"id": 20494, "req_state": 0, "failed": 1, "type": 16}),
        json.dumps({"id": 20494, "req_state": 0, "failed": 1, "type": 16}),
        json.dumps({"c": 17.0, "m": "off", "id": 20494, "type": 16, "name": "Zone A"}),
    ])

    publish_availability = Mock()
    warning_log = Mock()
    info_log = Mock()
    monkeypatch.setattr(app, "publish_availability", publish_availability)
    monkeypatch.setattr(app.logging, "warning", warning_log)
    monkeypatch.setattr(app.logging, "info", info_log)

    with pytest.raises(ConnectionError, match="closed"):
        await daemon.ws_loop()

    publish_availability.assert_any_call(
        mqtt_client, 20494, False, qos=cfg.mqtt.qos.value
    )
    publish_availability.assert_any_call(
        mqtt_client, 20494, True, qos=cfg.mqtt.qos.value
    )
    assert publish_availability.call_count == 2
    warning_log.assert_any_call("State request failed for heating zone 20494")
    assert warning_log.call_count == 1
    info_log.assert_any_call("Heating zone 20494 is responding again")
    assert 20494 not in daemon._offline_heating_ids


@pytest.mark.asyncio
async def test_ws_loop_publishes_discovery_only_for_successful_zones(monkeypatch):
    cfg = AppConfig(
        baxi={"ws_url": "ws://192.168.1.100/ws", "username": "test_user"},
        mqtt={"host": "192.168.1.10"},
        homeassistant={"enabled": True},
    )

    mqtt_client = Mock()
    mqtt_client.publish.return_value.rc = app.mqtt.MQTT_ERR_SUCCESS
    monkeypatch.setattr(app.mqtt, "Client", Mock(return_value=mqtt_client))

    daemon = app.BaxiMQTTDaemon(cfg)
    daemon.authenticated = True
    daemon.ws = _FakeWebSocket([
        json.dumps({"ids": [20496, 20494, 20603]}),
        json.dumps({"c": 16.8, "m": "heat", "id": 20496, "type": 16, "name": "Heating"}),
        json.dumps({"id": 20494, "req_state": 0, "failed": 1, "type": 16}),
        json.dumps({"c": 17.0, "m": "off", "id": 20603, "type": 16, "name": "DHW"}),
    ])

    publish_discovery = Mock()
    clear_discovery = Mock()
    publish_availability = Mock()
    monkeypatch.setattr(app, "publish_climate_discovery", publish_discovery)
    monkeypatch.setattr(app, "clear_climate_discovery", clear_discovery)
    monkeypatch.setattr(app, "publish_availability", publish_availability)

    with pytest.raises(ConnectionError, match="closed"):
        await daemon.ws_loop()

    assert publish_discovery.call_count == 2
    publish_discovery.assert_any_call(
        mqtt_client, cfg, [20496], names=daemon.heating_names, qos=cfg.mqtt.qos.value
    )
    publish_discovery.assert_any_call(
        mqtt_client, cfg, [20603], names=daemon.heating_names, qos=cfg.mqtt.qos.value
    )
    clear_discovery.assert_called_once_with(
        mqtt_client, cfg, 20494, qos=cfg.mqtt.qos.value
    )
    publish_availability.assert_any_call(
        mqtt_client, 20603, True, qos=cfg.mqtt.qos.value
    )
    assert publish_availability.call_count == 2
    assert 20494 not in daemon._discovery_published_names


@pytest.mark.asyncio
async def test_ws_loop_updates_websocket_health_and_marks_disconnect(monkeypatch):
    cfg = AppConfig(
        baxi={"ws_url": "ws://192.168.1.100/ws", "username": "test_user"},
        mqtt={"host": "192.168.1.10"},
        homeassistant={"enabled": False},
    )

    mqtt_client = Mock()
    monkeypatch.setattr(app.mqtt, "Client", Mock(return_value=mqtt_client))

    daemon = app.BaxiMQTTDaemon(cfg)
    daemon.authenticated = True
    daemon.ws = _FakeWebSocket([
        json.dumps({"c": 17.0, "m": "off", "id": 20603, "type": 16, "name": "DHW"}),
    ])

    with pytest.raises(ConnectionError, match="closed"):
        await daemon.ws_loop()

    assert daemon.health_checker.messages_received == 1
    assert daemon.health_checker.websocket_last_message is not None
    assert daemon.health_checker.websocket_connected is False


@pytest.mark.asyncio
async def test_mqtt_disconnect_updates_health(monkeypatch):
    cfg = AppConfig(
        baxi={"ws_url": "ws://192.168.1.100/ws", "username": "test_user"},
        mqtt={"host": "192.168.1.10"},
    )

    mqtt_client = Mock()
    monkeypatch.setattr(app.mqtt, "Client", Mock(return_value=mqtt_client))

    daemon = app.BaxiMQTTDaemon(cfg)
    daemon.on_mqtt_connect(mqtt_client, None, None, 0, None)
    assert daemon.health_checker.mqtt_connected is True

    daemon.on_mqtt_disconnect(mqtt_client, None, None, 1, None)
    assert daemon.health_checker.mqtt_connected is False
    assert daemon.health_checker.reconnections == 1


def test_publish_discovery_if_needed_skips_duplicate_names(monkeypatch):
    cfg = AppConfig(
        baxi={"ws_url": "ws://192.168.1.100/ws", "username": "test_user"},
        mqtt={"host": "192.168.1.10"},
        homeassistant={"enabled": True},
    )

    mqtt_client = Mock()
    monkeypatch.setattr(app.mqtt, "Client", Mock(return_value=mqtt_client))
    monkeypatch.setattr(app.asyncio, "get_running_loop", Mock(return_value=Mock()))

    daemon = app.BaxiMQTTDaemon(cfg)
    daemon.heating_names = {20496: "Zone A"}

    publish_discovery = Mock()
    monkeypatch.setattr(app, "publish_climate_discovery", publish_discovery)

    daemon.publish_discovery_if_needed([20496])
    daemon.publish_discovery_if_needed([20496])

    publish_discovery.assert_called_once_with(
        mqtt_client, cfg, [20496], names=daemon.heating_names, qos=cfg.mqtt.qos.value
    )
