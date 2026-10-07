#!/usr/bin/env python3

import os
import time
import threading
import unicodedata

from pubsub import pub
from meshtastic.protobuf import portnums_pb2


def normalize_text(text):
    """
    Normalize text for command matching.

    - Converts to lowercase
    - Removes leading/trailing whitespace
    - Removes Greek accents/diacritics
    """

    text = str(text).strip().lower()

    normalized = unicodedata.normalize(
        "NFD",
        text,
    )

    return "".join(
        char
        for char in normalized
        if unicodedata.category(char) != "Mn"
    )


def wind_direction_name(degrees):
    """
    Convert wind direction in degrees to a 16-point compass direction.

    0 / 360 = N
    22.5     = NNE
    45       = NE
    ...
    """

    degrees = float(degrees) % 360.0

    directions = [
        "N",
        "NNE",
        "NE",
        "ENE",
        "E",
        "ESE",
        "SE",
        "SSE",
        "S",
        "SSW",
        "SW",
        "WSW",
        "W",
        "WNW",
        "NW",
        "NNW",
    ]

    index = int(
        (degrees + 11.25) / 22.5
    ) % 16

    return directions[index]


class WeatherBot:
    def __init__(
        self,
        get_weather_snapshot,
        get_mesh_interface,
        connect_mesh_interface,
        request_mesh_reconnect,
        log,
    ):
        self.get_weather_snapshot = get_weather_snapshot
        self.get_mesh_interface = get_mesh_interface
        self.connect_mesh_interface = connect_mesh_interface
        self.request_mesh_reconnect = request_mesh_reconnect
        self.log = log

        self.enabled = (
            os.environ.get(
                "WEATHER_BOT_ENABLED",
                "false",
            ).lower()
            in ("1", "true", "yes", "on")
        )

        # -------------------------------------------------
        # Keywords
        # -------------------------------------------------
        #
        # Preferred configuration:
        #
        # WEATHER_BOT_KEYWORDS=weather,θερμοκρασία,meteo,καιρός
        #
        # WEATHER_BOT_KEYWORD is still supported for
        # backwards compatibility.
        #

        keywords_env = os.environ.get(
            "WEATHER_BOT_KEYWORDS"
        )

        if keywords_env:
            raw_keywords = (
                keywords_env.split(",")
            )
        else:
            raw_keywords = [
                os.environ.get(
                    "WEATHER_BOT_KEYWORD",
                    "weather",
                )
            ]

        self.keywords = {
            normalize_text(keyword)
            for keyword in raw_keywords
            if keyword.strip()
        }

        self.channel = int(
            os.environ.get(
                "WEATHER_BOT_CHANNEL",
                "0",
            )
        )

        self.cooldown = int(
            os.environ.get(
                "WEATHER_BOT_COOLDOWN",
                "60",
            )
        )

        self.reply_delay = float(
            os.environ.get(
                "WEATHER_BOT_REPLY_DELAY",
                "2",
            )
        )

        self.location_name = os.environ.get(
            "WEATHER_BOT_LOCATION",
            "Moschato",
        ).strip()

        self.last_reply = 0.0
        self.reply_lock = threading.Lock()

    # -----------------------------------------------------
    # Start
    # -----------------------------------------------------

    def start(self):
        if not self.enabled:
            self.log(
                "Weather bot disabled"
            )
            return

        pub.subscribe(
            self.on_receive,
            "meshtastic.receive",
        )

        keywords_display = ", ".join(
            sorted(self.keywords)
        )

        self.log(
            "Weather bot enabled: "
            f'keywords="{keywords_display}", '
            f"channel={self.channel}, "
            f"cooldown={self.cooldown}s, "
            f"reply_delay={self.reply_delay:.1f}s"
        )

    # -----------------------------------------------------
    # Build text response
    # -----------------------------------------------------

    def build_weather_message(self):
        snapshot = self.get_weather_snapshot()

        if snapshot is None:
            return (
                f"{self.location_name} weather: "
                "data currently unavailable."
            )

        data, gust_max = snapshot

        temperature = float(
            data["temperature_C"]
        )

        humidity = float(
            data["humidity"]
        )

        wind_speed = float(
            data["wind_avg_m_s"]
        )

        if gust_max is not None:
            wind_gust = float(
                gust_max
            )
        else:
            wind_gust = float(
                data["wind_max_m_s"]
            )

        wind_direction = int(
            round(
                data["wind_dir_deg"]
            )
        ) % 360

        wind_direction_text = wind_direction_name(
            wind_direction
        )

        return (
            f"{self.location_name} Weather / Current Conditions: "
            f"Temp: {temperature:.1f}°C | "
            f"Hum {humidity:.0f}% | "
            f"Wind {wind_speed:.1f} m/s | "
            f"Gust {wind_gust:.1f} m/s | "
            f"Dir {wind_direction}° ({wind_direction_text})"
        )

    # -----------------------------------------------------
    # Send public reply
    # -----------------------------------------------------

    def send_reply(
        self,
        text,
        channel,
        reply_id=None,
    ):
        iface = self.get_mesh_interface()

        if iface is None:
            if not self.connect_mesh_interface():
                self.log(
                    "Weather bot: "
                    "Meshtastic connection unavailable"
                )

                self.request_mesh_reconnect(
                    "weather bot send"
                )

                return False

            iface = self.get_mesh_interface()

        if iface is None:
            return False

        try:
            kwargs = {
                "destinationId": "^all",
                "channelIndex": channel,
                "wantAck": False,
            }

            if reply_id is not None:
                kwargs["replyId"] = reply_id

            iface.sendText(
                text,
                **kwargs,
            )

            return True

        except Exception as e:
            self.log(
                f"Weather bot send failed: {e}"
            )

            self.request_mesh_reconnect(
                f"weather bot send failure: {e}"
            )

            return False

    # -----------------------------------------------------
    # Incoming Meshtastic packet
    # -----------------------------------------------------

    def on_receive(
        self,
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
                "TEXT_MESSAGE_APP",
                portnums_pb2.PortNum.TEXT_MESSAGE_APP,
            ):
                return

            channel = packet.get(
                "channel",
                0,
            )

            # Ignore messages outside the configured channel.
            if channel != self.channel:
                return

            # Depending on the Meshtastic Python version,
            # decoded text may already be available as "text".
            text = decoded.get(
                "text"
            )

            # Otherwise decode the raw payload.
            if text is None:
                payload = decoded.get(
                    "payload",
                    b"",
                )

                if isinstance(
                    payload,
                    bytes,
                ):
                    text = payload.decode(
                        "utf-8",
                        errors="ignore",
                    )
                else:
                    text = str(
                        payload
                    )

            text = text.strip()

            if not text:
                return

            normalized_text = normalize_text(
                text
            )

            # Exact keyword match.
            #
            # Matching is:
            # - case-insensitive
            # - accent-insensitive
            #
            # Example:
            # "θερμοκρασία", "θερμοκρασια"
            # and "ΘΕΡΜΟΚΡΑΣΙΑ" all match the same keyword.
            if normalized_text not in self.keywords:
                return

            requester = packet.get(
                "from"
            )

            request_id = packet.get(
                "id"
            )

            self.log(
                "Weather bot command received "
                f"from node {requester}: "
                f'"{text}"'
            )

            # ---------------------------------------------
            # Anti-spam cooldown
            # ---------------------------------------------

            now = time.monotonic()

            with self.reply_lock:
                elapsed = (
                    now - self.last_reply
                )

                if (
                    self.last_reply > 0
                    and elapsed < self.cooldown
                ):
                    self.log(
                        "Weather bot reply suppressed "
                        f"(cooldown, "
                        f"{self.cooldown - elapsed:.0f}s remaining)"
                    )
                    return

                message = (
                    self.build_weather_message()
                )

                # Give the requesting node time to return
                # from TX to RX before sending the reply.
                if self.reply_delay > 0:
                    self.log(
                        f"Weather bot waiting "
                        f"{self.reply_delay:.1f}s before reply"
                    )

                    time.sleep(
                        self.reply_delay
                    )

                if self.send_reply(
                    message,
                    channel,
                    reply_id=request_id,
                ):
                    self.last_reply = (
                        time.monotonic()
                    )

                    self.log(
                        "Weather bot public reply sent: "
                        f"{message}"
                    )

        except Exception as e:
            self.log(
                f"Weather bot handler error: {e}"
            )
