import asyncio
import json
import logging
import websockets
import paho.mqtt.client as mqtt
import re
import signal
from config_validator import AppConfig, ConfigError
from health_checker import HealthChecker
from homeassistant_discovery import publish_climate_discovery, publish_availability
from reconnection_manager import WebSocketReconnectionManager

def setup_logging(config: AppConfig):
    """Setup logging based on configuration."""
    log_config = config.logging
    
    # Configure logging
    if log_config.file:
        logging.basicConfig(
            level=getattr(logging, log_config.level.value),
            format=log_config.format,
            filename=log_config.file,
            filemode='a'
        )
        logging.info(f"Logging to file: {log_config.file}")
    else:
        logging.basicConfig(
            level=getattr(logging, log_config.level.value),
            format=log_config.format
        )
        logging.info("Console logging enabled")

BASE_TOPIC = "baxi/heating"
BAXI_CMD_BASE = 2730          # 23.5°C → 2965, 24.0°C → 2970
POLL_INTERVAL = 60            # секунд

HEATING_STATE_MAP = {
    "off": 0,
    "heat": 1
}


def _mqtt_reason_code_value(reason_code):
    raw_value = getattr(reason_code, "value", reason_code)
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        return raw_value


class BaxiMQTTDaemon:
    def __init__(self, cfg: AppConfig):
        self.cfg = cfg

        self.ws = None
        self.authenticated = False
        self.auth_sent = False

        self.heating_ids = []
        self.heating_names = {}
        self._availability_published = set()
        self._discovery_published_names = {}
        self.target_temps = {}

        # Health checker
        self.health_checker = HealthChecker(cfg)

        # Reconnection managers
        self.ws_reconnection_manager = WebSocketReconnectionManager(
            max_retries=10,
            base_delay=1.0,
            max_delay=60.0,
            connection_timeout=30.0
        )
        
        # MQTT
        self.mqtt = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=cfg.mqtt.client_id
        )
        if cfg.mqtt.username:
            self.mqtt.username_pw_set(
                cfg.mqtt.username,
                cfg.mqtt.password
            )

        self.loop = asyncio.get_running_loop()
        
        # Graceful shutdown
        self._shutdown_requested = False
    
    async def _ws_connect(self):
        """Attempt to establish WebSocket connection."""
        logging.info("WS connecting...")
        self.ws = await websockets.connect(
            self.cfg.baxi.ws_url,
            ping_interval=30,
            ping_timeout=10
        )
        self.health_checker.update_websocket_status(True)
        await self.ws_send({"req_ids": 16})
    
    async def ws_connect_with_retry(self):
        """Connect to WebSocket with reconnection management."""
        async def connect_websocket():
            try:
                await self._ws_connect()
                return True
            except Exception as e:
                return False
        
        return await self.ws_reconnection_manager.reconnect_with_backoff(
            connect_func=connect_websocket,
            should_stop=lambda: self._shutdown_requested
        )

    def publish_discovery_if_needed(self, heating_ids):
        """Publish HA discovery only when a zone is new or its advertised name changed."""
        if not self.cfg.homeassistant.enabled:
            return

        qos = getattr(self.cfg.mqtt.qos, "value", self.cfg.mqtt.qos)
        to_publish = []
        for hid in heating_ids:
            name = self.heating_names.get(hid)
            if hid not in self._discovery_published_names or self._discovery_published_names[hid] != name:
                to_publish.append(hid)

        if not to_publish:
            return

        publish_climate_discovery(
            self.mqtt, self.cfg, to_publish,
            names=self.heating_names,
            qos=qos,
        )
        for hid in to_publish:
            self._discovery_published_names[hid] = self.heating_names.get(hid)

    # ================= MQTT =================

    def mqtt_start(self):
        self.mqtt.on_connect = self.on_mqtt_connect
        self.mqtt.on_message = self.on_mqtt_message
        self.mqtt.on_disconnect = self.on_mqtt_disconnect
        self.mqtt.reconnect_delay_set(min_delay=1, max_delay=30)

        self.mqtt.connect(
            self.cfg.mqtt.host,
            self.cfg.mqtt.port,
            keepalive=self.cfg.mqtt.keepalive
        )
        self.mqtt.loop_start()

    def on_mqtt_connect(self, client, userdata, flags, reason_code, properties):
        code = _mqtt_reason_code_value(reason_code)
        logging.info(f"MQTT connected with code {code}")
        if getattr(reason_code, "is_failure", code != 0):
            self.health_checker.update_mqtt_status(False)
            logging.warning(f"MQTT connect reported non-zero return code: {code}")
            return

        self.health_checker.update_mqtt_status(True)
        client.subscribe(f"{BASE_TOPIC}/+/set/#", qos=self.cfg.mqtt.qos)

    def on_mqtt_disconnect(self, client, userdata, disconnect_flags, reason_code, properties):
        code = _mqtt_reason_code_value(reason_code)
        self.health_checker.update_mqtt_status(False)
        if self._shutdown_requested:
            logging.info("MQTT disconnected during shutdown")
            return

        if code == 7:
            logging.warning(
                "MQTT disconnected with code 7. This often means the broker dropped "
                "the session, for example because another client is using the same client_id."
            )
        else:
            logging.warning(f"MQTT disconnected with code {code}")
        self.health_checker.record_reconnection()

    def mqtt_pub(self, suffix, value):
        topic = f"{BASE_TOPIC}/{suffix}"
        result = self.mqtt.publish(topic, value, retain=True, qos=self.cfg.mqtt.qos)
        self.health_checker.record_message_sent()
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            self.health_checker.update_mqtt_status(False)
            logging.warning(f"MQTT publish failed for topic={topic}, rc={result.rc}")

    def on_mqtt_message(self, client, userdata, msg):
        payload = msg.payload.decode()
        topic = msg.topic
        logging.info(f"MQTT received: topic={topic}, payload={payload}")

        if not self.authenticated:
            return

        match = re.match(rf"{BASE_TOPIC}/(\d+)/set/(.+)", topic)
        if not match:
            return

        hid = int(match.group(1))
        prop = match.group(2)

        try:
            if prop == "target_temperature":
                target = float(payload)

                # защита от loop
                if self.target_temps.get(hid) == target:
                    return

                cmd = int(BAXI_CMD_BASE + target * 10)
                logging.info(f"WS CMD → set temperature {target}°C (cmd={cmd})")

                self.loop.call_soon_threadsafe(
                    lambda: asyncio.create_task(
                        self.ws_send({"id": hid, "cmd": cmd})
                    )
                )

            elif prop == "target_heating_state":
                # Home Assistant sends "heat"/"off"; also accept 1/0
                pl = payload.strip().lower()
                if pl in ("heat", "1"):
                    mode = "heat"
                elif pl in ("off", "0"):
                    mode = "off"
                else:
                    try:
                        mode = "heat" if int(payload) == 1 else "off"
                    except (ValueError, TypeError):
                        logging.warning(f"Invalid target_heating_state: {payload!r}")
                        return

                logging.info(f"WS CMD → set mode {mode}")
                self.loop.call_soon_threadsafe(
                    lambda: asyncio.create_task(
                        self.ws_send({"id": hid, "m": mode})
                    )
                )

        except Exception as e:
            logging.warning(f"MQTT command error: {e}")

    # ================= WebSocket =================

    async def ws_send(self, data):
        msg = json.dumps(data, ensure_ascii=False)
        logging.info(f"WS SEND >>> {msg}")
        await self.ws.send(msg)

    async def ws_loop(self):
        try:
            async for msg in self.ws:
                logging.info(f"WS RECV <<< {msg}")
                data = json.loads(msg)
                self.health_checker.update_websocket_status(True, msg)
                self.health_checker.record_message_received()

                # -------- AUTH --------
                if data.get("auth") == 401:
                    if not self.auth_sent:
                        await self.ws_send({
                            "user": self.cfg.baxi.username,
                            "pass": self.cfg.baxi.password
                        })
                        self.auth_sent = True
                    continue

                if data.get("auth") == 200:
                    self.authenticated = True
                    logging.info("Authenticated OK")
                    await self.ws_send({"req_ids": 16})
                    continue

                if not self.authenticated:
                    continue

                # -------- IDS --------
                if "ids" in data:
                    self.heating_ids = data["ids"]
                    logging.info(f"Heating IDs: {self.heating_ids}")

                    if self.cfg.homeassistant.enabled:
                        self.publish_discovery_if_needed(self.heating_ids)
                        for hid in self.heating_ids:
                            publish_availability(
                                self.mqtt, hid, True,
                                qos=getattr(self.cfg.mqtt.qos, "value", self.cfg.mqtt.qos),
                            )
                            self._availability_published.add(hid)

                    for hid in self.heating_ids:
                        await self.ws_send({"id": hid, "req_state": 0})
                    continue

                # -------- DATA --------
                if data.get("type") == 16 and "id" in data:
                    hid = data["id"]

                    if data.get("failed") == 1:
                        logging.warning(f"State request failed for heating zone {hid}")
                        if self.cfg.homeassistant.enabled:
                            publish_availability(
                                self.mqtt, hid, False,
                                qos=getattr(self.cfg.mqtt.qos, "value", self.cfg.mqtt.qos),
                            )
                            self._availability_published.discard(hid)
                        continue

                    if "c" in data:
                        self.mqtt_pub(f"{hid}/current_temperature", data["c"])

                    if "s" in data:
                        self.target_temps[hid] = data["s"]
                        self.mqtt_pub(f"{hid}/target_temperature", data["s"])

                    if "m" in data:
                        state = HEATING_STATE_MAP.get(data["m"], 0)
                        self.mqtt_pub(f"{hid}/current_heating_state", state)
                        self.mqtt_pub(f"{hid}/target_heating_state", state)

                    if "name" in data:
                        previous_name = self.heating_names.get(hid)
                        self.heating_names[hid] = data["name"]
                        if previous_name != data["name"]:
                            self.mqtt_pub(f"{hid}/name", data["name"])
                        if self.cfg.homeassistant.enabled:
                            self.publish_discovery_if_needed([hid])

                    if hid not in self._availability_published and self.cfg.homeassistant.enabled:
                        publish_availability(
                            self.mqtt, hid, True,
                            qos=getattr(self.cfg.mqtt.qos, "value", self.cfg.mqtt.qos),
                        )
                        self._availability_published.add(hid)

            self.health_checker.update_websocket_status(False)
            if not self._shutdown_requested:
                raise ConnectionError("WebSocket connection closed")

        except Exception as e:
            self.health_checker.update_websocket_status(False)
            self.health_checker.record_error(f"WebSocket loop error: {e}")
            raise

    # ================= POLLING =================

    async def poll_states(self):
        while True:
            if self.authenticated and self.heating_ids:
                logging.info("Polling Baxi state...")
                for hid in self.heating_ids:
                    await self.ws_send({"id": hid, "req_state": 0})
            await asyncio.sleep(POLL_INTERVAL)

    # ================= SUPERVISOR =================

    async def run(self):
        # Start health check server
        await self.health_checker.start_monitoring()
        
        self.mqtt_start()
        delay = 1

        # Setup signal handlers for graceful shutdown
        self._setup_signal_handlers()

        while not self._shutdown_requested:
            poll_task = None
            try:
                self.authenticated = False
                self.auth_sent = False
                self.heating_ids = []
                self.heating_names = {}
                self._availability_published = set()
                self._discovery_published_names = {}
                self.target_temps = {}

                # Connect WebSocket with reconnection management
                success = await self.ws_connect_with_retry()
                if not success:
                    logging.error("Failed to establish WebSocket connection after all retries")
                    break
                
                poll_task = asyncio.create_task(self.poll_states())
                delay = 1
                
                # Run WebSocket loop with reconnection management
                try:
                    await self.ws_loop()
                except Exception as e:
                    logging.warning(f"WS loop error: {e}")
                    self.health_checker.record_reconnection()
                    self.health_checker.record_error(f"WebSocket loop error: {e}")

            except Exception as e:
                logging.error(f"WS error: {e}")
                self.health_checker.record_reconnection()
                self.health_checker.record_error(f"WS error: {e}")

            finally:
                if poll_task:
                    poll_task.cancel()
                    # Handle disconnect event
                    self.ws_reconnection_manager.on_disconnect_event()

            if not self._shutdown_requested:
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)
        
        # Cleanup
        await self.health_checker.stop_health_server()
        logging.info("Application shutdown complete")
    
    def _setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown."""
        for sig in (signal.SIGTERM, signal.SIGINT):
            self.loop.add_signal_handler(
                sig, 
                lambda: asyncio.create_task(self._handle_shutdown())
            )
    
    async def _handle_shutdown(self):
        """Handle graceful shutdown."""
        logging.info("Shutdown requested, stopping gracefully...")
        self._shutdown_requested = True
        
        # Stop health server
        await self.health_checker.stop_health_server()
        
        # Stop MQTT
        if self.mqtt:
            self.mqtt.disconnect()
            self.mqtt.loop_stop()
            self.health_checker.update_mqtt_status(False)
        
        # Stop WebSocket
        if self.ws:
            await self.ws.close()
            self.health_checker.update_websocket_status(False)


# ================= MAIN =================

def main():
    async def _main():
        try:
            # Load and validate configuration
            cfg = AppConfig.load_with_defaults("config/config.yaml")
            
            # Setup logging based on configuration
            setup_logging(cfg)
            
            logging.info(f"Configuration loaded successfully")
            logging.info(f"Baxi URL: {cfg.baxi.ws_url}")
            logging.info(f"MQTT: {cfg.mqtt.host}:{cfg.mqtt.port}")
            logging.info(f"Health port: {cfg.health.port}")
            
            daemon = BaxiMQTTDaemon(cfg)
            await daemon.run()
            
        except ConfigError as e:
            print(f"❌ Configuration error: {e}")
            print("Please check your config/config.yaml file")
            return 1
        except Exception as e:
            print(f"❌ Startup error: {e}")
            return 1

    return asyncio.run(_main())


if __name__ == "__main__":
    main()
