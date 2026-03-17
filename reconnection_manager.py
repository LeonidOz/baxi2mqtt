"""
Reconnection manager with exponential backoff and jitter.
Provides reliable reconnection strategies for WebSocket and MQTT connections.
"""

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Optional, Callable, Dict, Any
from enum import Enum


class ReconnectionState(Enum):
    """Reconnection state enumeration."""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    FAILED = "failed"


class ReconnectionManager:
    """
    Manages reconnection attempts with exponential backoff and jitter.
    
    Features:
    - Exponential backoff with jitter to prevent thundering herd
    - Separate reconnection strategies for different connection types
    - Maximum retry limits with configurable backoff
    - Detailed reconnection statistics and tracking
    - Graceful degradation and failure handling
    """
    
    def __init__(
        self,
        name: str,
        max_retries: int = 10,
        base_delay: float = 1.0,
        max_delay: float = 300.0,
        jitter_factor: float = 0.1,
        connection_timeout: float = 30.0
    ):
        self.name = name
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.jitter_factor = jitter_factor
        self.connection_timeout = connection_timeout
        
        # Reconnection state
        self.state = ReconnectionState.DISCONNECTED
        self.retry_count = 0
        self.last_connection_time: Optional[datetime] = None
        self.last_error: Optional[str] = None
        self.last_error_time: Optional[datetime] = None
        
        # Statistics
        self.total_reconnections = 0
        self.successful_connections = 0
        self.total_errors = 0
        self.consecutive_failures = 0
        self.connection_uptime = timedelta(0)
        self.connection_start_time: Optional[datetime] = None
        
        # Callbacks
        self.on_connect: Optional[Callable] = None
        self.on_disconnect: Optional[Callable] = None
        self.on_error: Optional[Callable[[Exception], None]] = None
        
        logging.info(f"ReconnectionManager '{name}' initialized: max_retries={max_retries}, "
                     f"base_delay={base_delay}s, max_delay={max_delay}s")
    
    def set_callbacks(
        self,
        on_connect: Optional[Callable] = None,
        on_disconnect: Optional[Callable] = None,
        on_error: Optional[Callable[[Exception], None]] = None
    ):
        """Set connection event callbacks."""
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect
        self.on_error = on_error
    
    def calculate_delay(self) -> float:
        """
        Calculate exponential backoff delay with jitter.
        
        Formula: delay = base_delay * (2 ** retry_count)
        Adds jitter: delay = delay + (delay * jitter_factor * random.random())
        """
        delay = self.base_delay * (2 ** self.retry_count)
        delay = min(delay, self.max_delay)
        
        # Add jitter to prevent thundering herd
        jitter = delay * self.jitter_factor * random.random()
        final_delay = delay + jitter
        
        logging.debug(f"{self.name}: Calculated delay: {final_delay:.2f}s "
                     f"(base: {self.base_delay:.2f}s, retry: {self.retry_count}, "
                     f"jitter: {jitter:.2f}s)")
        
        return final_delay
    
    def should_retry(self) -> bool:
        """Check if reconnection should be attempted."""
        if self.state in [ReconnectionState.CONNECTED, ReconnectionState.CONNECTING]:
            return False
        
        if self.retry_count >= self.max_retries:
            logging.warning(f"{self.name}: Max reconnection attempts reached "
                           f"({self.max_retries})")
            return False
        
        # Add circuit breaker logic - if too many consecutive failures
        if self.consecutive_failures >= self.max_retries:
            logging.warning(f"{self.name}: Circuit breaker activated due to "
                           f"{self.consecutive_failures} consecutive failures")
            return False
        
        return True
    
    async def attempt_reconnection(self, connect_func: Callable) -> bool:
        """
        Attempt to establish a connection with the provided connect function.
        
        Args:
            connect_func: Async function that returns True on successful connection
            
        Returns:
            bool: True if connection successful, False otherwise
        """
        self.state = ReconnectionState.CONNECTING
        self.retry_count += 1
        
        try:
            logging.info(f"{self.name}: Connection attempt {self.retry_count}/{self.max_retries}")
            
            # Attempt connection with timeout
            connection_task = asyncio.create_task(connect_func())
            
            try:
                result = await asyncio.wait_for(connection_task, timeout=self.connection_timeout)
            except asyncio.TimeoutError:
                connection_task.cancel()
                raise ConnectionError(f"Connection timeout after {self.connection_timeout}s")

            if result is False:
                raise ConnectionError(f"{self.name} connection attempt returned False")
            
            # Connection successful
            self._on_connection_success()
            return True
            
        except Exception as e:
            self._on_connection_failure(e)
            return False
    
    def _on_connection_success(self):
        """Handle successful connection."""
        self.state = ReconnectionState.CONNECTED
        self.retry_count = 0
        self.consecutive_failures = 0
        self.last_connection_time = datetime.now(timezone.utc)
        self.connection_start_time = self.last_connection_time
        self.successful_connections += 1
        
        logging.info(f"{self.name}: Connection successful! "
                     f"Total connections: {self.successful_connections}")
        
        if self.on_connect:
            try:
                self.on_connect()
            except Exception as e:
                logging.error(f"{self.name}: Error in connect callback: {e}")
    
    def _on_connection_failure(self, error: Exception):
        """Handle connection failure."""
        self.state = ReconnectionState.FAILED
        self.last_error = str(error)
        self.last_error_time = datetime.now(timezone.utc)
        self.total_errors += 1
        self.consecutive_failures += 1
        
        # Update uptime if we were connected
        if self.connection_start_time:
            self.connection_uptime += datetime.now(timezone.utc) - self.connection_start_time
            self.connection_start_time = None
        
        logging.warning(f"{self.name}: Connection failed (attempt {self.retry_count}): {error}")
        
        if self.on_error:
            try:
                self.on_error(error)
            except Exception as e:
                logging.error(f"{self.name}: Error in error callback: {e}")
    
    def on_disconnect_event(self):
        """Handle disconnection event."""
        if self.state == ReconnectionState.CONNECTED:
            self.state = ReconnectionState.DISCONNECTED
            self.total_reconnections += 1
            
            # Update uptime
            if self.connection_start_time:
                self.connection_uptime += datetime.now(timezone.utc) - self.connection_start_time
                self.connection_start_time = None
            
            logging.info(f"{self.name}: Disconnected. Total reconnections: {self.total_reconnections}")
            
            if self.on_disconnect:
                try:
                    self.on_disconnect()
                except Exception as e:
                    logging.error(f"{self.name}: Error in disconnect callback: {e}")
    
    async def reconnect_with_backoff(
        self,
        connect_func: Callable,
        should_stop: Callable[[], bool] = lambda: False
    ) -> bool:
        """
        Execute reconnection with exponential backoff.
        
        Args:
            connect_func: Async function to establish connection
            should_stop: Function to check if reconnection should stop
            
        Returns:
            bool: True if connection successful, False if max retries exceeded
        """
        logging.info(f"{self.name}: Starting reconnection process")
        
        while self.should_retry() and not should_stop():
            delay = self.calculate_delay()
            
            if delay > 0:
                logging.info(f"{self.name}: Waiting {delay:.2f}s before next attempt")
                await asyncio.sleep(delay)
            
            # Check if we should stop before attempting connection
            if should_stop():
                logging.info(f"{self.name}: Reconnection stopped by external signal")
                break
            
            success = await self.attempt_reconnection(connect_func)
            if success:
                return True
        
        # Check if we should reset for future attempts
        if self.retry_count >= self.max_retries:
            self.state = ReconnectionState.FAILED
            logging.error(f"{self.name}: Reconnection failed after "
                         f"{self.max_retries} attempts. Circuit breaker activated.")
            return False
        else:
            # This allows external logic to reset and retry later
            logging.warning(f"{self.name}: Reconnection incomplete, "
                           f"retry_count={self.retry_count}")
            return False
    
    def reset(self):
        """Reset reconnection state for fresh start."""
        logging.info(f"{self.name}: Resetting reconnection state")
        
        self.state = ReconnectionState.DISCONNECTED
        self.retry_count = 0
        self.last_error = None
        self.last_error_time = None
        self.consecutive_failures = 0
        
        # Don't reset cumulative statistics
        # self.total_reconnections = 0
        # self.successful_connections = 0
        # self.total_errors = 0
        # self.connection_uptime = timedelta(0)
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get comprehensive reconnection statistics."""
        now = datetime.now(timezone.utc)
        current_uptime = timedelta(0)
        
        if self.connection_start_time:
            current_uptime = now - self.connection_start_time
        
        total_uptime = self.connection_uptime + current_uptime
        
        return {
            "name": self.name,
            "state": self.state.value,
            "retry_count": self.retry_count,
            "last_connection_time": self.last_connection_time.isoformat() if self.last_connection_time else None,
            "last_error": self.last_error,
            "last_error_time": self.last_error_time.isoformat() if self.last_error_time else None,
            "statistics": {
                "total_reconnections": self.total_reconnections,
                "successful_connections": self.successful_connections,
                "total_errors": self.total_errors,
                "consecutive_failures": self.consecutive_failures,
                "success_rate": (
                    self.successful_connections / (self.successful_connections + self.total_reconnections)
                    if (self.successful_connections + self.total_reconnections) > 0 else 0.0
                ),
                "uptime": {
                    "current": str(current_uptime),
                    "total": str(total_uptime),
                    "total_seconds": total_uptime.total_seconds()
                }
            },
            "config": {
                "max_retries": self.max_retries,
                "base_delay": self.base_delay,
                "max_delay": self.max_delay,
                "jitter_factor": self.jitter_factor,
                "connection_timeout": self.connection_timeout
            }
        }
    
    def is_healthy(self, timeout_minutes: int = 5) -> bool:
        """
        Check if the reconnection manager is in a healthy state.
        
        Args:
            timeout_minutes: Minutes to consider a connection stale
            
        Returns:
            bool: True if healthy, False otherwise
        """
        # Healthy if connected and recent
        if self.state == ReconnectionState.CONNECTED:
            if self.last_connection_time:
                time_since_connection = datetime.now(timezone.utc) - self.last_connection_time
                return time_since_connection < timedelta(minutes=timeout_minutes)
            return True
        
        # Also healthy if recently reconnected and not in failed state
        if self.state == ReconnectionState.DISCONNECTED:
            if self.last_connection_time:
                time_since_connection = datetime.now(timezone.utc) - self.last_connection_time
                return time_since_connection < timedelta(minutes=timeout_minutes)
        
        # Not healthy if failed or too many consecutive failures
        if self.state == ReconnectionState.FAILED:
            return False
        
        if self.consecutive_failures >= 3:
            return False
        
        return True


class WebSocketReconnectionManager(ReconnectionManager):
    """Specialized reconnection manager for WebSocket connections."""
    
    def __init__(
        self,
        max_retries: int = 10,
        base_delay: float = 1.0,
        max_delay: float = 300.0,
        connection_timeout: float = 30.0
    ):
        super().__init__(
            name="WebSocket",
            max_retries=max_retries,
            base_delay=base_delay,
            max_delay=max_delay,
            connection_timeout=connection_timeout
        )

