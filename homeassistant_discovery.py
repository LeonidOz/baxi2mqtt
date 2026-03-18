"""
Home Assistant MQTT Discovery.
Publishes discovery messages so entities appear automatically in Home Assistant.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from config_validator import AppConfig

# Topics must match app.py
BASE_TOPIC = "baxi/heating"


def _device_info(cfg: AppConfig) -> Dict[str, Any]:
    """Build device info block for discovery."""
    d = cfg.homeassistant.device
    return {
        "identifiers": ["baxi_connect_plus"],
        "name": d.name,
        "model": d.model,
        "manufacturer": d.manufacturer,
        "sw_version": d.sw_version,
    }


def climate_discovery_config(
    cfg: AppConfig,
    heating_id: int,
    base_topic: str = BASE_TOPIC,
    entity_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Build MQTT Discovery config for one heating zone (climate entity).
    """
    prefix = cfg.homeassistant.discovery_prefix
    uid = f"baxi_heating_{heating_id}"
    name = entity_name or f"Heating {heating_id}"

    return {
        "name": name,
        "unique_id": uid,
        "object_id": f"heating_{heating_id}",
        # Temperature
        "current_temperature_topic": f"{base_topic}/{heating_id}/current_temperature",
        "temperature_state_topic": f"{base_topic}/{heating_id}/target_temperature",
        "temperature_command_topic": f"{base_topic}/{heating_id}/set/target_temperature",
        "min_temp": 5.0,
        "max_temp": 35.0,
        "temp_step": 0.5,
        "temperature_unit": "C",
        # Mode: we publish 0/1, HA expects "off"/"heat"
        "mode_state_topic": f"{base_topic}/{heating_id}/current_heating_state",
        "mode_state_template": "{% if value == '1' %}heat{% else %}off{% endif %}",
        "mode_command_topic": f"{base_topic}/{heating_id}/set/target_heating_state",
        "modes": ["heat", "off"],
        # Payloads for mode command: HA sends "heat" or "off"
        "payload_off": "off",
        "payload_on": "heat",
        "device": _device_info(cfg),
        "availability_topic": f"{base_topic}/{heating_id}/status",
        "payload_available": "online",
        "payload_not_available": "offline",
    }


def discovery_topic(cfg: AppConfig, component: str, object_id: str) -> str:
    """Discovery topic: <prefix>/<component>/<node_id>/<object_id>/config."""
    prefix = cfg.homeassistant.discovery_prefix
    node_id = "baxi"
    return f"{prefix}/{component}/{node_id}/{object_id}/config"


def publish_climate_discovery(
    mqtt_client,
    cfg: AppConfig,
    heating_ids: List[int],
    names: Optional[Dict[int, str]] = None,
    qos: int = 1,
) -> None:
    """
    Publish MQTT Discovery messages for all heating zones.
    Call after MQTT connect and when heating_ids are known (and optionally when names are received).
    """
    if not cfg.homeassistant.enabled:
        return
    names = names or {}
    for hid in heating_ids:
        config = climate_discovery_config(
            cfg, hid, entity_name=names.get(hid)
        )
        topic = discovery_topic(cfg, "climate", f"heating_{hid}")
        payload = json.dumps(config, ensure_ascii=False)
        mqtt_client.publish(topic, payload, retain=True, qos=qos)
        logging.info("HA discovery: published climate config for heating_id=%s topic=%s", hid, topic)


def clear_climate_discovery(mqtt_client, cfg: AppConfig, heating_id: int, qos: int = 1) -> None:
    """Remove a retained climate discovery topic so Home Assistant drops the entity."""
    topic = discovery_topic(cfg, "climate", f"heating_{heating_id}")
    mqtt_client.publish(topic, "", retain=True, qos=qos)
    logging.info("HA discovery: cleared climate config for heating_id=%s topic=%s", heating_id, topic)


def publish_availability(mqtt_client, heating_id: int, available: bool, qos: int = 1) -> None:
    """Publish availability status for a heating zone (so HA can show online/offline)."""
    topic = f"{BASE_TOPIC}/{heating_id}/status"
    payload = "online" if available else "offline"
    mqtt_client.publish(topic, payload, retain=True, qos=qos)
