# Meshtastic Traceroute Logger

A small Python utility that performs Meshtastic traceroutes to a predefined list of nodes and stores the results for later analysis.

It was mainly created to compare antenna performance over time by collecting repeated measurements under similar conditions.


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

Results are stored in:

```text
traceroutes.csv
traceroutes_raw.jsonl
```

The CSV file is intended for easy analysis in Excel, LibreOffice, Python, pandas, etc.

The JSONL file keeps the raw traceroute information for more detailed analysis if needed.

## Antenna Comparison

The most useful values for antenna A/B testing are:

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

- Python 3
- Meshtastic Python package

Install Meshtastic:

```bash
pip install meshtastic
```

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
  --host 192.168.1.234 \
  --port 4403
```

## Recommended Test Method

For a meaningful antenna comparison, try to keep the following unchanged:

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

## Important

> [!WARNING]
> **DO NOT ABUSE TRACEROUTE.**
>
> Meshtastic uses a shared, low-bandwidth LoRa radio channel.
>
> Traceroute traffic consumes airtime on every node involved in the route and excessive use can negatively affect the mesh for everyone.
>
> Traceroute should be treated as a diagnostic tool, not as a continuous monitoring mechanism.

The script intentionally waits between traceroute requests.

Do not reduce the delay aggressively and do not run the script at unnecessarily short intervals.

For long-term antenna or propagation testing, one or two measurements per day are usually more than enough. Avoid frequent or continuous execution.

## Disclaimer

This project is intended for testing, diagnostics and experimentation on Meshtastic networks.

Use it **responsibly** and **respect** the shared radio spectrum.

## License

MIT License

Copyright © 2026 Antonis Maglaras
