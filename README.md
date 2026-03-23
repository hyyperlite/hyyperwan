# HyyperWAN

HyyperWAN is a web application for emulating WAN conditions on Linux systems. It uses `tc qdisc` to apply latency, jitter, packet loss, and bandwidth limits to network interfaces, making it useful for testing SD-WAN, QoS, and other network-dependent features. The primary deployment method is as a Docker container running with `--net=host` so it controls the host's network stack directly.

![Alt text](hyyperwan.png "HyyperWAN")

---

## What's New

- **Bandwidth limiting** — Set a bandwidth cap per interface (kbit/mbit/gbit) in addition to or instead of latency/jitter/loss. Uses HTB+netem stacking when combining impairments.
- **Route table management** — View, add, and remove IPv4 and IPv6 routes on the host via a dedicated Routes page (uses `ip route`; changes are temporary).
- **Interface detail page** — Click the `↗` icon next to any interface to open a dedicated page with:
  - Live bandwidth graph (RX/TX bytes/sec, 60-second rolling window, 1s polling)
  - IPv4/IPv6 address management (add/remove via `ip addr`)
  - MTU configuration (`ip link set mtu`)
- **Simultaneous HTTP + HTTPS** — A single image/process can now listen on both HTTP and HTTPS at the same time, controlled entirely by environment variables. The separate `hyyperwan-http` and `hyyperwan-https` images have been replaced by a single `hyyperwan:latest`.
- **UI overhaul** — Sticky navbar, theme switcher (Dark Red / Dark Blue / Light), responsive layout, coloured status badges, and theme preference persisted in localStorage.
- **Consolidated Dockerfile** — Single `docker/Dockerfile` replaces the previous `Dockerfile.http` and `Dockerfile.https`.

---

## Features

- Set and control network latency, jitter, and packet loss per interface
- Bandwidth limiting per interface
- Enable/disable Source NAT (Masquerade) per interface
- Interface aliases for easier identification (persistent across restarts)
- Per-interface detail page with live bandwidth graph, IP address management, and MTU setting
- Host route table view with add/remove (IPv4 and IPv6)
- Packet capture via tcpdump with host/network/port filters and PCAP download
- HTTP, HTTPS, or both simultaneously — controlled via environment variables
- Dark Red, Dark Blue, and Light UI themes (persisted in browser localStorage)

---

## Requirements

**When run as a Docker container (recommended):**
- Docker
- The container must be run with `--net=host` and `--privileged`

**When run directly on the host:**
- Linux (tested on Ubuntu/Debian)
- Python 3.8+
- Root/sudo privileges
- `iproute2` (`ip`, `tc` commands)
- `tcpdump` (for packet capture)
- `iptables` (for Source NAT)

---

## Installation

### Option 1: Docker — Pull from GitHub Container Registry (recommended)

```bash
docker pull ghcr.io/hyyperlite/hyyperwan:latest
```

**HTTP only (default — port 8080):**
```bash
docker run -d --name hyyperwan \
  --net=host --privileged \
  --restart unless-stopped \
  ghcr.io/hyyperlite/hyyperwan:latest
```

**HTTPS only (port 8443):**
```bash
docker run -d --name hyyperwan \
  --net=host --privileged \
  --restart unless-stopped \
  -e ENABLE_HTTP=false \
  -e ENABLE_HTTPS=true \
  -e SSL_CERT_PATH=/certs/cert.pem \
  -e SSL_KEY_PATH=/certs/key.pem \
  -v /path/to/your/certs:/certs \
  ghcr.io/hyyperlite/hyyperwan:latest
```

**Both HTTP and HTTPS simultaneously:**
```bash
docker run -d --name hyyperwan \
  --net=host --privileged \
  --restart unless-stopped \
  -e ENABLE_HTTP=true \
  -e ENABLE_HTTPS=true \
  -e SSL_CERT_PATH=/certs/cert.pem \
  -e SSL_KEY_PATH=/certs/key.pem \
  -v /path/to/your/certs:/certs \
  ghcr.io/hyyperlite/hyyperwan:latest
```

**Custom ports:**
```bash
docker run -d --name hyyperwan \
  --net=host --privileged \
  --restart unless-stopped \
  -e HTTP_PORT=80 \
  -e HTTPS_PORT=443 \
  -e ENABLE_HTTPS=true \
  -e SSL_CERT_PATH=/certs/cert.pem \
  -e SSL_KEY_PATH=/certs/key.pem \
  -v /path/to/your/certs:/certs \
  ghcr.io/hyyperlite/hyyperwan:latest
```

**Hide the Tools column (packet capture / NAT buttons):**
```bash
docker run -d --name hyyperwan \
  --net=host --privileged \
  --restart unless-stopped \
  -e DISABLE_TOOLS_COLUMN=true \
  ghcr.io/hyyperlite/hyyperwan:latest
```

**Access the application:**
- HTTP:  `http://<host-ip>:8080`
- HTTPS: `https://<host-ip>:8443` (self-signed cert will produce a browser warning — accept/proceed)

**Stop / view logs:**
```bash
docker stop hyyperwan
docker logs hyyperwan
```

> **Security note:** `--net=host` and `--privileged` grant the container extensive access to the host. This is intentional — it is how HyyperWAN controls host network interfaces from inside the container.

---

### Option 2: Build Docker Image from Source

Clone the repository and build the single unified Dockerfile:

```bash
git clone https://github.com/hyyperlite/hyyperwan.git
cd hyyperwan
docker build --no-cache -t hyyperwan -f docker/Dockerfile .
```

Then run using the same `docker run` examples shown in Option 1, replacing `ghcr.io/hyyperlite/hyyperwan:latest` with `hyyperwan`.

**Generating a self-signed certificate (if needed for HTTPS):**
```bash
mkdir -p certificates
openssl req -x509 -newkey rsa:4096 -nodes \
  -out certificates/cert.pem \
  -keyout certificates/key.pem \
  -days 365
```

---

### Option 3: Direct Installation (no Docker)

```bash
git clone https://github.com/hyyperlite/hyyperwan.git
cd hyyperwan
pip install -r requirements.txt
python app.py
```

The application will be accessible at `http://server-ip:8080` by default. See the Environment Variables section below to configure ports, HTTPS, etc.

Ensure the following are installed on the host:
```bash
sudo apt-get install iproute2 tcpdump iptables   # Debian/Ubuntu
sudo yum install iproute tcpdump iptables        # RHEL/CentOS
```

---

### Option 4: Systemd Service (production, non-containerized)

1. **Create a dedicated user:**
    ```bash
    sudo groupadd hyyperwan
    sudo useradd -r -g hyyperwan -d /opt/hyyperwan -s /sbin/nologin hyyperwan
    ```

2. **Clone and place application files:**
    ```bash
    git clone https://github.com/hyyperlite/hyyperwan.git
    sudo mv hyyperwan /opt/
    ```

3. **Create a virtual environment and install dependencies:**
    ```bash
    sudo apt-get install python3-venv
    sudo -u hyyperwan python3 -m venv /opt/hyyperwan/venv
    sudo -u hyyperwan /opt/hyyperwan/venv/bin/pip install --no-cache-dir -r /opt/hyyperwan/requirements.txt
    ```

4. **Configure passwordless sudo for the hyyperwan user:**
    Use `sudo visudo -f /etc/sudoers.d/hyyperwan` and add:
    ```sudoers
    hyyperwan ALL=(ALL) NOPASSWD: /usr/sbin/tc
    hyyperwan ALL=(ALL) NOPASSWD: /usr/sbin/ip
    hyyperwan ALL=(ALL) NOPASSWD: /usr/sbin/tcpdump
    hyyperwan ALL=(ALL) NOPASSWD: /usr/sbin/iptables
    ```
    Verify paths with `which tc`, `which ip`, etc.

5. **Copy and configure the systemd service file:**
    ```bash
    sudo cp /opt/hyyperwan/systemctl/hyyperwan.service.http /etc/systemd/system/hyyperwan.service
    ```
    Edit the service file to set environment variables as needed (see Environment Variables below).

6. **Set ownership and enable the service:**
    ```bash
    sudo chown -R hyyperwan:hyyperwan /opt/hyyperwan
    sudo systemctl daemon-reload
    sudo systemctl enable hyyperwan.service
    sudo systemctl start hyyperwan.service
    sudo systemctl status hyyperwan.service
    ```

---

## Configuration

### Environment Variables

All configuration is done through environment variables — in the `.env` file for direct installs, or via `-e` flags for Docker.

| Variable | Default | Description |
|---|---|---|
| `FLASK_RUN_HOST` | `0.0.0.0` | Bind address |
| `ENABLE_HTTP` | `true` | Enable HTTP listener |
| `ENABLE_HTTPS` | `false` | Enable HTTPS listener |
| `HTTP_PORT` | `8080` | HTTP listen port |
| `HTTPS_PORT` | `8443` | HTTPS listen port |
| `SSL_CERT_PATH` | _(unset)_ | Path to TLS certificate (required when ENABLE_HTTPS=true) |
| `SSL_KEY_PATH` | _(unset)_ | Path to TLS private key (required when ENABLE_HTTPS=true) |
| `DISABLE_TOOLS_COLUMN` | `false` | Hide the Tools column (packet capture + NAT buttons) |
| `FLASK_DEBUG` | `false` | Enable Flask debug mode |
| `USE_HTTPS` | `false` | Legacy alias: `true` is equivalent to `ENABLE_HTTPS=true` + `ENABLE_HTTP=false` |
| `FLASK_RUN_PORT` | _(unset)_ | Legacy alias for `HTTP_PORT` |

---

## Usage

### Main Interface Table

Access the web interface at `http://<host-ip>:8080` (or the configured port).

For each network interface you can:
- Set **latency** (e.g. `100ms`, `50us`)
- Set **jitter** (e.g. `10ms`)
- Set **packet loss** (e.g. `5%`)
- Set a **bandwidth limit** (e.g. `10 mbit`)
- Click **Apply** to apply selected conditions
- Click **Remove** to clear conditions for that interface
- Click **Reset All Interfaces** to clear all interfaces at once
- Toggle **Source NAT** (Masquerade) on/off per interface
- Click **Capture** to start a tcpdump packet capture

Click the **`↗`** icon next to any interface name to open the interface detail page.

### Interface Detail Page

- **IP Addresses** — view, add, and remove IPv4/IPv6 addresses (`ip addr add/del`)
- **MTU** — view and set the MTU (`ip link set mtu`)
- **Bandwidth Monitor** — live scrolling graph of RX/TX bytes/sec (1-second polling, 60-second window)

> Address and MTU changes are temporary and will not survive a reboot. Use your distribution's network configuration tooling (Netplan, NetworkManager, etc.) for persistent changes.

### Routes Page

Click **Routes** in the navigation bar to view and manage the host's routing table.

- View all IPv4 and IPv6 routes
- Add a route (destination, gateway, interface, metric)
- Remove a non-kernel route

> Route changes are temporary and will not survive a reboot.

### Interface Aliases

Click **Add alias** / **Edit alias** next to any interface name to assign a friendly label. Aliases are stored persistently in `interface_aliases.json` and survive application restarts.

### Packet Capture

1. Click **Capture** next to an interface
2. Configure filters in the popup:
   - **Host filter** — specific IP addresses (comma-separated, AND/OR logic)
   - **Network filter** — CIDR blocks (e.g. `192.168.1.0/24`)
   - **Port filter** — port numbers (comma-separated)
3. Click **Start Capture** — limited to 10,000 packets
4. Click **Stop & Download** to retrieve the `.pcap` file (compatible with Wireshark)

Capture files are stored temporarily in `/tmp/hyyperwan_pcaps/` and deleted automatically after download.

### Themes

Click the **Theme** button in the top-right corner to cycle through:
- **Dark Red** (default)
- **Dark Blue**
- **Light**

Theme preference is saved in browser `localStorage` and persists across sessions.

---

## Troubleshooting

**Application logs:**
```bash
cat app.log                                    # direct install
docker logs hyyperwan                          # Docker
sudo journalctl -u hyyperwan.service -n 50     # systemd
```

**Verify tc is working:**
```bash
sudo tc qdisc show
```

**Verify ip command is available:**
```bash
ip -j addr
```

**HTTPS slow to start:** The HTTPS listener may take up to ~60 seconds to begin accepting connections after startup while the SSL context initialises. HTTP is available immediately.

**sudo password prompts in logs:** The application requires passwordless sudo for `tc`, `ip`, `tcpdump`, and `iptables`. Ensure sudoers is configured correctly (see Option 4 step 4 above). Docker deployments using `--privileged` handle this automatically.

---

## License

[Add your license information here]

## Contributors

[Add contributor information here]
