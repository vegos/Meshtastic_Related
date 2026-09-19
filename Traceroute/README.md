# Meshtastic Traceroute Logger

A small Python utility that performs Meshtastic traceroutes to a predefined list of nodes and stores the results for later analysis.

It was mainly created to compare performance over time by collecting repeated measurements under similar conditions.


## What It Records

For each target node, the script stores:

- Timestamp
- Antenna label
- Source node IP / hostname
- Source TCP port
- Source Meshtastic node ID
- Target name
- Target node ID
- Traceroute success or failure
- Response duration
- Forward route
- Return route
- Number of relays
- Local TX SNR
- Local RX SNR
- Full forward-route SNR values
- Full return-route SNR values
- Retry status after timeout


Results are stored in:

```text
traceroutes.csv
traceroutes_raw.jsonl
```

The CSV file is intended for easy analysis in Excel, LibreOffice, Python, pandas, etc.

The JSONL file keeps the raw traceroute information for more detailed analysis if needed.


## Comparison

The most useful values for performance comparison are:


### Local TX SNR

The SNR reported by the first node that receives the packet from the local node.

Example:

```text
Local Node -> Relay -> Target
             ^
             Local TX SNR
```

For a direct connection:

```text
Local Node -> Target
             ^
             Local TX SNR
```

This is useful for evaluating how well the local antenna is transmitting.


### Local RX SNR

The SNR of the final hop received by the local node on the return path.

Example:

```text
Target -> Relay -> Local Node
                   ^
                   Local RX SNR
```

This is useful for evaluating receive-side antenna performance.

Intermediate SNR values are also stored, but they are generally less important when comparing only the antenna connected to the local node.


## Requirements

* Python 3.10 or newer
* `pip`
* Meshtastic Python package
* Network access to a Meshtastic node using the TCP API, or a directly connected serial device
* Write permission to the configured log directory

For Debian/Ubuntu systems, the required Python packages can be installed with:

```bash
sudo apt update
sudo apt install python3 python3-pip python3-venv
```

## Installation

Clone the repository:

```bash
git clone https://github.com/vegos/Meshtastic_Related.git
cd Meshtastic_Related/Traceroute
```

Creating a Python virtual environment is recommended:

```bash
python3 -m venv venv
source venv/bin/activate
```

Install the Meshtastic Python package:

```bash
pip install --upgrade pip
pip install meshtastic
```

The script does not currently require any additional third-party Python packages.

Make the script executable if you want to run it directly:

```bash
chmod +x meshtastic_traceroute_logger.py
```

You can then run it either with Python:

```bash
python3 meshtastic_traceroute_logger.py \
  --antenna "6dB Omni" \
  --host 192.168.1.234 \
  --port 4403
```

or, if the script is executable:

```bash
./meshtastic_traceroute_logger.py \
  --antenna "6dB Omni" \
  --host 192.168.1.234 \
  --port 4403
```

### Log Directory

By default, results are written to:

```text
/var/log/meshtastic-traceroute-data/
```

The user running the script must have permission to create and write to this directory.

For example:

```bash
sudo mkdir -p /var/log/meshtastic-traceroute-data
sudo chown $USER:$USER /var/log/meshtastic-traceroute-data
```

If you prefer another location, change the `DATA_DIR` setting inside the script.


## Configuration

Edit the target list inside the script:

```python
TARGETS = [
    ("Node-1", "!12345678"),
    ("Node-2", "!90123456"),
    ("Node-3", "!78901234"),
    ("Node-4", "!56789012"),
    ("Node-5", "!34567890"), # etc
]
```

The first value is only a friendly label.

The second value must be the actual Meshtastic node ID.


## Usage

Example:

```bash
python3 meshtastic_traceroute_logger.py \
  --antenna "6dB Omni" \
  --host 192.168.1.234 \
  --port 4403
```

Available parameters:

```text
--antenna    Label for the antenna currently installed
--host       Meshtastic TCP host or IP address
--port       Meshtastic TCP port
```

Example using another node:

```bash
python3 meshtastic_traceroute_logger.py \
  --antenna "Test Antenna" \
  --host 192.168.1.235 \
  --port 4403
```

### Note

Network access to a Meshtastic node is normally provided through the TCP API.  
Serial connections are also supported by changing `CONNECTION_TYPE` and `SERIAL_PORT` in the script.  


## Retry Behavior

If a traceroute request times out, the script waits for 30 seconds and automatically retries the same target once.

The retry is performed only after a `TIMEOUT`. Other routing or parsing errors are not retried.

Retry results are recorded in the existing `error` field:

```text
RETRY_SUCCESS_AFTER_TIMEOUT
```

means that the first attempt timed out but the retry succeeded.

```text
TIMEOUT_AFTER_RETRY
```

means that both the initial attempt and the retry timed out.

If the retry fails for another reason, the error is recorded as:

```text
RETRY_FAILED_AFTER_TIMEOUT:<error>
```

This preserves the information that a retry was required, which can be useful when comparing link reliability over time.


## Recommended Test Method

For a meaningful performance comparison, try to keep the following unchanged:  

- Meshtastic node
- Node position
- Antenna height
- Coaxial cable
- TX power
- Modem preset
- Frequency
- Hop limit
- Target nodes
- Measurement times

Collect several days of data with each antenna.

Useful comparison metrics include:

- Traceroute success rate
- Direct vs relayed routes
- Median Local TX SNR
- Median Local RX SNR
- Number of relays
- Route stability
- Timeout rate

Directly reachable nodes are particularly useful for antenna comparisons because no intermediate relay affects the RF path.


## Example Output

```text
Connecting to Meshtastic node 192.168.1.234:4403 ...

Local node : !7a31c8f2
Endpoint   : 192.168.1.234:4403
Antenna    : 6dB Omni
Targets    : 6
CSV        : /var/log/meshtastic-traceroute-data/traceroutes.csv

[1/6] Traceroute -> Alpha (!12ab34cd)
  OK | relays=0 | TX SNR=-3.50 dB | RX SNR=8.00 dB
  OUT: !7a31c8f2 -> !12ab34cd
  IN : !12ab34cd -> !7a31c8f2
  Waiting 35s...

[2/6] Traceroute -> Bravo (!56ef7890)
  OK | relays=2 | TX SNR=-1.75 dB | RX SNR=7.50 dB
  OUT: !7a31c8f2 -> !91bc22de -> !4f7a813c -> !56ef7890
  IN : !56ef7890 -> !7a31c8f2
  Waiting 35s...

[3/6] Traceroute -> Charlie (!8c42de17)
  OK | relays=0 | TX SNR=-8.00 dB | RX SNR=10.00 dB
  OUT: !7a31c8f2 -> !8c42de17
  IN : !8c42de17 -> !7a31c8f2
  Waiting 35s...

[4/6] Traceroute -> Delta (!3e9f10a6)
  OK | relays=0 | TX SNR=-2.00 dB | RX SNR=10.00 dB
  OUT: !7a31c8f2 -> !3e9f10a6
  IN : !3e9f10a6 -> !7a31c8f2
  Waiting 35s...

[5/6] Traceroute -> Echo (!b74c29e1)
  TIMEOUT - retrying in 30s...
  OK (after retry) | relays=1 | TX SNR=-5.25 dB | RX SNR=-2.75 dB
  OUT: !7a31c8f2 -> !6d28f4a9 -> !b74c29e1
  IN : !b74c29e1 -> !7a31c8f2
  Waiting 35s...

[6/6] Traceroute -> Foxtrot (!d18a63f5)
  TIMEOUT - retrying in 30s...
  FAILED: TIMEOUT_AFTER_RETRY
```


## Important

> [!WARNING]
> **DO NOT ABUSE TRACEROUTE.**
>
> Meshtastic uses a shared, low-bandwidth LoRa radio channel.
>
> Traceroute traffic **consumes airtime** on every node involved in the route and excessive use **can negatively affect the mesh for everyone**.
>
> Traceroute should be treated as a diagnostic tool, not as a continuous monitoring mechanism.

The script intentionally waits between traceroute requests.

**Do not reduce the delay aggressively** and do not run the script at unnecessarily short intervals.

For long-term antenna or propagation testing, **one or two measurements per day** are usually more than enough. Avoid frequent or continuous execution.

## Disclaimer

This project is intended for testing, diagnostics and experimentation on Meshtastic networks.

Use it **responsibly** and **respect** the shared radio spectrum.

## License

MIT License

Copyright © 2026 Antonis Maglaras
