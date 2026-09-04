#!/usr/bin/env python3

import os
import time
import threading
from datetime import datetime

import paho.mqtt.client as mqtt
from pubsub import pub

from meshtastic.tcp_interface import TCPInterface
from meshtastic.protobuf import telemetry_pb2, portnums_pb2


# ---------------------------------------------------------
# Configuration
# ---------------------------------------------------------

MQTT_HOST = os.environ.get("WEATHERSTATION_MQTT_IP")
MQTT_PORT = int(os.environ.get("WEATHERSTATION_MQTT_PORT", "1883"))
MQTT_USER = os.environ.get("WEATHERSTATION_MQTT_USERNAME")
MQTT_PASSWORD = os.environ.get("WEATHERSTATION_MQTT_PASSWORD")
WEATHERSTATION_SENSOR_ID = os.environ.get("WEATHERSTATION_SENSOR_ID")

MQTT_BASE = (
    "homeassistant/sensor/"
    "Bresser-6in1/"
    f"{WEATHERSTATION_SENSOR_ID}/"
    "state"
)

MESH_HOST = os.environ.get(
    "MESH_HOST",
    "127.0.0.1",
)

MESH_PORT = int(
    os.environ.get(
        "MESH_PORT",
        "4405",
    )
)

SEND_INTERVAL = int(
    os.environ.get(
        "SEND_INTERVAL",
        "900",
    )
)

MAX_DATA_AGE = int(
    os.environ.get(
        "MAX_DATA_AGE",
        "180",
    )
)

MESH_RECONNECT_INTERVAL = int(
    os.environ.get(
        "MESH_RECONNECT_INTERVAL",
        "10",
    )
)


# ---------------------------------------------------------
# Runtime state
# ---------------------------------------------------------

weather = {}
last_update = {}

interval_stats = {
    "wind_gust_max": None,
}

weather_lock = threading.Lock()

stop_event = threading.Event()

mesh_iface = None
mesh_lock = threading.Lock()
mesh_reconnect_event = threading.Event()


# ---------------------------------------------------------
# Logging
# ---------------------------------------------------------

def log(message):
    print(
        f"[{datetime.now():%d-%m-%Y %H:%M:%S}] "
        f"{message}",
        flush=True,
    )


# ---------------------------------------------------------
# Configuration validation
# ---------------------------------------------------------

def validate_configuration():
    required = {
        "WEATHERSTATION_MQTT_IP": MQTT_HOST,
        "WEATHERSTATION_MQTT_USERNAME": MQTT_USER,
        "WEATHERSTATION_MQTT_PASSWORD": MQTT_PASSWORD,
        "WEATHERSTATION_SENSOR_ID": WEATHERSTATION_SENSOR_ID,
    }

    missing = [
        name
        for name, value in required.items()
        if not value
    ]

    if missing:
        log(
            "ERROR: Missing environment variables: "
            + ", ".join(missing)
        )
        return False

    return True


# ---------------------------------------------------------
# Mesh connection handling
# ---------------------------------------------------------

def get_mesh_interface():
    with mesh_lock:
        return mesh_iface


def close_mesh_interface():
    global mesh_iface

    with mesh_lock:
        iface = mesh_iface
        mesh_iface = None

    if iface is not None:
        try:
            iface.close()
        except Exception:
            pass


def connect_mesh_interface():
    global mesh_iface

    with mesh_lock:
        if mesh_iface is not None:
            return True

    try:
        log(
            "Connecting to Meshtastic "
            f"{MESH_HOST}:{MESH_PORT}"
        )

        iface = TCPInterface(
            hostname=MESH_HOST,
            portNumber=MESH_PORT,
        )

        with mesh_lock:
            mesh_iface = iface

        log(
            "Meshtastic connection established"
        )

        return True

    except Exception as e:
        log(
            f"Meshtastic connection failed: {e}"
        )

        with mesh_lock:
            mesh_iface = None

        return False


def request_mesh_reconnect(reason=None):
    if reason:
        log(
            f"Meshtastic reconnect requested: {reason}"
        )

    mesh_reconnect_event.set()


# ---------------------------------------------------------
# Catch exceptions from Meshtastic background threads
# ---------------------------------------------------------

_original_threading_excepthook = threading.excepthook


def mesh_thread_exception_handler(args):
    exc = args.exc_value

    connection_errors = (
        BrokenPipeError,
        ConnectionResetError,
        ConnectionAbortedError,
        ConnectionRefusedError,
        TimeoutError,
    )

    if isinstance(exc, connection_errors):
        log(
            "Meshtastic background connection error: "
            f"{exc}"
        )

        request_mesh_reconnect(
            type(exc).__name__
        )

        # Do not dump a huge traceback for a known/recoverable
        # TCP disconnect.
        return

    _original_threading_excepthook(args)


threading.excepthook = mesh_thread_exception_handler


# ---------------------------------------------------------
# Mesh watchdog / reconnect loop
# ---------------------------------------------------------

def mesh_watchdog_loop():
    while not stop_event.is_set():

        iface = get_mesh_interface()

        if iface is None or mesh_reconnect_event.is_set():

            if mesh_reconnect_event.is_set():
                log(
                    "Resetting Meshtastic connection"
                )

            close_mesh_interface()

            if connect_mesh_interface():
                mesh_reconnect_event.clear()

            else:
                if stop_event.wait(
                    MESH_RECONNECT_INTERVAL
                ):
                    break

                continue

        stop_event.wait(5)


# ---------------------------------------------------------
# Safe Meshtastic send wrapper
# ---------------------------------------------------------

def mesh_send(
    telemetry,
    destination_id,
    channel_index=0,
    reply_id=None,
):
    for attempt in range(2):

        iface = get_mesh_interface()

        if iface is None:
            if not connect_mesh_interface():
                request_mesh_reconnect(
                    "interface unavailable"
                )
                return False

            iface = get_mesh_interface()

        if iface is None:
            return False

        kwargs = {
            "destinationId": destination_id,
            "portNum": (
                portnums_pb2
                .PortNum
                .TELEMETRY_APP
            ),
            "wantAck": False,
            "channelIndex": channel_index,
        }

        if reply_id is not None:
            kwargs["replyId"] = reply_id

        try:
            iface.sendData(
                telemetry,
                **kwargs,
            )

            return True

        except Exception as e:
            log(
                f"Meshtastic send failed: {e}"
            )

            request_mesh_reconnect(
                f"send failure: {e}"
            )

            close_mesh_interface()

            if attempt == 0:
                log(
                    "Reconnecting and retrying send..."
                )

                time.sleep(2)

                if not connect_mesh_interface():
                    return False

    return False


# ---------------------------------------------------------
# MQTT callbacks
# ---------------------------------------------------------

def on_mqtt_connect(
    client,
    userdata,
    flags,
    reason_code,
    properties=None,
):
    log(
        f"MQTT connected: {reason_code}"
    )

    log(
        f"Subscribing to: {MQTT_BASE}/#"
    )

    client.subscribe(
        MQTT_BASE + "/#"
    )


def on_mqtt_disconnect(
    client,
    userdata,
    disconnect_flags,
    reason_code,
    properties=None,
):
    if not stop_event.is_set():
        log(
            f"MQTT disconnected: {reason_code}"
        )


def on_mqtt_message(
    client,
    userdata,
    msg,
):
    field = msg.topic.rsplit(
        "/",
        1,
    )[-1]

    try:
        raw_value = (
            msg.payload
            .decode()
            .strip()
        )

        numeric_fields = {
            "temperature_C",
            "humidity",
            "wind_max_m_s",
            "wind_avg_m_s",
            "wind_dir_deg",
            "rain_mm",
            "battery_ok",
        }

        if field in numeric_fields:
            value = float(
                raw_value
            )
        else:
            value = raw_value

        now = time.time()

        with weather_lock:
            weather[field] = value
            last_update[field] = now

            if field == "wind_max_m_s":

                current_max = interval_stats[
                    "wind_gust_max"
                ]

                if (
                    current_max is None
                    or value > current_max
                ):
                    interval_stats[
                        "wind_gust_max"
                    ] = value

    except Exception as e:
        log(
            f"MQTT parse error "
            f"topic={msg.topic}: {e}"
        )


# ---------------------------------------------------------
# Weather state
# ---------------------------------------------------------

def get_weather_snapshot():
    now = time.time()

    with weather_lock:
        data = weather.copy()
        updates = last_update.copy()

        gust_max = interval_stats[
            "wind_gust_max"
        ]

    required = [
        "temperature_C",
        "humidity",
        "wind_avg_m_s",
        "wind_max_m_s",
        "wind_dir_deg",
    ]

    missing = [
        field
        for field in required
        if field not in data
    ]

    if missing:
        log(
            "Weather unavailable - missing: "
            + ", ".join(missing)
        )
        return None

    stale = []

    for field in required:
        updated = updates.get(field)

        if (
            updated is None
            or now - updated > MAX_DATA_AGE
        ):
            stale.append(field)

    if stale:
        log(
            "Weather unavailable - stale: "
            + ", ".join(stale)
        )
        return None

    return data, gust_max


# ---------------------------------------------------------
# Build EnvironmentMetrics protobuf
# ---------------------------------------------------------

def build_environment_telemetry():
    snapshot = get_weather_snapshot()

    if snapshot is None:
        return None

    data, gust_max = snapshot

    telemetry = telemetry_pb2.Telemetry()

    env = telemetry.environment_metrics

    env.temperature = float(
        data["temperature_C"]
    )

    env.relative_humidity = float(
        data["humidity"]
    )

    env.wind_speed = float(
        data["wind_avg_m_s"]
    )

    if gust_max is not None:
        env.wind_gust = float(
            gust_max
        )
    else:
        env.wind_gust = float(
            data["wind_max_m_s"]
        )

    env.wind_direction = int(
        round(
            data["wind_dir_deg"]
        )
    )

    return telemetry


# ---------------------------------------------------------
# Periodic broadcast
# ---------------------------------------------------------

def send_periodic_weather():
    telemetry = (
        build_environment_telemetry()
    )

    if telemetry is None:
        return False

    env = telemetry.environment_metrics

    log(
        "Sending periodic weather telemetry"
    )

    print(
        f"  Temperature : "
        f"{env.temperature:.1f} C",
        flush=True,
    )

    print(
        f"  Humidity    : "
        f"{env.relative_humidity:.0f} %",
        flush=True,
    )

    print(
        f"  Wind speed  : "
        f"{env.wind_speed:.1f} m/s",
        flush=True,
    )

    print(
        f"  Gust max    : "
        f"{env.wind_gust:.1f} m/s",
        flush=True,
    )

    print(
        f"  Direction   : "
        f"{env.wind_direction} deg",
        flush=True,
    )

    if not mesh_send(
        telemetry,
        destination_id="^all",
        channel_index=0,
    ):
        log(
            "Periodic telemetry send failed"
        )

        # Do NOT reset wind_gust_max.
        # Nothing was successfully transmitted.
        return False

    log(
        "Periodic telemetry sent"
    )

    # Start a new gust measurement window only
    # after successful transmission.
    with weather_lock:
        interval_stats[
            "wind_gust_max"
        ] = None

    return True


# ---------------------------------------------------------
# On-demand Environment Metrics responder
# ---------------------------------------------------------

def on_meshtastic_receive(
    packet,
    interface,
):
    try:
        decoded = packet.get(
            "decoded",
            {},
        )

        portnum = decoded.get(
            "portnum"
        )

        if portnum not in (
            "TELEMETRY_APP",
            portnums_pb2.PortNum.TELEMETRY_APP,
        ):
            return

        want_response = decoded.get(
            "wantResponse",
            decoded.get(
                "want_response",
                False,
            ),
        )

        if not want_response:
            return

        request_id = packet.get(
            "id"
        )

        requester = packet.get(
            "from"
        )

        channel = packet.get(
            "channel",
            0,
        )

        if (
            request_id is None
            or requester is None
        ):
            return

        log(
            "Environment telemetry request "
            f"from node {requester} "
            f"(packet {request_id})"
        )

        telemetry = (
            build_environment_telemetry()
        )

        if telemetry is None:
            log(
                "Cannot answer telemetry request: "
                "weather data unavailable"
            )
            return

        if mesh_send(
            telemetry,
            destination_id=requester,
            channel_index=channel,
            reply_id=request_id,
        ):
            log(
                "On-demand telemetry reply sent "
                f"to node {requester}"
            )

        else:
            log(
                "On-demand telemetry reply failed "
                f"to node {requester}"
            )

    except Exception as e:
        log(
            "Telemetry request handler error: "
            f"{e}"
        )


# ---------------------------------------------------------
# Periodic sender loop
# ---------------------------------------------------------

def sender_loop():
    while not stop_event.wait(
        SEND_INTERVAL
    ):
        try:
            send_periodic_weather()

        except Exception as e:
            log(
                f"Sender loop error: {e}"
            )


# ---------------------------------------------------------
# Main
# ---------------------------------------------------------

def main():
    if not validate_configuration():
        return 1

    mqtt_client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2
    )

    mqtt_client.username_pw_set(
        MQTT_USER,
        MQTT_PASSWORD,
    )

    mqtt_client.on_connect = (
        on_mqtt_connect
    )

    mqtt_client.on_disconnect = (
        on_mqtt_disconnect
    )

    mqtt_client.on_message = (
        on_mqtt_message
    )

    # Listen for incoming Meshtastic packets.
    pub.subscribe(
        on_meshtastic_receive,
        "meshtastic.receive",
    )

    try:
        #
        # Initial Meshtastic connection.
        #
        # Failure here is no longer fatal.
        # The watchdog will keep retrying.
        #
        if not connect_mesh_interface():
            log(
                "Initial Meshtastic connection failed. "
                "Watchdog will retry automatically."
            )

            mesh_reconnect_event.set()

        #
        # Start Mesh watchdog.
        #
        watchdog_thread = threading.Thread(
            target=mesh_watchdog_loop,
            name="mesh-watchdog",
            daemon=True,
        )

        watchdog_thread.start()

        #
        # Connect MQTT.
        #
        mqtt_client.connect(
            MQTT_HOST,
            MQTT_PORT,
            60,
        )

        #
        # Start periodic telemetry sender.
        #
        sender_thread = threading.Thread(
            target=sender_loop,
            name="weather-sender",
            daemon=True,
        )

        sender_thread.start()

        log(
            "Weather bridge started"
        )

        log(
            f"Periodic interval: "
            f"{SEND_INTERVAL} seconds "
            f"({SEND_INTERVAL // 60} minutes)"
        )

        log(
            "On-demand Environment Metrics "
            "responder enabled"
        )

        log(
            f"Meshtastic reconnect interval: "
            f"{MESH_RECONNECT_INTERVAL} seconds"
        )

        mqtt_client.loop_forever()

    except KeyboardInterrupt:
        log(
            "Stopping weather bridge"
        )

    except Exception as e:
        log(
            f"Fatal error: {e}"
        )
        return 1

    finally:
        stop_event.set()

        try:
            mqtt_client.disconnect()
        except Exception:
            pass

        close_mesh_interface()

        log(
            "Stopped"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
