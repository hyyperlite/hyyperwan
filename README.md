# HyyperWAN

HyyperWAN is a web application for controlling network conditions (latency, jitter, and packet loss) on Linux systems using tc qdisc. A per interface packet capture utility (using tcpdump) is also included.

## Features

- Set and control network latency
- Add network jitter
- Simulate packet loss
- Customize interface aliases for better identification
- Capture network packets with tcpdump for traffic analysis
- Support for both HTTP and HTTPS
- View current network condition settings per interface

## Requirements

- Linux operating system
- Python 3.8 or higher
- Root privileges (for tc commands and packet capture)
- iproute2 package (for the `ip` command)
- tcpdump (for packet capture functionality)

## Installation

### Option 1: Pull Docker Image from GitHub Container Registry (ghcr.io)

This is the recommended method for most users. Pre-built Docker images are available on GitHub Container Registry.

1.  **Pull the Docker Image:**

    For the HTTP version (listens on port 8080 by default):
    ```bash
    docker pull ghcr.io/hyyperlite/hyyperwan-http:latest
    ```

    For the HTTPS version (listens on port 8443 by default, uses included self-signed certificates):
    ```bash
    docker pull ghcr.io/hyyperlite/hyyperwan-https:latest
    ```

2.  **Run the Docker Container:**
    Start the container in detached mode. `--net=host` allows the container to share the host's network stack, `--privileged` grants the necessary permissions for `tc` and `tcpdump` operations on the host interfaces, and `--restart unless-stopped` ensures the container restarts automatically with Docker or on host reboot.

    For HTTP:
    ```bash
    docker run -d --name hyyperwan-http --net=host --privileged --restart unless-stopped ghcr.io/hyyperlite/hyyperwan-http:latest
    ```

    For HTTPS:
    ```bash
    docker run -d --name hyyperwan-https --net=host --privileged --restart unless-stopped ghcr.io/hyyperlite/hyyperwan-https:latest
    ```

3.  **Access the Application:**
    The application will be accessible via the host machine's IP address.
    - For HTTP: `http://<host-ip>:8080` (or the port configured via `FLASK_RUN_PORT`)
    - For HTTPS: `https://<host-ip>:8443` (or the port configured via `FLASK_RUN_PORT`)

4.  **Stopping the Container:**
    ```bash
    docker stop hyyperwan-http  # For the HTTP container
    # or
    docker stop hyyperwan-https # For the HTTPS container
    ```

5.  **Viewing Logs:**
    ```bash
    docker logs hyyperwan-http  # For the HTTP container
    # or
    docker logs hyyperwan-https # For the HTTPS container
    ```

### Option 2: Build Docker Image from Dockerfile

If you prefer to build the image yourself or need to make custom modifications. Example `Dockerfile.http` (for HTTP) and `Dockerfile.https` (for HTTPS) are provided in the `Docker/` directory of the repository.

**To get started, you'll need the Dockerfiles. Here are a couple of ways to obtain them:**

*   **A) Clone the full repository (Recommended if you also plan to modify application code):**
    This gives you all project files, including the Dockerfiles located in the `Docker/` subdirectory.
    ```bash
    git clone https://github.com/hyyperlite/hyyperwan.git
    cd hyyperwan
    # After cloning, the Dockerfiles will be in ./Docker/
    # Proceed to step "1. Build the Docker Image" below.
    ```

*   **B) Download only the Dockerfiles using `wget`:**
    This method is useful if you only need the Dockerfiles themselves. The provided Dockerfiles are multi-stage and will clone the application code during their own build process.
    ```bash
    # Create a directory to store the Dockerfiles and build the image
    mkdir hyyperwan_docker_build
    cd hyyperwan_docker_build

    # Create the Docker subdirectory
    mkdir Docker
    cd Docker

    # Download the Dockerfiles
    wget https://raw.githubusercontent.com/hyyperlite/hyyperwan/refs/heads/main/docker/Dockerfile.http
    wget https://raw.githubusercontent.com/hyyperlite/hyyperwan/refs/heads/main/docker/Dockerfile.https
    
    # Go back to the parent directory for the build context
    cd ..
    # After these commands, the Dockerfiles will be available in the Docker/ subdirectory.
    # (e.g., hyyperwan_docker_build/Docker/Dockerfile.http).
    # Now, proceed to step "1. Build the Docker Image" below, ensuring your terminal is in the 'hyyperwan_docker_build' directory.
    ```

**1. Build the Docker Image:**

Navigate to the directory where you have the `Docker` subdirectory (e.g., the root of the full repository if you chose option A, or the `hyyperwan_docker_build` directory if you chose option B). The build context (the `.` at the end of the `docker build` command) should be this directory.

To build the HTTP version (defaulting to port 8080):
```bash
docker build --no-cache -t hyyperwan-http -f Docker/Dockerfile.http .
```

To build the HTTPS version (defaulting to port 8443):
```bash
docker build --no-cache -t hyyperwan-https -f Docker/Dockerfile.https .
```

2.  **Run the Docker Container:**
    Start the container in detached mode. `--net=host` allows the container to share the host's network stack, `--privileged` grants the necessary permissions for `tc` and `tcpdump` operations on the host interfaces, and `--restart unless-stopped` ensures the container restarts automatically.

    For HTTP:
    ```bash
    docker run -d --name hyyperwan-http --net=host --privileged --restart unless-stopped hyyperwan-http
    ```

    For HTTPS:
    ```bash
    docker run -d --name hyyperwan-https --net=host --privileged --restart unless-stopped hyyperwan-https
    ```

3.  **Access the Application:**
    (Same as Option 1)

4.  **Stopping the Container:**
    (Same as Option 1)

5.  **Viewing Logs:**
    (Same as Option 1)

**Note for Docker Options:** Running with `--net=host` and `--privileged` grants the container extensive access to the host system. Ensure you understand the security implications before using this method.

### Option 3: Direct Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/yourusername/hyyperwan.git
   cd hyyperwan
   ```

2. Install the required dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Make sure tcpdump is installed:
   ```bash
   sudo apt-get install tcpdump   # For Debian/Ubuntu
   sudo yum install tcpdump       # For RHEL/CentOS
   ```

4. Run the application:
   ```bash
   sudo python app.py
   ```

   The application will be accessible at http://server-ip:8080

### Option 4: Systemd Service (Recommended for Production on a non-containerized host)

1. Clone the repository:
   ```bash
   git clone https://github.com/yourusername/hyyperwan.git
   ```

2. Copy the application files to the recommended location:
   ```bash
   sudo mkdir -p /opt/hyyperwan
   sudo cp -r hyyperwan/* /opt/hyyperwan/
   ```

3. Install the required dependencies:
   ```bash
   sudo pip install -r /opt/hyyperwan/requirements.txt
   ```

4. Copy the systemd service file (choose HTTP or HTTPS):
   For HTTPS (recommended, uses `/opt/hyyperwan/certificates`):
   ```bash
   sudo cp systemctl/hyyperwan.service.https /etc/systemd/system/hyyperwan.service
   ```
   For HTTP:
   ```bash
   sudo cp systemctl/hyyperwan.service.http /etc/systemd/system/hyyperwan.service
   ```
   *(Ensure the paths to certificates in `hyyperwan.service.https` are correct if you customize them)*

5. Reload systemd, enable and start the service:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable hyyperwan.service
   sudo systemctl start hyyperwan.service
   ```

6. Check the status:
   ```bash
   sudo systemctl status hyyperwan.service
   ```

   The application will be accessible at `http://server-ip:8080` (for HTTP service) or `https://server-ip:8443` (for HTTPS service).

## Configuration

### Environment Variables

You can customize the application behavior using environment variables in the `.env` file:

- `FLASK_RUN_HOST`: Host to listen on (default: 0.0.0.0)
- `FLASK_RUN_PORT`: Port to listen on (default: 8080)
- `USE_HTTPS`: Whether to use HTTPS (default: false)
- `SSL_CERT_PATH`: Path to SSL certificate (default: certificates/cert.pem)
- `SSL_KEY_PATH`: Path to SSL key (default: certificates/key.pem)

### HTTPS Configuration

To enable HTTPS:

1. Create certificates directory (if it doesn't exist):
   ```bash
   mkdir -p certificates
   ```

2. Generate self-signed certificates:
   ```bash
   openssl req -x509 -newkey rsa:4096 -nodes -out certificates/cert.pem -keyout certificates/key.pem -days 365
   ```

3. Update the `.env` file:
   ```
   USE_HTTPS=true
   SSL_CERT_PATH=certificates/cert.pem
   SSL_KEY_PATH=certificates/key.pem
   ```

### Systemd Service Configuration

The systemd service file (`/etc/systemd/system/hyyperwan.service`) can be modified to adjust settings:

```ini
[Service]
# ...existing code...
Environment="FLASK_RUN_HOST=0.0.0.0"
Environment="FLASK_RUN_PORT=8443"
Environment="USE_HTTPS=true"
Environment="SSL_CERT_PATH=/opt/hyyperwan/certificates/cert.pem"
Environment="SSL_KEY_PATH=/opt/hyyperwan/certificates/key.pem"
# ...existing code...
```

After modifying the service file, reload and restart the service:
```bash
sudo systemctl daemon-reload
sudo systemctl restart hyyperwan.service
```

### HTTP Service Alternative

For users who prefer running the application over HTTP rather than HTTPS, an alternative service file (`hyyperwan.service.http`) is provided:

1. Copy the HTTP service file to systemd:
   ```bash
   sudo cp /path/to/hyyperwan/hyyperwan.service.http /etc/systemd/system/hyyperwan.service
   ```
   
   Note: Replace `/path/to/hyyperwan/` with the actual path where you installed the application. If you followed the recommended installation, this would be `/opt/hyyperwan/`

2. Alternatively, to maintain both services side by side:
   ```bash
   sudo cp /path/to/hyyperwan/hyyperwan.service.http /etc/systemd/system/hyyperwan-http.service
   ```

3. Reload systemd and enable/start the service:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable hyyperwan.service
   sudo systemctl start hyyperwan.service
   ```

The HTTP service runs on port 8080 by default. If you need to modify paths or other settings in the service file, edit it before copying to the systemd directory.

## Usage

1. Access the web interface at http://server-ip:8080 (or https://server-ip:8443 if HTTPS is enabled)

2. For each network interface, you can:
   - Set latency (e.g., 100ms)
   - Set jitter (e.g., 20ms)
   - Set packet loss (e.g., 5%)
   - Create custom aliases for easier identification
   - Capture packets for network analysis

3. Click "Apply" to set the selected network conditions

4. Click "Remove" to clear conditions for a specific interface

5. Click "Reset All Interfaces" to clear conditions from all interfaces

## Interface Aliases

You can add user-friendly names to interfaces for easier identification:

1. Click "Add Alias" next to an interface name
2. Enter a descriptive name for the interface
3. Click "Save"

Aliases are stored persistently and will be remembered across application restarts.

## Packet Capture

HyyperWAN includes a packet capture feature to help analyze network traffic:

1. Click "Capture" next to the interface you want to monitor
2. In the popup window, configure your capture filters:
   - **Host Filter**: Capture packets from/to specific IP addresses (comma separated)
   - **Network Filter**: Capture packets for specific networks (e.g., 192.168.1.0/24)
   - **Port Filter**: Capture packets for specific ports (comma separated)
   - For each filter type, select whether to use AND or OR logic for multiple values

3. Click "Start Capture" to begin capturing packets
   - Captures are limited to 10,000 packets maximum to prevent disk space issues
   - A counter will show the elapsed capture time

4. Click "Stop & Download" to end the capture and download the .pcap file
   - The file can be analyzed with tools like Wireshark or tcpdump

Notes:
- You must run HyyperWAN with root/sudo privileges for packet capture to work
- Packet capture files are temporarily stored in `/tmp/hyyperwan_pcaps/`
- Files are automatically deleted after download to preserve disk space
- Closing the browser window will automatically stop any active captures

## Troubleshooting

If you encounter issues:

1. Check the application logs:
   ```bash
   cat app.log
   ```

2. For systemd service issues:
   ```bash
   sudo journalctl -u hyyperwan.service -n 50
   ```

3. Verify that tc commands work manually:
   ```bash
   sudo tc qdisc show
   ```

4. Make sure you have root/sudo privileges

## License

[Add your license information here]

## Contributors

[Add contributor information here]