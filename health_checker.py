"""
Health check module for BaxiMQTT daemon.
Provides HTTP endpoint for monitoring application status.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from aiohttp import web
from aiohttp.web import Request, Response, middleware
import psutil
from config_validator import AppConfig


class HealthChecker:
    """Health monitoring and HTTP endpoint provider."""
    
    def __init__(self, config: AppConfig, websocket_message_timeout: Optional[int] = None):
        self.config = config
        self.start_time = datetime.now(timezone.utc)
        self.startup_grace_seconds = max(config.health.interval, config.health.timeout * 2)
        self.websocket_message_timeout = (
            websocket_message_timeout
            if websocket_message_timeout is not None
            else config.health.timeout * 2
        )
        
        # Component status tracking
        self.websocket_connected = False
        self.websocket_last_message = None
        self.mqtt_connected = False
        self.mqtt_last_message = None
        
        # Statistics
        self.messages_received = 0
        self.messages_sent = 0
        self.reconnections = 0
        self.last_error = None
        self.last_error_time = None
        
        # HTTP server
        self.app = None
        self.site = None
        self.runner = None
        
        logging.info(f"Health checker initialized on port {config.health.port}")
    
    def update_websocket_status(self, connected: bool, message: str = None):
        """Update WebSocket connection status."""
        self.websocket_connected = connected
        if message:
            self.websocket_last_message = datetime.now(timezone.utc)
    
    def update_mqtt_status(self, connected: bool):
        """Update MQTT connection status."""
        self.mqtt_connected = connected
        if connected:
            self.mqtt_last_message = datetime.now(timezone.utc)
    
    def record_message_sent(self):
        """Record that a message was sent."""
        self.messages_sent += 1
    
    def record_message_received(self):
        """Record that a message was received."""
        self.messages_received += 1
    
    def record_reconnection(self):
        """Record a reconnection event."""
        self.reconnections += 1
    
    def record_error(self, error: str):
        """Record an error event."""
        self.last_error = error
        self.last_error_time = datetime.now(timezone.utc)
        logging.error(f"Health check error recorded: {error}")
    
    def _get_uptime(self) -> str:
        """Get formatted uptime string."""
        uptime = datetime.now(timezone.utc) - self.start_time
        days = uptime.days
        hours, remainder = divmod(uptime.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        
        if days > 0:
            return f"{days}d {hours}h {minutes}m {seconds}s"
        elif hours > 0:
            return f"{hours}h {minutes}m {seconds}s"
        elif minutes > 0:
            return f"{minutes}m {seconds}s"
        else:
            return f"{seconds}s"
    
    def _check_component_health(
        self,
        connected: bool,
        last_message: Optional[datetime],
        timeout: int,
        require_recent_message: bool = True
    ) -> Dict[str, Any]:
        """Check health of a single component."""
        now = datetime.now(timezone.utc)
        
        if not connected:
            return {
                "status": "unhealthy",
                "reason": "not_connected",
                "last_message": last_message.isoformat() if last_message else None
            }
        
        if require_recent_message and last_message:
            time_since_message = (now - last_message).total_seconds()
            if time_since_message > timeout:
                return {
                    "status": "unhealthy",
                    "reason": "message_timeout",
                    "last_message": last_message.isoformat(),
                    "seconds_since_message": int(time_since_message)
                }
        
        return {
            "status": "healthy",
            "last_message": last_message.isoformat() if last_message else None
        }
    
    def get_health_status(self) -> Dict[str, Any]:
        """Get comprehensive health status."""
        now = datetime.now(timezone.utc)
        
        # Check component health
        ws_health = self._check_component_health(
            self.websocket_connected, 
            self.websocket_last_message, 
            self.websocket_message_timeout
        )
        
        mqtt_health = self._check_component_health(
            self.mqtt_connected,
            self.mqtt_last_message,
            self.config.health.timeout,
            require_recent_message=False
        )
        
        # Overall status
        component_statuses = [ws_health["status"], mqtt_health["status"]]
        overall_status = "healthy" if all(s == "healthy" for s in component_statuses) else "unhealthy"
        
        # System resources
        try:
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            cpu = psutil.cpu_percent(interval=1)
        except Exception:
            memory = disk = cpu = None
        
        status = {
            "status": overall_status,
            "timestamp": now.isoformat(),
            "uptime": self._get_uptime(),
            "start_time": self.start_time.isoformat(),
            "components": {
                "websocket": ws_health,
                "mqtt": mqtt_health
            },
            "statistics": {
                "messages_received": self.messages_received,
                "messages_sent": self.messages_sent,
                "reconnections": self.reconnections,
                "last_error": self.last_error,
                "last_error_time": self.last_error_time.isoformat() if self.last_error_time else None
            },
            "system": {
                "memory_percent": memory.percent if memory else None,
                "memory_used_gb": round(memory.used / (1024**3), 2) if memory else None,
                "memory_total_gb": round(memory.total / (1024**3), 2) if memory else None,
                "disk_free_gb": round(disk.free / (1024**3), 2) if disk else None,
                "disk_total_gb": round(disk.total / (1024**3), 2) if disk else None,
                "cpu_percent": cpu
            }
        }
        
        return status

    def should_log_unhealthy_status(self, status: Dict[str, Any], now: Optional[datetime] = None) -> bool:
        """Suppress noisy startup warnings while dependencies are still initializing."""
        if status["status"] != "unhealthy":
            return False

        now = now or datetime.now(timezone.utc)
        uptime = (now - self.start_time).total_seconds()
        return uptime >= self.startup_grace_seconds
    
    async def _health_handler(self, request: Request) -> Response:
        """Handle health check requests."""
        try:
            status = self.get_health_status()
            status_code = 200 if status["status"] == "healthy" else 503
            return web.json_response(status, status=status_code)
        except Exception as e:
            logging.error(f"Health check handler error: {e}")
            return web.json_response(
                {
                    "status": "error",
                    "error": str(e),
                    "timestamp": datetime.now(timezone.utc).isoformat()
                },
                status=500
            )
    
    async def _ready_handler(self, request: Request) -> Response:
        """Handle readiness check requests."""
        status = self.get_health_status()
        is_ready = (
            status["status"] == "healthy" and
            self.websocket_connected and
            self.mqtt_connected
        )
        
        return web.json_response(
            {
                "ready": is_ready,
                "status": status["status"],
                "timestamp": datetime.now(timezone.utc).isoformat()
            },
            status=200 if is_ready else 503
        )
    
    async def _live_handler(self, request: Request) -> Response:
        """Handle liveness check requests."""
        # Liveness just checks if the process is running
        return web.json_response(
            {
                "alive": True,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "uptime": self._get_uptime()
            },
            status=200
        )
    
    async def _metrics_handler(self, request: Request) -> Response:
        """Handle metrics requests in Prometheus format."""
        status = self.get_health_status()
        
        # Prometheus metrics format
        metrics = [
            f"# HELP baxi2mqtt_uptime_seconds Uptime in seconds",
            f"# TYPE baxi2mqtt_uptime_seconds gauge",
            f"baxi2mqtt_uptime_seconds {(datetime.now(timezone.utc) - self.start_time).total_seconds()}",
            "",
            f"# HELP baxi2mqtt_messages_received_total Total messages received",
            f"# TYPE baxi2mqtt_messages_received_total counter",
            f"baxi2mqtt_messages_received_total {status['statistics']['messages_received']}",
            "",
            f"# HELP baxi2mqtt_messages_sent_total Total messages sent",
            f"# TYPE baxi2mqtt_messages_sent_total counter",
            f"baxi2mqtt_messages_sent_total {status['statistics']['messages_sent']}",
            "",
            f"# HELP baxi2mqtt_reconnections_total Total reconnections",
            f"# TYPE baxi2mqtt_reconnections_total counter",
            f"baxi2mqtt_reconnections_total {status['statistics']['reconnections']}",
            "",
            f"# HELP baxi2mqtt_websocket_connected WebSocket connection status",
            f"# TYPE baxi2mqtt_websocket_connected gauge",
            f"baxi2mqtt_websocket_connected {1 if status['components']['websocket']['status'] == 'healthy' else 0}",
            "",
            f"# HELP baxi2mqtt_mqtt_connected MQTT connection status",
            f"# TYPE baxi2mqtt_mqtt_connected gauge",
            f"baxi2mqtt_mqtt_connected {1 if status['components']['mqtt']['status'] == 'healthy' else 0}",
        ]
        
        if status['system']['memory_percent'] is not None:
            metrics.extend([
                "",
                f"# HELP baxi2mqtt_memory_percent Memory usage percentage",
                f"# TYPE baxi2mqtt_memory_percent gauge",
                f"baxi2mqtt_memory_percent {status['system']['memory_percent']}"
            ])
        
        if status['system']['cpu_percent'] is not None:
            metrics.extend([
                "",
                f"# HELP baxi2mqtt_cpu_percent CPU usage percentage",
                f"# TYPE baxi2mqtt_cpu_percent gauge",
                f"baxi2mqtt_cpu_percent {status['system']['cpu_percent']}"
            ])
        
        return web.Response(text="\n".join(metrics), content_type="text/plain")
    
    @middleware
    async def cors_middleware(self, request: Request, handler):
        """CORS middleware for health check endpoints."""
        response = await handler(request)
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return response
    
    async def start_health_server(self):
        """Start the health check HTTP server."""
        try:
            # Create web application
            self.app = web.Application(middlewares=[self.cors_middleware])
            
            # Add routes
            self.app.router.add_get('/health', self._health_handler)
            self.app.router.add_get('/ready', self._ready_handler)
            self.app.router.add_get('/live', self._live_handler)
            self.app.router.add_get('/metrics', self._metrics_handler)
            
            # Create runner and site
            self.runner = web.AppRunner(self.app)
            await self.runner.setup()
            
            self.site = web.TCPSite(
                runner=self.runner,
                host='0.0.0.0',
                port=self.config.health.port
            )
            
            await self.site.start()
            logging.info(f"Health check server started on port {self.config.health.port}")
            logging.info(f"Health endpoints: http://localhost:{self.config.health.port}/health")
            logging.info(f"Metrics: http://localhost:{self.config.health.port}/metrics")
            
        except Exception as e:
            logging.error(f"Failed to start health server: {e}")
            raise
    
    async def stop_health_server(self):
        """Stop the health check HTTP server."""
        try:
            if self.site:
                await self.site.stop()
                logging.info("Health check server stopped")
            
            if self.runner:
                await self.runner.cleanup()
                
        except Exception as e:
            logging.error(f"Error stopping health server: {e}")
    
    async def health_monitoring_loop(self):
        """Background loop for periodic health monitoring."""
        while True:
            try:
                # Log periodic status
                status = self.get_health_status()
                if self.should_log_unhealthy_status(status):
                    logging.warning(f"Health check detected unhealthy status: {status}")
                
                await asyncio.sleep(self.config.health.interval)
                
            except Exception as e:
                logging.error(f"Health monitoring loop error: {e}")
                await asyncio.sleep(self.config.health.interval)
    
    async def start_monitoring(self):
        """Start health monitoring."""
        # Start HTTP server
        await self.start_health_server()
        
        # Start monitoring loop
        asyncio.create_task(self.health_monitoring_loop())
