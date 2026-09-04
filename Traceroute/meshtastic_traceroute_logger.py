#!/usr/bin/env python3

import argparse
import csv
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from meshtastic.protobuf import mesh_pb2, portnums_pb2
from meshtastic.tcp_interface import TCPInterface
from meshtastic.serial_interface import SerialInterface


# ---------------------------------------------------------------------
# DEFAULT CONFIGURATION
# ---------------------------------------------------------------------

CONNECTION_TYPE = "tcp"

# Defaults used when --host / --port are not specified
DEFAULT_TCP_HOST = "192.168.100.15"
DEFAULT_TCP_PORT = 4404

# Used only if CONNECTION_TYPE = "serial"
SERIAL_PORT = "/dev/ttyACM0"

# Meshtastic channel used for traceroute
CHANNEL_INDEX = 0

# Keep identical during antenna A/B testing
HOP_LIMIT = 4

# Timeout for each traceroute
TRACEROUTE_TIMEOUT = 90

# Pause between traceroutes to avoid hammering the mesh
DELAY_BETWEEN_TARGETS = 35


# ---------------------------------------------------------------------
# TARGET NODES
# ---------------------------------------------------------------------

TARGETS = [
    ("Node-1", "!12345678"),
    ("Node-2", "!90123456"),
    ("Node-3", "!78901234"),
    ("Node-4", "!56789012"),
    ("Node-5", "!34567890"), # etc
]


# ---------------------------------------------------------------------
# DATA FILES
# ---------------------------------------------------------------------

# Include the full logging folder path and name
DATA_DIR = Path.home() / "/var/log/meshtastic-traceroute-data"

CSV_FILE = DATA_DIR / "traceroutes.csv"
RAW_FILE = DATA_DIR / "traceroutes_raw.jsonl"


CSV_FIELDS = [
    "timestamp_local",
    "timestamp_utc",

    "antenna",

    "source_host",
    "source_port",
    "source_node_id",

    "target_name",
    "target_id",

    "success",
    "error",
    "duration_s",

    "forward_route",
    "return_route",

    "forward_relays",
    "return_relays",

    # Main antenna comparison values
    "local_tx_snr_db",
    "local_rx_snr_db",

    # Full SNR information retained for later analysis
    "forward_snr_db",
    "return_snr_db",
]


# ---------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------

def node_id(node_num):
    """
    Convert Meshtastic numeric node number to !xxxxxxxx format.
    """

    if node_num is None:
        return ""

    return f"!{int(node_num):08x}"


def snr_value(raw):
    """
    Meshtastic traceroute SNR values are stored in quarter-dB units.

    Example:
        -32 -> -8.00 dB

    -128 means unknown.
    """

    if raw == -128:
        return None

    return raw / 4.0


def snr_list(values):
    return [snr_value(v) for v in values]


def format_snr_list(values):
    """
    Convert SNR list to a compact CSV-friendly string.

    Example:
        -5.00|-3.75|-7.25
    """

    result = []

    for value in values:
        if value is None:
            result.append("?")
        else:
            result.append(f"{value:.2f}")

    return "|".join(result)


# ---------------------------------------------------------------------
# CONNECTION
# ---------------------------------------------------------------------

def open_interface(host, port):

    if CONNECTION_TYPE.lower() == "tcp":

        print(
            f"Connecting to Meshtastic node "
            f"{host}:{port} ..."
        )

        return TCPInterface(
            hostname=host,
            portNumber=port,
            timeout=30,
        )

    if CONNECTION_TYPE.lower() == "serial":

        print(
            f"Connecting to Meshtastic node "
            f"{SERIAL_PORT} ..."
        )

        return SerialInterface(
            devPath=SERIAL_PORT
        )

    raise ValueError(
        f"Unknown CONNECTION_TYPE: {CONNECTION_TYPE}"
    )


# ---------------------------------------------------------------------
# TRACEROUTE
# ---------------------------------------------------------------------

def run_traceroute(interface, target_id):
    """
    Send one Meshtastic traceroute request.

    Returns:
        route
        route_back
        snr_towards
        snr_back
        duration
        success/error
    """

    finished = threading.Event()

    result = {
        "success": False,
        "error": "",
        "route": [],
        "route_back": [],
        "snr_towards": [],
        "snr_back": [],
        "from": None,
        "to": None,
    }


    def response_handler(packet):

        try:
            decoded = packet.get("decoded", {})
            portnum = decoded.get("portnum")


            # ---------------------------------------------------------
            # ROUTING ERROR
            # ---------------------------------------------------------

            if portnum == "ROUTING_APP":

                routing = decoded.get(
                    "routing",
                    {}
                )

                error = routing.get(
                    "errorReason",
                    "ROUTING_ERROR"
                )

                if error != "NONE":

                    result["success"] = False
                    result["error"] = str(error)

                    finished.set()

                return


            # ---------------------------------------------------------
            # TRACEROUTE RESPONSE
            # ---------------------------------------------------------

            if portnum != "TRACEROUTE_APP":
                return


            payload = decoded.get("payload")

            if payload is None:

                result["success"] = False
                result["error"] = (
                    "TRACEROUTE_WITHOUT_PAYLOAD"
                )

                finished.set()
                return


            route = mesh_pb2.RouteDiscovery()

            route.ParseFromString(
                payload
            )


            result["route"] = list(
                route.route
            )

            result["route_back"] = list(
                route.route_back
            )

            result["snr_towards"] = snr_list(
                list(route.snr_towards)
            )

            result["snr_back"] = snr_list(
                list(route.snr_back)
            )

            result["from"] = packet.get("from")
            result["to"] = packet.get("to")

            result["success"] = True

            finished.set()


        except Exception as exc:

            result["success"] = False

            result["error"] = (
                f"PARSE_ERROR: {exc}"
            )

            finished.set()


    # Empty RouteDiscovery request
    request = mesh_pb2.RouteDiscovery()

    start = time.monotonic()


    interface.sendData(
        request,
        destinationId=target_id,
        portNum=portnums_pb2.PortNum.TRACEROUTE_APP,
        wantResponse=True,
        onResponse=response_handler,
        channelIndex=CHANNEL_INDEX,
        hopLimit=HOP_LIMIT,
    )


    received = finished.wait(
        TRACEROUTE_TIMEOUT
    )


    result["duration_s"] = round(
        time.monotonic() - start,
        2
    )


    if not received:

        result["success"] = False
        result["error"] = "TIMEOUT"


    return result


# ---------------------------------------------------------------------
# CREATE CSV ROW
# ---------------------------------------------------------------------

def build_csv_row(
    interface,
    target_name,
    target_id,
    antenna,
    host,
    port,
    result,
):

    now_local = datetime.now().astimezone()
    now_utc = datetime.now(timezone.utc)

    local_num = interface.localNode.nodeNum
    local_id = node_id(local_num)


    forward_nodes = []
    return_nodes = []


    if result["success"]:

        # -------------------------------------------------------------
        # FORWARD ROUTE
        #
        # local -> relay(s) -> destination
        # -------------------------------------------------------------

        forward_nodes = (
            [local_id]
            + [
                node_id(x)
                for x in result["route"]
            ]
            + [target_id]
        )


        # -------------------------------------------------------------
        # RETURN ROUTE
        #
        # destination -> relay(s) -> local
        # -------------------------------------------------------------

        return_nodes = (
            [target_id]
            + [
                node_id(x)
                for x in result["route_back"]
            ]
            + [local_id]
        )


    forward_snr = result["snr_towards"]
    return_snr = result["snr_back"]


    # -----------------------------------------------------------------
    # LOCAL TX SNR
    #
    # SNR at the FIRST receiving node after our own node.
    #
    # This is the most useful TX-side metric for comparing our node.
    # -----------------------------------------------------------------

    local_tx_snr = (
        forward_snr[0]
        if forward_snr
        else None
    )


    # -----------------------------------------------------------------
    # LOCAL RX SNR
    #
    # SNR at OUR node from the final hop of the return route.
    #
    # This is the most useful RX-side metric for comparing our node.
    # -----------------------------------------------------------------

    local_rx_snr = (
        return_snr[-1]
        if return_snr
        else None
    )


    row = {

        "timestamp_local":
            now_local.isoformat(
                timespec="seconds"
            ),

        "timestamp_utc":
            now_utc.isoformat(
                timespec="seconds"
            ),


        "antenna":
            antenna,


        "source_host":
            host,

        "source_port":
            port,

        "source_node_id":
            local_id,


        "target_name":
            target_name,

        "target_id":
            target_id,


        "success":
            int(result["success"]),

        "error":
            result["error"],

        "duration_s":
            result["duration_s"],


        "forward_route":
            " -> ".join(
                forward_nodes
            ),

        "return_route":
            " -> ".join(
                return_nodes
            ),


        "forward_relays":
            len(
                result["route"]
            ),

        "return_relays":
            len(
                result["route_back"]
            ),


        "local_tx_snr_db":
            (
                ""
                if local_tx_snr is None
                else f"{local_tx_snr:.2f}"
            ),

        "local_rx_snr_db":
            (
                ""
                if local_rx_snr is None
                else f"{local_rx_snr:.2f}"
            ),


        "forward_snr_db":
            format_snr_list(
                forward_snr
            ),

        "return_snr_db":
            format_snr_list(
                return_snr
            ),
    }


    return row


# ---------------------------------------------------------------------
# CSV HANDLING
# ---------------------------------------------------------------------

def get_csv_file():
    """
    Check whether the existing CSV has the current schema.

    If an older version of traceroutes.csv exists,
    create a new CSV instead of corrupting it.
    """

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True
    )


    if not CSV_FILE.exists():
        return CSV_FILE


    try:

        with CSV_FILE.open(
            "r",
            newline="",
            encoding="utf-8"
        ) as f:

            reader = csv.reader(f)

            existing_header = next(
                reader,
                []
            )


        if existing_header == CSV_FIELDS:
            return CSV_FILE


    except Exception:
        pass


    timestamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    new_file = DATA_DIR / (
        f"traceroutes_{timestamp}.csv"
    )


    print()
    print(
        "Existing traceroutes.csv uses an older schema."
    )

    print(
        f"New data will be written to:"
    )

    print(
        f"  {new_file}"
    )

    print()


    return new_file


def save_csv(csv_file, row):

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True
    )


    new_file = (
        not csv_file.exists()
        or csv_file.stat().st_size == 0
    )


    with csv_file.open(
        "a",
        newline="",
        encoding="utf-8"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=CSV_FIELDS
        )


        if new_file:
            writer.writeheader()


        writer.writerow(row)


# ---------------------------------------------------------------------
# RAW JSON LOG
# ---------------------------------------------------------------------

def save_raw(
    interface,
    target_name,
    target_id,
    antenna,
    host,
    port,
    result,
):

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True
    )


    local_id = node_id(
        interface.localNode.nodeNum
    )


    record = {

        "timestamp_local":
            datetime.now()
            .astimezone()
            .isoformat(
                timespec="seconds"
            ),

        "timestamp_utc":
            datetime.now(
                timezone.utc
            ).isoformat(
                timespec="seconds"
            ),


        "antenna":
            antenna,


        "source_host":
            host,

        "source_port":
            port,

        "source_node_id":
            local_id,


        "target_name":
            target_name,

        "target_id":
            target_id,


        **result,
    }


    with RAW_FILE.open(
        "a",
        encoding="utf-8"
    ) as f:

        f.write(
            json.dumps(
                record,
                ensure_ascii=False
            )
            + "\n"
        )


# ---------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------

def main():

    parser = argparse.ArgumentParser(
        description=(
            "Meshtastic traceroute logger "
            "for node tests"
        )
    )


    parser.add_argument(
        "--antenna",
        required=True,
        help=(
            "Label/name of the antenna "
            "currently installed"
        ),
    )


    parser.add_argument(
        "--host",
        default=DEFAULT_TCP_HOST,
        help=(
            "Meshtastic node IP address "
            "or hostname "
            f"(default: {DEFAULT_TCP_HOST})"
        ),
    )


    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_TCP_PORT,
        help=(
            "Meshtastic TCP port "
            f"(default: {DEFAULT_TCP_PORT})"
        ),
    )


    args = parser.parse_args()


    antenna = args.antenna
    host = args.host
    port = args.port


    interface = None


    # Resolve correct CSV once for the entire run
    csv_file = get_csv_file()


    try:

        interface = open_interface(
            host,
            port
        )


        local_id = node_id(
            interface.localNode.nodeNum
        )


        print()
        print(
            f"Local node : {local_id}"
        )

        print(
            f"Endpoint   : {host}:{port}"
        )

        print(
            f"Antenna    : {antenna}"
        )

        print(
            f"Targets    : {len(TARGETS)}"
        )

        print(
            f"CSV        : {csv_file}"
        )

        print()


        for index, (
            target_name,
            target_id
        ) in enumerate(TARGETS):


            print(
                f"[{index + 1}/{len(TARGETS)}] "
                f"Traceroute -> "
                f"{target_name} "
                f"({target_id})"
            )


            try:

                result = run_traceroute(
                    interface,
                    target_id
                )


            except Exception as exc:

                result = {

                    "success": False,

                    "error":
                        f"EXCEPTION: {exc}",

                    "duration_s": 0,

                    "route": [],
                    "route_back": [],

                    "snr_towards": [],
                    "snr_back": [],

                    "from": None,
                    "to": None,
                }


            row = build_csv_row(
                interface,
                target_name,
                target_id,
                antenna,
                host,
                port,
                result,
            )


            save_csv(
                csv_file,
                row
            )


            save_raw(
                interface,
                target_name,
                target_id,
                antenna,
                host,
                port,
                result,
            )


            # ---------------------------------------------------------
            # CONSOLE OUTPUT
            # ---------------------------------------------------------

            if result["success"]:

                print(
                    f"  OK"
                    f" | relays="
                    f"{row['forward_relays']}"
                    f" | TX SNR="
                    f"{row['local_tx_snr_db']} dB"
                    f" | RX SNR="
                    f"{row['local_rx_snr_db']} dB"
                )


                print(
                    f"  OUT: "
                    f"{row['forward_route']}"
                )


                if row["return_route"]:

                    print(
                        f"  IN : "
                        f"{row['return_route']}"
                    )


            else:

                print(
                    f"  FAILED: "
                    f"{result['error']}"
                )


            # ---------------------------------------------------------
            # DELAY BEFORE NEXT TARGET
            # ---------------------------------------------------------

            if index < len(TARGETS) - 1:

                print(
                    f"  Waiting "
                    f"{DELAY_BETWEEN_TARGETS}s..."
                )

                time.sleep(
                    DELAY_BETWEEN_TARGETS
                )


            print()


    finally:

        if interface:

            try:
                interface.close()

            except Exception:
                pass


if __name__ == "__main__":
    main()
