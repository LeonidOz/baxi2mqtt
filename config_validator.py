"""
Configuration validation for BaxiMQTT.
Uses Pydantic for robust configuration validation and type checking.
"""

import os
import yaml
from typing import Dict, Any, List, Optional
from enum import Enum
from pydantic import BaseModel, Field, field_validator, model_validator
from datetime import timedelta


class LogLevel(str, Enum):
    """Supported logging levels."""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class MQTTQoSLevel(int, Enum):
    """MQTT QoS levels."""
    AT_MOST_ONCE = 0
    AT_LEAST_ONCE = 1
    EXACTLY_ONCE = 2


class BaxiConfig(BaseModel):
    """Baxi Connect+ configuration."""
    ws_url: str = Field(..., description="WebSocket URL for Baxi Connect+")
    username: str = Field(..., min_length=1, description="Baxi username")
    password: str = Field(default="", description="Baxi password")
    timeout: int = Field(default=30, ge=1, le=300, description="Connection timeout in seconds")
    
    @field_validator('ws_url')
    @classmethod
    def validate_ws_url(cls, v):
        """Validate WebSocket URL format."""
        if not v.startswith(('ws://', 'wss://')):
            raise ValueError('WebSocket URL must start with ws:// or wss://')
        
        # Extract and validate hostname
        try:
            from urllib.parse import urlparse
            parsed = urlparse(v)
            if not parsed.hostname:
                raise ValueError('Invalid WebSocket URL: missing hostname')
            if parsed.hostname.lower() in ('baxiconnect-ip-or-domain', 'localhost'):
                raise ValueError('WebSocket URL contains placeholder - replace with actual IP or domain')
        except ValueError:
            # Re-raise our validation errors
            raise
        except Exception:
            raise ValueError('Invalid WebSocket URL format')
        
        return v


class MQTTConfig(BaseModel):
    """MQTT broker configuration."""
    host: str = Field(..., min_length=1, description="MQTT broker host")
    port: int = Field(default=1883, ge=1, le=65535, description="MQTT broker port")
    username: str = Field(default="", description="MQTT username")
    password: str = Field(default="", description="MQTT password")
    client_id: str = Field(default="baxi2mqtt", min_length=1, description="MQTT client ID")
    keepalive: int = Field(default=60, ge=10, le=300, description="MQTT keepalive interval")
    qos: MQTTQoSLevel = Field(default=MQTTQoSLevel.AT_LEAST_ONCE, description="MQTT QoS level")
    
    @field_validator('host')
    @classmethod
    def validate_host(cls, v):
        """Validate MQTT host."""
        if v in ('mqtt', 'localhost'):
            raise ValueError('MQTT host contains placeholder - replace with actual IP or domain')
        return v


class LoggingConfig(BaseModel):
    """Logging configuration."""
    level: LogLevel = Field(default=LogLevel.INFO, description="Logging level")
    format: str = Field(
        default="%(asctime)s [%(levelname)s] %(message)s",
        description="Log format"
    )
    file: Optional[str] = Field(default=None, description="Log file path (console only if None)")
    
    @field_validator('file')
    @classmethod
    def validate_log_file(cls, v):
        """Validate log file path."""
        if v:
            # Check if directory exists or can be created
            log_dir = os.path.dirname(v)
            if log_dir and not os.path.exists(log_dir):
                try:
                    os.makedirs(log_dir, exist_ok=True)
                except OSError as e:
                    raise ValueError(f'Cannot create log directory {log_dir}: {e}')
        return v


class HealthConfig(BaseModel):
    """Health check configuration."""
    port: int = Field(default=8080, ge=1024, le=65535, description="HTTP port for health check")
    interval: int = Field(default=30, ge=5, le=300, description="Health check interval in seconds")
    timeout: int = Field(default=10, ge=1, le=60, description="Component timeout in seconds")


class DeviceInfo(BaseModel):
    """Device information for Home Assistant."""
    name: str = Field(default="Baxi Connect+", description="Device name")
    model: str = Field(default="Connect+", description="Device model")
    manufacturer: str = Field(default="Baxi", description="Device manufacturer")
    sw_version: str = Field(default="1.0.0", description="Software version")


class HomeAssistantConfig(BaseModel):
    """Home Assistant auto-discovery configuration."""
    enabled: bool = Field(default=True, description="Enable Home Assistant auto-discovery")
    discovery_prefix: str = Field(default="homeassistant", description="MQTT discovery prefix")
    device: DeviceInfo = Field(default_factory=DeviceInfo, description="Device information")


class AppConfig(BaseModel):
    """Main application configuration."""
    baxi: BaxiConfig
    mqtt: MQTTConfig
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    health: HealthConfig = Field(default_factory=HealthConfig)
    homeassistant: HomeAssistantConfig = Field(default_factory=HomeAssistantConfig)
    
    @model_validator(mode='after')
    def validate_auth_consistency(self):
        """Validate authentication consistency."""
        if self.mqtt:
            # If username is provided, password should also be provided (or vice versa)
            has_mqtt_user = bool(self.mqtt.username)
            has_mqtt_pass = bool(self.mqtt.password)
            if has_mqtt_user != has_mqtt_pass:
                raise ValueError('MQTT username and password should both be provided or both empty')
        
        if self.baxi:
            # If Baxi password is provided, username should be provided (always true in our case)
            has_baxi_user = bool(self.baxi.username)
            has_baxi_pass = bool(self.baxi.password)
            if has_baxi_pass and not has_baxi_user:
                raise ValueError('Baxi password provided without username')
        
        return self
    
    @classmethod
    def load_from_file(cls, file_path: str) -> 'AppConfig':
        """
        Load configuration from YAML file.
        
        Args:
            file_path: Path to configuration file
            
        Returns:
            Validated AppConfig instance
            
        Raises:
            ConfigError: If configuration is invalid
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                config_data = yaml.safe_load(f)
            
            if not config_data:
                raise ValueError('Configuration file is empty')
            
            return cls(**config_data)
            
        except FileNotFoundError:
            raise ConfigError(f'Configuration file not found: {file_path}')
        except yaml.YAMLError as e:
            raise ConfigError(f'Invalid YAML in configuration file: {e}')
        except ValueError as e:
            raise ConfigError(f'Configuration validation failed: {e}')
        except Exception as e:
            raise ConfigError(f'Error loading configuration: {e}')
    
    @classmethod
    def load_with_defaults(cls, file_path: str) -> 'AppConfig':
        """
        Load configuration with environment variable overrides.
        
        Args:
            file_path: Path to configuration file
            
        Returns:
            AppConfig with env var overrides applied
        """
        # Load base configuration
        config = cls.load_from_file(file_path)
        
        # Override with environment variables
        env_mappings = {
            'BAXI_WS_URL': ('baxi', 'ws_url'),
            'BAXI_USERNAME': ('baxi', 'username'),
            'BAXI_PASSWORD': ('baxi', 'password'),
            'BAXI_TIMEOUT': ('baxi', 'timeout'),
            'MQTT_HOST': ('mqtt', 'host'),
            'MQTT_PORT': ('mqtt', 'port'),
            'MQTT_USERNAME': ('mqtt', 'username'),
            'MQTT_PASSWORD': ('mqtt', 'password'),
            'MQTT_CLIENT_ID': ('mqtt', 'client_id'),
            'MQTT_KEEPALIVE': ('mqtt', 'keepalive'),
            'MQTT_QOS': ('mqtt', 'qos'),
            'LOG_LEVEL': ('logging', 'level'),
            'LOG_FILE': ('logging', 'file'),
            'HEALTH_PORT': ('health', 'port'),
            'HEALTH_INTERVAL': ('health', 'interval'),
            'HEALTH_TIMEOUT': ('health', 'timeout'),
            'HA_ENABLED': ('homeassistant', 'enabled'),
            'HA_DISCOVERY_PREFIX': ('homeassistant', 'discovery_prefix'),
        }
        
        for env_var, (section, key) in env_mappings.items():
            value = os.getenv(env_var)
            if value is not None:
                # Convert string to appropriate type
                if key == 'port':
                    value = int(value)
                elif key == 'enabled':
                    value = value.lower() in ('true', '1', 'yes', 'on')
                elif key in ('timeout', 'interval', 'keepalive', 'qos'):
                    value = int(value)
                
                # Set the value
                config_dict = config.model_dump()
                config_dict[section][key] = value
                config = cls(**config_dict)
        
        return config


class ConfigError(Exception):
    """Configuration related errors."""
    pass


def validate_config_file(file_path: str) -> bool:
    """
    Validate configuration file without loading it.
    
    Args:
        file_path: Path to configuration file
        
    Returns:
        True if configuration is valid
        
    Raises:
        ConfigError: If configuration is invalid
    """
    try:
        AppConfig.load_from_file(file_path)
        return True
    except ConfigError:
        raise
    except Exception as e:
        raise ConfigError(f'Unexpected validation error: {e}')


if __name__ == '__main__':
    # Simple validation script
    import sys
    
    if len(sys.argv) != 2:
        print('Usage: python config_validator.py <config_file>')
        sys.exit(1)
    
    config_file = sys.argv[1]
    
    try:
        config = AppConfig.load_from_file(config_file)
        print(f'✅ Configuration is valid: {config_file}')
        print(f'📋 Configuration summary:')
        print(f'   Baxi URL: {config.baxi.ws_url}')
        print(f'   MQTT: {config.mqtt.host}:{config.mqtt.port}')
        print(f'   Health port: {config.health.port}')
        print(f'   HA discovery: {config.homeassistant.enabled}')
        
    except ConfigError as e:
        print(f'❌ Configuration error: {e}')
        sys.exit(1)
