# HyyperWAN

HyyperWAN is a web application for controlling network conditions (latency, jitter, and packet loss) on Linux systems using tc qdisc.

## Features

- Set and control network latency
- Add network jitter
- Simulate packet loss
- Customize interface aliases for better identification
- Support for both HTTP and HTTPS
- View current network condition settings per interface

## Requirements

- Linux operating system
- Python 3.8 or higher
- Root privileges (for tc commands)
- iproute2 package (for the `ip` command)

## Installation

### Option 1: Direct Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/yourusername/hyyperwan.git
   cd hyyperwan
   ```

2. Install the required dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Run the application:
   ```bash
   sudo python app.py
   ```

   The application will be accessible at http://server-ip:8080

### Option 2: Systemd Service (Recommended for Production)

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

4. Copy the systemd service file:
   ```bash
   sudo cp /opt/hyyperwan/hyyperwan.service /etc/systemd/system/
   ```

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

   The application will be accessible at http://server-ip:8443 (if HTTPS is enabled) or http://server-ip:8080

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

## Usage

1. Access the web interface at http://server-ip:8080 (or https://server-ip:8443 if HTTPS is enabled)

2. For each network interface, you can:
   - Set latency (e.g., 100ms)
   - Set jitter (e.g., 20ms)
   - Set packet loss (e.g., 5%)
   - Create custom aliases for easier identification

3. Click "Apply" to set the selected network conditions

4. Click "Remove" to clear conditions for a specific interface

5. Click "Reset All Interfaces" to clear conditions from all interfaces

## Interface Aliases

You can add user-friendly names to interfaces for easier identification:

1. Click "Add Alias" next to an interface name
2. Enter a descriptive name for the interface
3. Click "Save"

Aliases are stored persistently and will be remembered across application restarts.

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