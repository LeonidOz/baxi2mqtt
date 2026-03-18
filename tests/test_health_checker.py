"""
Tests for health checker module.
"""

import pytest
import asyncio
import json
from datetime import datetime, timezone, timedelta
from unittest.mock import Mock, AsyncMock, patch
from aiohttp import web
from health_checker import HealthChecker
from config_validator import AppConfig, HealthConfig


class TestHealthChecker:
    """Test HealthChecker functionality."""
    
    @pytest.fixture
    def config(self):
        """Create test configuration."""
        return AppConfig(
            baxi={
                "ws_url": "ws://192.168.1.100/ws",
                "username": "test_user"
            },
            mqtt={"host": "192.168.1.10"},
            health=HealthConfig(port=8080, interval=5, timeout=10)
        )
    
    @pytest.fixture
    def health_checker(self, config):
        """Create HealthChecker instance."""
        return HealthChecker(config)
    
    def test_init(self, health_checker):
        """Test HealthChecker initialization."""
        assert health_checker.config.health.port == 8080
        assert health_checker.start_time is not None
        assert health_checker.websocket_message_timeout == 20
        assert health_checker.websocket_connected is False
        assert health_checker.mqtt_connected is False
        assert health_checker.messages_received == 0
        assert health_checker.messages_sent == 0

    def test_init_with_custom_websocket_timeout(self, config):
        """Test custom WebSocket timeout can be provided by the daemon."""
        health_checker = HealthChecker(config, websocket_message_timeout=70)
        assert health_checker.websocket_message_timeout == 70
    
    def test_update_websocket_status(self, health_checker):
        """Test WebSocket status updates."""
        # Test connection
        health_checker.update_websocket_status(True)
        assert health_checker.websocket_connected is True
        
        # Test message
        health_checker.update_websocket_status(True, "test message")
        assert health_checker.websocket_connected is True
        assert health_checker.websocket_last_message is not None
        
        # Test disconnection
        health_checker.update_websocket_status(False)
        assert health_checker.websocket_connected is False
    
    def test_update_mqtt_status(self, health_checker):
        """Test MQTT status updates."""
        # Test connection
        health_checker.update_mqtt_status(True)
        assert health_checker.mqtt_connected is True
        assert health_checker.mqtt_last_message is not None
        
        # Test disconnection
        health_checker.update_mqtt_status(False)
        assert health_checker.mqtt_connected is False
    
    def test_message_recording(self, health_checker):
        """Test message recording."""
        # Test sent messages
        health_checker.record_message_sent()
        health_checker.record_message_sent()
        assert health_checker.messages_sent == 2
        
        # Test received messages
        health_checker.record_message_received()
        assert health_checker.messages_received == 1
    
    def test_error_recording(self, health_checker):
        """Test error recording."""
        error_msg = "Test error"
        health_checker.record_error(error_msg)
        
        assert health_checker.last_error == error_msg
        assert health_checker.last_error_time is not None
    
    def test_get_uptime(self, health_checker):
        """Test uptime formatting."""
        uptime = health_checker._get_uptime()
        assert isinstance(uptime, str)
        assert len(uptime) > 0
        
        # Should contain time units
        assert any(unit in uptime for unit in ['s', 'm', 'h', 'd'])
    
    def test_check_component_healthy(self, health_checker):
        """Test healthy component check."""
        now = datetime.now(timezone.utc)
        
        # Test connected with recent message
        status = health_checker._check_component_health(True, now, 60)
        assert status["status"] == "healthy"
        assert status["last_message"] == now.isoformat()
    
    def test_check_component_not_connected(self, health_checker):
        """Test component not connected."""
        status = health_checker._check_component_health(False, None, 60)
        assert status["status"] == "unhealthy"
        assert status["reason"] == "not_connected"
        assert status["last_message"] is None
    
    def test_check_component_timeout(self, health_checker):
        """Test component with message timeout."""
        old_time = datetime.now(timezone.utc) - timedelta(seconds=120)
        
        status = health_checker._check_component_health(True, old_time, 60)
        assert status["status"] == "unhealthy"
        assert status["reason"] == "message_timeout"
        assert status["seconds_since_message"] >= 120
    
    def test_get_health_status_healthy(self, health_checker):
        """Test overall healthy status."""
        # Set up healthy state
        now = datetime.now(timezone.utc)
        health_checker.update_websocket_status(True)
        health_checker.update_websocket_status(True, "test")
        health_checker.update_mqtt_status(True)
        health_checker.record_message_sent()
        health_checker.record_message_received()
        
        status = health_checker.get_health_status()
        
        assert status["status"] == "healthy"
        assert status["components"]["websocket"]["status"] == "healthy"
        assert status["components"]["mqtt"]["status"] == "healthy"
        assert status["statistics"]["messages_sent"] == 1
        assert status["statistics"]["messages_received"] == 1
        assert "system" in status
        assert "uptime" in status
    
    def test_get_health_status_unhealthy(self, health_checker):
        """Test overall unhealthy status."""
        # Set up unhealthy state
        health_checker.update_websocket_status(False)
        health_checker.update_mqtt_status(False)
        
        status = health_checker.get_health_status()
        
        assert status["status"] == "unhealthy"
        assert status["components"]["websocket"]["status"] == "unhealthy"
        assert status["components"]["mqtt"]["status"] == "unhealthy"

    def test_custom_websocket_timeout_avoids_false_positive_between_polls(self, config):
        """WebSocket health should stay healthy between normal polling cycles."""
        health_checker = HealthChecker(config, websocket_message_timeout=70)
        health_checker.update_websocket_status(True)
        health_checker.update_mqtt_status(True)
        health_checker.websocket_last_message = datetime.now(timezone.utc) - timedelta(seconds=55)

        status = health_checker.get_health_status()

        assert status["status"] == "healthy"
        assert status["components"]["websocket"]["status"] == "healthy"

    def test_custom_websocket_timeout_still_detects_real_stale_connection(self, config):
        """WebSocket health should turn unhealthy once the extended timeout is exceeded."""
        health_checker = HealthChecker(config, websocket_message_timeout=70)
        health_checker.update_websocket_status(True)
        health_checker.update_mqtt_status(True)
        health_checker.websocket_last_message = datetime.now(timezone.utc) - timedelta(seconds=80)

        status = health_checker.get_health_status()

        assert status["status"] == "unhealthy"
        assert status["components"]["websocket"]["status"] == "unhealthy"
        assert status["components"]["websocket"]["reason"] == "message_timeout"

    def test_should_not_log_unhealthy_during_startup_grace(self, health_checker):
        """Test unhealthy status is suppressed during startup grace period."""
        status = health_checker.get_health_status()
        assert health_checker.should_log_unhealthy_status(status) is False

    def test_should_log_unhealthy_after_startup_grace(self, health_checker):
        """Test unhealthy status is logged after startup grace period expires."""
        health_checker.start_time = datetime.now(timezone.utc) - timedelta(
            seconds=health_checker.startup_grace_seconds + 1
        )
        status = health_checker.get_health_status()
        assert health_checker.should_log_unhealthy_status(status) is True
    
    @pytest.mark.asyncio
    async def test_health_handler(self, health_checker):
        """Test health check HTTP handler."""
        # Set up test state
        health_checker.update_websocket_status(True)
        health_checker.update_websocket_status(True, "test")
        health_checker.update_mqtt_status(True)
        
        # Test the handler directly
        mock_request = Mock()
        mock_request.headers = {}
        
        response = await health_checker._health_handler(mock_request)
        
        assert response.status == 200
        data = json.loads(response.text)
        assert data["status"] == "healthy"
        assert "timestamp" in data
        assert "components" in data
        assert "statistics" in data
    
    @pytest.mark.asyncio
    async def test_ready_handler_healthy(self, health_checker):
        """Test ready handler for healthy state."""
        # Set up healthy state
        health_checker.update_websocket_status(True)
        health_checker.update_mqtt_status(True)
        
        # Test the handler directly
        mock_request = Mock()
        mock_request.headers = {}
        
        response = await health_checker._ready_handler(mock_request)
        
        assert response.status == 200
        data = json.loads(response.text)
        assert data["ready"] is True
        assert data["status"] == "healthy"
    
    @pytest.mark.asyncio
    async def test_ready_handler_unhealthy(self, health_checker):
        """Test ready handler for unhealthy state."""
        # Set up unhealthy state
        health_checker.update_websocket_status(False)
        health_checker.update_mqtt_status(False)
        
        # Test the handler directly
        mock_request = Mock()
        mock_request.headers = {}
        
        response = await health_checker._ready_handler(mock_request)
        
        assert response.status == 503
        data = json.loads(response.text)
        assert data["ready"] is False
        assert data["status"] == "unhealthy"
    
    @pytest.mark.asyncio
    async def test_live_handler(self, health_checker):
        """Test liveness handler."""
        # Test the handler directly
        mock_request = Mock()
        mock_request.headers = {}
        
        response = await health_checker._live_handler(mock_request)
        
        assert response.status == 200
        data = json.loads(response.text)
        assert data["alive"] is True
        assert "timestamp" in data
        assert "uptime" in data
    
    @pytest.mark.asyncio
    async def test_metrics_handler(self, health_checker):
        """Test metrics handler."""
        # Set up test data
        health_checker.record_message_sent()
        health_checker.record_message_received()
        health_checker.update_websocket_status(True)
        health_checker.update_mqtt_status(True)
        
        # Test the handler directly
        mock_request = Mock()
        mock_request.headers = {}
        
        response = await health_checker._metrics_handler(mock_request)
        
        assert response.status == 200
        metrics_text = response.text
        
        # Check for Prometheus format
        assert "HELP" in metrics_text
        assert "TYPE" in metrics_text
        assert "baxi2mqtt_messages_sent_total 1" in metrics_text
        assert "baxi2mqtt_messages_received_total 1" in metrics_text
        assert "baxi2mqtt_websocket_connected 1" in metrics_text
        assert "baxi2mqtt_mqtt_connected 1" in metrics_text
    
    @pytest.mark.asyncio
    async def test_start_health_server(self, health_checker):
        """Test starting health server."""
        # This should start without error
        await health_checker.start_health_server()
        
        # Verify server was started
        assert health_checker.app is not None
        assert health_checker.runner is not None
        assert health_checker.site is not None
        
        # Cleanup
        await health_checker.stop_health_server()
    
    @pytest.mark.asyncio
    async def test_start_monitoring(self, health_checker):
        """Test starting monitoring."""
        # Mock the start_health_server to avoid actual server
        with patch.object(health_checker, 'start_health_server', new_callable=AsyncMock):
            with patch.object(health_checker, 'health_monitoring_loop', new_callable=AsyncMock):
                await health_checker.start_monitoring()
                
                # Verify methods were called
                health_checker.start_health_server.assert_called_once()
                health_checker.health_monitoring_loop.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_cors_middleware(self, health_checker):
        """Test CORS middleware functionality."""
        # Create a mock handler
        mock_handler = AsyncMock(return_value=web.Response(text="test"))
        
        # Create mock request
        mock_request = Mock()
        mock_request.headers = {}
        
        # Test middleware
        response = await health_checker.cors_middleware(mock_request, mock_handler)
        
        # Verify CORS headers
        mock_handler.assert_called_once()
        assert response.headers['Access-Control-Allow-Origin'] == '*'
        assert 'GET' in response.headers['Access-Control-Allow-Methods']
    
    def test_statistics_tracking(self, health_checker):
        """Test statistics tracking over time."""
        # Record multiple events
        for i in range(5):
            health_checker.record_message_sent()
        for i in range(3):
            health_checker.record_message_received()
        for i in range(2):
            health_checker.record_reconnection()
        
        status = health_checker.get_health_status()
        
        assert status["statistics"]["messages_sent"] == 5
        assert status["statistics"]["messages_received"] == 3
        assert status["statistics"]["reconnections"] == 2
    
    @patch('psutil.virtual_memory')
    @patch('psutil.disk_usage')
    @patch('psutil.cpu_percent')
    def test_system_metrics(self, mock_cpu, mock_disk, mock_memory, health_checker):
        """Test system metrics collection."""
        # Mock system data
        mock_memory.return_value = Mock(
            percent=50.5,
            used=4 * 1024**3,  # 4GB
            total=8 * 1024**3   # 8GB
        )
        mock_disk.return_value = Mock(
            free=100 * 1024**3,  # 100GB
            total=500 * 1024**3   # 500GB
        )
        mock_cpu.return_value = 25.0
        
        status = health_checker.get_health_status()
        
        system = status["system"]
        assert system["memory_percent"] == 50.5
        assert system["memory_used_gb"] == 4.0
        assert system["memory_total_gb"] == 8.0
        assert system["disk_free_gb"] == 100.0
        assert system["disk_total_gb"] == 500.0
        assert system["cpu_percent"] == 25.0


class TestHealthCheckerIntegration:
    """Integration tests for health checker."""
    
    @pytest.mark.asyncio
    async def test_full_health_cycle(self):
        """Test complete health check cycle."""
        config = AppConfig(
            baxi={"ws_url": "ws://test.com/ws", "username": "test"},
            mqtt={"host": "test.com"},
            health=HealthConfig(port=8081, interval=5)
        )
        
        health_checker = HealthChecker(config)
        
        # Simulate application lifecycle
        health_checker.update_websocket_status(False)
        health_checker.update_mqtt_status(False)
        
        # Initial state should be unhealthy
        status = health_checker.get_health_status()
        assert status["status"] == "unhealthy"
        
        # Connect components
        health_checker.update_websocket_status(True)
        health_checker.update_websocket_status(True, "test")
        health_checker.update_mqtt_status(True)
        
        # Should be healthy now
        status = health_checker.get_health_status()
        assert status["status"] == "healthy"
        assert status["statistics"]["messages_sent"] == 0
        assert status["statistics"]["messages_received"] == 0
        
        # Record some activity
        health_checker.record_message_sent()
        health_checker.record_message_received()
        
        # Get updated status
        updated_status = health_checker.get_health_status()
        assert updated_status["statistics"]["messages_sent"] == 1
        assert updated_status["statistics"]["messages_received"] == 1
        
        # Test server start/stop
        await health_checker.start_health_server()
        await health_checker.stop_health_server()


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
