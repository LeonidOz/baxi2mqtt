"""
Pytest fixtures and configuration for testing.
"""

import pytest
import tempfile
import os
from config_validator import AppConfig, BaxiConfig, MQTTConfig


@pytest.fixture
def valid_config_dict():
    """Valid configuration dictionary for testing."""
    return {
        "baxi": {
            "ws_url": "ws://192.168.1.100/ws",
            "username": "Пользователь",
            "password": "secret123"
        },
        "mqtt": {
            "host": "192.168.1.10",
            "port": 1883,
            "username": "mqtt_user",
            "password": "mqtt_pass",
            "client_id": "test_baxi2mqtt"
        },
        "logging": {
            "level": "INFO",
            "format": "%(asctime)s [%(levelname)s] %(message)s"
        },
        "health": {
            "port": 8080,
            "interval": 30,
            "timeout": 10
        },
        "homeassistant": {
            "enabled": True,
            "discovery_prefix": "homeassistant",
            "device": {
                "name": "Test Baxi",
                "model": "Connect+",
                "manufacturer": "Baxi"
            }
        }
    }


@pytest.fixture
def minimal_config_dict():
    """Minimal configuration dictionary for testing."""
    return {
        "baxi": {
            "ws_url": "ws://192.168.1.100/ws",
            "username": "user"
        },
        "mqtt": {
            "host": "192.168.1.10"
        }
    }


@pytest.fixture
def valid_config_file(valid_config_dict):
    """Create a temporary valid configuration file."""
    config_yaml = f"""
baxi:
  ws_url: "{valid_config_dict['baxi']['ws_url']}"
  username: "{valid_config_dict['baxi']['username']}"
  password: "{valid_config_dict['baxi']['password']}"

mqtt:
  host: "{valid_config_dict['mqtt']['host']}"
  port: {valid_config_dict['mqtt']['port']}
  username: "{valid_config_dict['mqtt']['username']}"
  password: "{valid_config_dict['mqtt']['password']}"
  client_id: "{valid_config_dict['mqtt']['client_id']}"

logging:
  level: "{valid_config_dict['logging']['level']}"
  format: "{valid_config_dict['logging']['format']}"

health:
  port: {valid_config_dict['health']['port']}
  interval: {valid_config_dict['health']['interval']}
  timeout: {valid_config_dict['health']['timeout']}

homeassistant:
  enabled: {str(valid_config_dict['homeassistant']['enabled']).lower()}
  discovery_prefix: "{valid_config_dict['homeassistant']['discovery_prefix']}"
  device:
    name: "{valid_config_dict['homeassistant']['device']['name']}"
    model: "{valid_config_dict['homeassistant']['device']['model']}"
    manufacturer: "{valid_config_dict['homeassistant']['device']['manufacturer']}"
"""
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False, encoding='utf-8') as f:
        f.write(config_yaml)
        f.flush()
        temp_path = f.name

    yield temp_path
    
    # Cleanup
    os.unlink(temp_path)


@pytest.fixture
def invalid_config_file():
    """Create a temporary invalid configuration file."""
    invalid_yaml = """
baxi:
  ws_url: "http://invalid.com"  # Invalid protocol
  username: "user"

mqtt:
  host: "mqtt"  # Placeholder host
"""
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False, encoding='utf-8') as f:
        f.write(invalid_yaml)
        f.flush()
        temp_path = f.name

    yield temp_path
    
    # Cleanup
    os.unlink(temp_path)


@pytest.fixture
def app_config(valid_config_dict):
    """Create AppConfig instance for testing."""
    return AppConfig(**valid_config_dict)


@pytest.fixture
def baxi_config():
    """Create BaxiConfig instance for testing."""
    return BaxiConfig(
        ws_url="ws://192.168.1.100/ws",
        username="Пользователь",
        password="secret"
    )


@pytest.fixture
def mqtt_config():
    """Create MQTTConfig instance for testing."""
    return MQTTConfig(
        host="192.168.1.10",
        port=1883,
        username="mqtt_user",
        password="mqtt_pass"
    )
