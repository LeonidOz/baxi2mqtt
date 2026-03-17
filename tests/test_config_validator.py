"""
Test configuration validation.
"""

import pytest
import tempfile
import os
from config_validator import (
    AppConfig, BaxiConfig, MQTTConfig, LoggingConfig,
    HealthConfig, HomeAssistantConfig, ConfigError,
    LogLevel, MQTTQoSLevel
)


class TestBaxiConfig:
    """Test Baxi configuration validation."""
    
    def test_valid_config(self):
        """Test valid Baxi configuration."""
        config = BaxiConfig(
            ws_url="ws://192.168.1.100/ws",
            username="Пользователь",
            password="secret"
        )
        assert config.ws_url == "ws://192.168.1.100/ws"
        assert config.username == "Пользователь"
        assert config.password == "secret"
        assert config.timeout == 30
    
    def test_invalid_ws_url(self):
        """Test invalid WebSocket URL."""
        with pytest.raises(ValueError, match="WebSocket URL must start with"):
            BaxiConfig(
                ws_url="http://invalid.com",
                username="user"
            )
    
    def test_placeholder_ws_url(self):
        """Test placeholder WebSocket URL."""
        with pytest.raises(ValueError, match="placeholder"):
            BaxiConfig(
                ws_url="ws://BAXICONNECT-IP-OR-DOMAIN/ws",
                username="user"
            )
    
    def test_wss_url(self):
        """Test secure WebSocket URL."""
        config = BaxiConfig(
            ws_url="wss://secure.baxi.com/ws",
            username="user"
        )
        assert config.ws_url == "wss://secure.baxi.com/ws"


class TestMQTTConfig:
    """Test MQTT configuration validation."""
    
    def test_valid_config(self):
        """Test valid MQTT configuration."""
        config = MQTTConfig(
            host="192.168.1.10",
            port=1883,
            username="mqtt_user",
            password="mqtt_pass"
        )
        assert config.host == "192.168.1.10"
        assert config.port == 1883
        assert config.qos == MQTTQoSLevel.AT_LEAST_ONCE
    
    def test_invalid_port(self):
        """Test invalid port range."""
        with pytest.raises(ValueError):
            MQTTConfig(host="test", port=70000)
    
    def test_placeholder_host(self):
        """Test placeholder host."""
        with pytest.raises(ValueError, match="placeholder"):
            MQTTConfig(host="mqtt")
    
    def test_qos_levels(self):
        """Test QoS levels."""
        config = MQTTConfig(host="test", qos=MQTTQoSLevel.EXACTLY_ONCE)
        assert config.qos == 2


class TestLoggingConfig:
    """Test logging configuration validation."""
    
    def test_valid_config(self):
        """Test valid logging configuration."""
        config = LoggingConfig(
            level=LogLevel.DEBUG,
            format="%(message)s",
            file="/tmp/test.log"
        )
        assert config.level == LogLevel.DEBUG
        assert config.file == "/tmp/test.log"
    
    def test_console_logging(self):
        """Test console logging (no file)."""
        config = LoggingConfig(level=LogLevel.INFO)
        assert config.level == LogLevel.INFO
        assert config.file is None


class TestHealthConfig:
    """Test health check configuration."""
    
    def test_valid_config(self):
        """Test valid health configuration."""
        config = HealthConfig(port=8080, interval=30, timeout=10)
        assert config.port == 8080
        assert config.interval == 30
        assert config.timeout == 10
    
    def test_invalid_port(self):
        """Test invalid port range."""
        with pytest.raises(ValueError):
            HealthConfig(port=100)


class TestAppConfig:
    """Test complete application configuration."""
    
    def test_valid_complete_config(self):
        """Test valid complete configuration."""
        config_data = {
            "baxi": {
                "ws_url": "ws://192.168.1.100/ws",
                "username": "Пользователь",
                "password": "secret"
            },
            "mqtt": {
                "host": "192.168.1.10",
                "port": 1883,
                "username": "mqtt_user",
                "password": "mqtt_pass"
            },
            "logging": {
                "level": "INFO",
                "file": "/tmp/baxi.log"
            },
            "health": {
                "port": 8080
            },
            "homeassistant": {
                "enabled": True,
                "device": {
                    "name": "Test Baxi"
                }
            }
        }
        
        config = AppConfig(**config_data)
        assert config.baxi.ws_url == "ws://192.168.1.100/ws"
        assert config.mqtt.host == "192.168.1.10"
        assert config.logging.level == LogLevel.INFO
        assert config.health.port == 8080
        assert config.homeassistant.enabled is True
    
    def test_minimal_config(self):
        """Test minimal configuration with defaults."""
        config_data = {
            "baxi": {
                "ws_url": "ws://192.168.1.100/ws",
                "username": "user"
            },
            "mqtt": {
                "host": "192.168.1.10"
            }
        }
        
        config = AppConfig(**config_data)
        assert config.baxi.ws_url == "ws://192.168.1.100/ws"
        assert config.mqtt.host == "192.168.1.10"
        assert config.logging.level == LogLevel.INFO
        assert config.health.port == 8080
        assert config.homeassistant.enabled is True
    
    def test_auth_consistency_validation(self):
        """Test authentication consistency validation."""
        # This should work - both username and password provided
        config_data = {
            "baxi": {"ws_url": "ws://test.com/ws", "username": "user", "password": "pass"},
            "mqtt": {"host": "test", "username": "mqtt", "password": "mqtt_pass"}
        }
        AppConfig(**config_data)
        
        # This should work - neither username nor password provided
        config_data = {
            "baxi": {"ws_url": "ws://test.com/ws", "username": "user"},
            "mqtt": {"host": "test"}
        }
        AppConfig(**config_data)


class TestConfigFileLoading:
    """Test configuration file loading."""
    
    def test_load_valid_yaml(self):
        """Test loading valid YAML configuration."""
        config_yaml = """
baxi:
  ws_url: "ws://192.168.1.100/ws"
  username: "Пользователь"
  password: "secret"

mqtt:
  host: "192.168.1.10"
  port: 1883
  username: "mqtt_user"
  password: "mqtt_pass"

logging:
  level: "DEBUG"
  file: "/tmp/test.log"
"""
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False, encoding='utf-8') as f:
            f.write(config_yaml)
            f.flush()
            temp_path = f.name

        try:
            config = AppConfig.load_from_file(temp_path)
            assert config.baxi.ws_url == "ws://192.168.1.100/ws"
            assert config.mqtt.host == "192.168.1.10"
            assert config.logging.level == LogLevel.DEBUG
        finally:
            os.unlink(temp_path)
    
    def test_load_invalid_yaml(self):
        """Test loading invalid YAML configuration."""
        invalid_yaml = """
baxi:
  ws_url: "ws://test.com/ws"
  username: "user"
invalid_yaml: [
"""
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False, encoding='utf-8') as f:
            f.write(invalid_yaml)
            f.flush()
            temp_path = f.name

        try:
            with pytest.raises(ConfigError, match="Invalid YAML"):
                AppConfig.load_from_file(temp_path)
        finally:
            os.unlink(temp_path)
    
    def test_load_nonexistent_file(self):
        """Test loading nonexistent file."""
        with pytest.raises(ConfigError, match="not found"):
            AppConfig.load_from_file("/nonexistent/config.yaml")
    
    def test_load_empty_file(self):
        """Test loading empty configuration file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False, encoding='utf-8') as f:
            f.write("")
            f.flush()
            temp_path = f.name

        try:
            with pytest.raises(ConfigError, match="empty"):
                AppConfig.load_from_file(temp_path)
        finally:
            os.unlink(temp_path)


if __name__ == '__main__':
    # Run tests manually
    pytest.main([__file__, '-v'])
