import os

from config_validator import AppConfig, MQTTQoSLevel


def test_load_with_defaults_applies_extended_env_overrides(monkeypatch, valid_config_file):
    monkeypatch.setenv("MQTT_KEEPALIVE", "90")
    monkeypatch.setenv("MQTT_QOS", "2")
    monkeypatch.setenv("HEALTH_INTERVAL", "45")
    monkeypatch.setenv("HEALTH_TIMEOUT", "20")
    monkeypatch.setenv("HA_DISCOVERY_PREFIX", "ha-test")

    config = AppConfig.load_with_defaults(valid_config_file)

    assert config.mqtt.keepalive == 90
    assert config.mqtt.qos == MQTTQoSLevel.EXACTLY_ONCE
    assert config.health.interval == 45
    assert config.health.timeout == 20
    assert config.homeassistant.discovery_prefix == "ha-test"
