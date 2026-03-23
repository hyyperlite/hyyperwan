import os
import logging
import shutil
import subprocess
import threading
import atexit
import json
import time
import re
import socket
import uuid
import signal

from flask import Flask, render_template, request, redirect, url_for, flash, send_file, jsonify
from flask import send_from_directory

# Configure logging as early as possible
logging.basicConfig(
    filename='app.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(process)d - %(threadName)s - %(module)s - %(funcName)s - %(lineno)d - %(message)s'
)

# Make dotenv optional
try:
    from dotenv import load_dotenv
    if load_dotenv(): # load_dotenv() returns True if a .env file was loaded
        logging.info("Successfully loaded environment variables from .env file.")
    else:
        # This means .env was not found, or it was empty. Not an error.
        logging.info(".env file not found or is empty. Continuing without it.")
except ImportError:
    # This means python-dotenv is not installed.
    logging.warning("python-dotenv not installed. Cannot load .env file. Consider installing with 'pip install python-dotenv'.")

app = Flask(__name__)
app.secret_key = 'your_secret_key'  # Needed for flashing messages

# Path to store interface aliases
ALIASES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'interface_aliases.json')

# ---------------------------------------------------------------------------
# Admin config — persistent settings stored in a JSON file.
# The file location can be overridden via ADMIN_CONFIG_PATH so that Docker
# users can mount a volume and have settings survive container restarts.
# Env vars supply the baseline defaults when no config file exists yet.
# ---------------------------------------------------------------------------
ADMIN_CONFIG_PATH = os.environ.get(
    'ADMIN_CONFIG_PATH',
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'admin_config.json')
)
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', '')  # empty = no auth required

def _env_list(var, default=''):
    """Parse a comma-separated env var into a list of stripped strings."""
    return [i.strip() for i in os.environ.get(var, default).split(',') if i.strip()]

def load_admin_config():
    """Load admin config from file, falling back to env var defaults."""
    defaults = {
        'hidden_interfaces': _env_list('IGNORE_INTERFACES', 'docker0'),
        'disable_tools_column': os.environ.get('DISABLE_TOOLS_COLUMN', 'false').lower() == 'true',
        'default_theme': os.environ.get('DEFAULT_THEME', ''),
        'disable_routes': False,
        'disable_interface_ips': False,
        'disable_mtu': False,
        'interface_overrides': {},  # keyed by interface name
    }
    if os.path.exists(ADMIN_CONFIG_PATH):
        try:
            with open(ADMIN_CONFIG_PATH) as f:
                saved = json.load(f)
            # Merge: saved values override defaults
            defaults.update(saved)
        except Exception as e:
            logging.error(f"Error reading admin config: {e}")
    return defaults

def save_admin_config(cfg):
    """Persist admin config to file, creating parent dirs as needed."""
    try:
        os.makedirs(os.path.dirname(ADMIN_CONFIG_PATH), exist_ok=True)
        with open(ADMIN_CONFIG_PATH, 'w') as f:
            json.dump(cfg, f, indent=4)
        return True, None
    except Exception as e:
        logging.error(f"Error saving admin config: {e}")
        return False, str(e)

def get_iface_override(cfg, iface, key, default=False):
    """Return a per-interface override value, falling back to default."""
    return cfg.get('interface_overrides', {}).get(iface, {}).get(key, default)

# Interfaces to not display in the UI — driven by admin config (or env var default)
# This is re-read on each request via load_admin_config() so it stays current.
IGNORED_INTERFACES = _env_list('IGNORE_INTERFACES', 'docker0')

# Path to store temporary pcap files - use /tmp directory
PCAP_DIR = '/tmp/hyyperwan_pcaps'
# Create the directory if it doesn't exist
if not os.path.exists(PCAP_DIR):
    os.makedirs(PCAP_DIR)

# Dictionary to track active captures and completed ones
active_captures = {}
completed_captures = {}

def cleanup_pcap_file(filepath, delay=30):
    """Delete a pcap file after a delay to ensure download completes"""
    def delete_file():
        try:
            time.sleep(delay)
            if os.path.exists(filepath):
                os.unlink(filepath)
                logging.info(f"Deleted packet capture file: {filepath}")
        except Exception as e:
            logging.error(f"Error deleting packet capture file: {str(e)}")
    
    # Start a thread to delete the file after delay
    threading.Thread(target=delete_file).start()

def load_interface_aliases():
    """
    Load interface aliases from the JSON file.
    Returns a dictionary mapping interface names to their aliases.
    """
    try:
        if os.path.exists(ALIASES_FILE):
            with open(ALIASES_FILE, 'r') as f:
                return json.load(f)
        else:
            return {}
    except Exception as e:
        logging.error(f"Error loading interface aliases: {str(e)}")
        return {}

def save_interface_aliases(aliases):
    """
    Save interface aliases to the JSON file.
    """
    try:
        with open(ALIASES_FILE, 'w') as f:
            json.dump(aliases, f, indent=2)
    except Exception as e:
        logging.error(f"Error saving interface aliases: {str(e)}")

def get_interface_alias(interface_name):
    """
    Get the alias for an interface, or return the original name if no alias exists.
    """
    aliases = load_interface_aliases()
    return aliases.get(interface_name, interface_name)

def log_command(command, output):
    logging.info(f"Command: {' '.join(command)}")
    logging.info(f"Output: {output}")

# Validation functions
def validate_latency_jitter(value, field_name):
    """
    Validate latency and jitter inputs:
    - Should be a whole number
    - Up to 6 digits max
    - Can optionally end with 'ms' or 'MS'
    
    Returns (is_valid, cleaned_value, error_message)
    """
    if not value or value.strip() == '':
        return True, None, None  # Empty value is valid
    
    # Remove 'ms' or 'MS' if present
    if value.upper().endswith('MS'):
        value = value[:-2]
    
    # Check if it's a valid whole number
    if not value.isdigit():
        return False, None, f"{field_name} must be a whole number"
    
    # Check length (up to 6 digits)
    if len(value) > 6:
        return False, None, f"{field_name} cannot exceed 6 digits"
    
    return True, value, None

def validate_loss(value):
    """
    Validate loss input:
    - Should be a whole number
    - Between 0 and 100
    - Can optionally end with '%'
    
    Returns (is_valid, cleaned_value, error_message)
    """
    if not value or value.strip() == '':
        return True, None, None  # Empty value is valid
    
    # Remove '%' if present
    if value.endswith('%'):
        value = value[:-1]
    
    # Check if it's a valid whole number
    if not value.isdigit():
        return False, None, "Loss must be a whole number"
    
    # Check range (0-100)
    value_int = int(value)
    if value_int < 0 or value_int > 100:
        return False, None, "Loss must be between 0 and 100"
    
    return True, value, None

def validate_bandwidth(value):
    """
    Validate bandwidth input.
    Accepts: 10mbit, 100kbit, 1gbit (case-insensitive), or plain number (treated as mbit).
    Returns (is_valid, normalized_value, error_message)
    normalized_value is like "10mbit", "100kbit", "1gbit" (lowercase)
    """
    if not value or value.strip() == '':
        return True, None, None

    value = value.strip().lower()
    match = re.match(r'^(\d+(?:\.\d+)?)(kbit|mbit|gbit)?$', value)
    if not match:
        return False, None, "Bandwidth must be a number followed by kbit, mbit, or gbit (e.g., 10mbit)"

    num_str = match.group(1)
    unit = match.group(2) or 'mbit'

    try:
        num = float(num_str)
    except ValueError:
        return False, None, "Bandwidth value is not a valid number"

    if num <= 0:
        return False, None, "Bandwidth must be greater than 0"

    return True, f"{num_str}{unit}", None


def compute_tbf_burst(bandwidth_str):
    """Calculate an appropriate TBF burst size for a given bandwidth string like '10mbit'."""
    match = re.match(r'^(\d+(?:\.\d+)?)(kbit|mbit|gbit)$', bandwidth_str.lower())
    if not match:
        return '32kbit'

    val = float(match.group(1))
    unit = match.group(2)
    multipliers = {'kbit': 1_000, 'mbit': 1_000_000, 'gbit': 1_000_000_000}
    bits = val * multipliers[unit]

    # burst >= rate/HZ; HZ is typically 250-1000; use rate/250 with 4KB minimum
    burst_bytes = max(int(bits / 250 / 8), 4096)

    if burst_bytes >= 1_048_576:
        return f"{int(burst_bytes / 1_048_576)}mb"
    elif burst_bytes >= 1024:
        return f"{int(burst_bytes / 1024)}kb"
    return f"{burst_bytes}b"



def list_interfaces():
    interfaces = []
    try:
        result = subprocess.run(['ip', '-j', 'addr'], capture_output=True, text=True)
        output = result.stdout
        log_command(['ip', '-j', 'addr'], output)
        
        # Load interface aliases; build ignored set (always include 'lo')
        aliases = load_interface_aliases()
        cfg = load_admin_config()
        ignored_interfaces_set = set(cfg.get('hidden_interfaces', IGNORED_INTERFACES) + ['lo'])
        
        try:
            data = json.loads(output)
            
            for interface in data:
                interface_name = interface['ifname']
                # Skip ignored interfaces
                if interface_name in ignored_interfaces_set:
                    logging.info(f"Skipping ignored interface: {interface_name}")
                    continue
                
                ip_address = None
                for addr_info in interface.get('addr_info', []):
                    if addr_info['family'] == 'inet':
                        ip_address = addr_info['local']
                        break
                if ip_address:
                    try:
                        # Get current network condition settings
                        latency, loss, jitter, bandwidth = get_qdisc_settings(interface_name)
                        nat_status = get_nat_status(interface_name)
                        interfaces.append({
                            'name': interface_name,
                            'alias': aliases.get(interface_name, ''),
                            'ip': ip_address,
                            'latency': latency,
                            'loss': loss,
                            'jitter': jitter,
                            'bandwidth': bandwidth,
                            'nat_status': nat_status,
                        })
                    except Exception as e:
                        logging.error(f"Error getting settings for interface {interface_name}: {str(e)}")
                        interfaces.append({
                            'name': interface_name,
                            'alias': aliases.get(interface_name, ''),
                            'ip': ip_address,
                            'latency': '0ms',
                            'loss': '0%',
                            'jitter': '0ms',
                            'bandwidth': None,
                            'nat_status': False,
                        })
        
        except json.JSONDecodeError as e:
            logging.error(f"Error parsing JSON output from ip command: {str(e)}")
            flash("Error retrieving network interfaces", "error")
    
    except Exception as e:
        logging.error(f"Error listing interfaces: {str(e)}")
        flash("Error retrieving network interfaces", "error")
    
    return interfaces

def get_latency(interface):
    try:
        result = subprocess.run(['tc', 'qdisc', 'show', 'dev', interface], capture_output=True, text=True)
        output = result.stdout
        log_command(['tc', 'qdisc', 'show', 'dev', interface], output)
        match = re.search(r'delay (\d+ms|\d+us)', output)
        return match.group(1) if match else '0ms'
    except Exception as e:
        logging.error(f"Error getting latency for interface {interface}: {str(e)}")
        return '0ms'

def get_loss(interface):
    try:
        result = subprocess.run(['tc', 'qdisc', 'show', 'dev', interface], capture_output=True, text=True)
        output = result.stdout
        log_command(['tc', 'qdisc', 'show', 'dev', interface], output)
        match = re.search(r'loss (\d+)%', output)
        return match.group(1) + '%' if match else '0%'
    except Exception as e:
        logging.error(f"Error getting loss for interface {interface}: {str(e)}")
        return '0%'

def get_qdisc_settings(interface):
    """
    Return (latency, loss, jitter, bandwidth) for the interface.
    bandwidth is None if no rate limit is set, otherwise a string like '10Mbit'.
    """
    try:
        result = subprocess.run(['sudo', 'tc', 'qdisc', 'show', 'dev', interface], capture_output=True, text=True)
        output = result.stdout
        log_command(['sudo', 'tc', 'qdisc', 'show', 'dev', interface], output)

        latency_match = re.search(r'delay (\d+(?:ms|us))', output)
        jitter_match  = re.search(r'delay \d+(?:ms|us)\s+(\d+(?:ms|us))', output)
        loss_match    = re.search(r'loss (\d+(?:\.\d+)?)%', output)

        latency = latency_match.group(1) if latency_match else '0ms'
        loss    = loss_match.group(1) + '%' if loss_match else '0%'
        jitter  = jitter_match.group(1) if jitter_match else '0ms'

        if jitter_match:
            logging.info(f"Captured jitter: {jitter}")
        else:
            logging.info("No jitter found in tc output")

        # Detect bandwidth limit
        bandwidth = None
        if 'tbf' in output:
            # e.g. "rate 10Mbit burst 32Kb lat 400ms"
            rate_match = re.search(r'rate (\S+)', output)
            if rate_match:
                bandwidth = rate_match.group(1)
        elif 'htb' in output:
            # Rate is on the class, not the qdisc line
            class_result = subprocess.run(
                ['sudo', 'tc', 'class', 'show', 'dev', interface],
                capture_output=True, text=True
            )
            log_command(['sudo', 'tc', 'class', 'show', 'dev', interface], class_result.stdout)
            # Pick first class rate (our classid 1:10)
            rate_match = re.search(r'class htb[^\n]+rate (\S+)', class_result.stdout)
            if rate_match:
                bandwidth = rate_match.group(1)

        return latency, loss, jitter, bandwidth
    except subprocess.SubprocessError as e:
        logging.error(f"Error executing tc command for interface {interface}: {str(e)}")
        return '0ms', '0%', '0ms', None
    except Exception as e:
        logging.error(f"Error getting qdisc settings for interface {interface}: {str(e)}")
        return '0ms', '0%', '0ms', None

def apply_qdisc(interface, latency=None, loss=None, jitter=None, bandwidth=None):
    """
    Apply network conditions to an interface using tc qdisc.

    Strategy:
      - Tear down the existing root qdisc unconditionally (ignore errors — there may be none).
      - Rebuild from scratch based on the merged desired state.

    Cases handled:
      1. netem only (latency/loss/jitter, no bandwidth)
      2. bandwidth only (TBF)
      3. bandwidth + netem (HTB root → netem leaf)
    """
    try:
        alias = get_interface_alias(interface)
        display_name = f"{interface} ({alias})" if alias and alias != interface else interface

        # Retrieve current settings and merge
        current_latency, current_loss, current_jitter, current_bandwidth = get_qdisc_settings(interface)

        latency   = latency   if latency   is not None else current_latency
        loss      = loss      if loss      is not None else current_loss
        jitter    = jitter    if jitter    is not None else current_jitter
        bandwidth = bandwidth if bandwidth is not None else current_bandwidth

        # Normalise latency / jitter units
        if latency and not latency.endswith(('ms', 'us')):
            latency += 'ms'
        if jitter and not jitter.endswith(('ms', 'us')):
            jitter += 'ms'

        # netem requires a latency value when jitter is set
        if jitter and jitter != '0ms' and (not latency or latency == '0ms'):
            latency = '1ms'
            logging.info("Setting minimal 1ms latency to satisfy netem jitter requirement")

        has_netem = (latency and latency != '0ms') or (loss and loss != '0%') or (jitter and jitter != '0ms')
        has_bw    = bool(bandwidth)

        # --- Step 1: tear down existing root qdisc (cascades child classes/qdiscs) ---
        del_result = subprocess.run(
            ['sudo', 'tc', 'qdisc', 'del', 'dev', interface, 'root'],
            capture_output=True, text=True
        )
        # returncode != 0 is fine here — means no custom qdisc was present
        log_command(['sudo', 'tc', 'qdisc', 'del', 'dev', interface, 'root'], del_result.stdout)

        # --- Step 2: rebuild ---
        errors = []

        def run_tc(cmd):
            r = subprocess.run(cmd, capture_output=True, text=True)
            log_command(cmd, r.stdout)
            if r.returncode != 0:
                errors.append(r.stderr.strip())
            return r.returncode == 0

        if has_bw and has_netem:
            # HTB root + netem leaf
            run_tc(['sudo', 'tc', 'qdisc', 'add', 'dev', interface,
                    'root', 'handle', '1:0', 'htb', 'default', '10'])
            run_tc(['sudo', 'tc', 'class', 'add', 'dev', interface,
                    'parent', '1:0', 'classid', '1:10', 'htb', 'rate', bandwidth])
            netem_cmd = ['sudo', 'tc', 'qdisc', 'add', 'dev', interface,
                         'parent', '1:10', 'handle', '20:0', 'netem']
            if latency and latency != '0ms':
                if jitter and jitter != '0ms':
                    netem_cmd.extend(['delay', latency, jitter])
                else:
                    netem_cmd.extend(['delay', latency])
            if loss and loss != '0%':
                netem_cmd.extend(['loss', loss.replace('%', '')])
            run_tc(netem_cmd)

        elif has_bw:
            # TBF for bandwidth-only
            burst = compute_tbf_burst(bandwidth)
            run_tc(['sudo', 'tc', 'qdisc', 'add', 'dev', interface,
                    'root', 'tbf', 'rate', bandwidth, 'burst', burst, 'latency', '400ms'])

        elif has_netem:
            netem_cmd = ['sudo', 'tc', 'qdisc', 'add', 'dev', interface, 'root', 'netem']
            if latency and latency != '0ms':
                if jitter and jitter != '0ms':
                    netem_cmd.extend(['delay', latency, jitter])
                else:
                    netem_cmd.extend(['delay', latency])
            if loss and loss != '0%':
                netem_cmd.extend(['loss', loss.replace('%', '')])
            run_tc(netem_cmd)

        if errors:
            flash(f"Error applying conditions to {display_name}: {'; '.join(errors)}", "error")
            logging.error(f"tc errors on {interface}: {errors}")
        else:
            flash(f"Network conditions applied to {display_name}", "success")

    except Exception as e:
        flash(f"Error applying network conditions to {interface}: {str(e)}", "error")
        logging.error(f"Error in apply_qdisc for interface {interface}: {str(e)}")

def remove_degradations(interface):
    """Remove ALL tc qdisc settings (netem, TBF, HTB) from an interface."""
    try:
        alias = get_interface_alias(interface)
        display_name = f"{interface} ({alias})" if alias and alias != interface else interface

        check_result = subprocess.run(
            ['sudo', 'tc', 'qdisc', 'show', 'dev', interface],
            capture_output=True, text=True
        )
        log_command(['sudo', 'tc', 'qdisc', 'show', 'dev', interface], check_result.stdout)

        has_custom = any(kw in check_result.stdout for kw in ('netem', 'htb', 'tbf'))
        if has_custom:
            # Deleting root cascades all child classes and qdiscs
            result = subprocess.run(
                ['sudo', 'tc', 'qdisc', 'del', 'dev', interface, 'root'],
                capture_output=True, text=True
            )
            log_command(['sudo', 'tc', 'qdisc', 'del', 'dev', interface, 'root'], result.stdout)
            if result.returncode != 0:
                flash(f"Error removing qdisc from {display_name}: {result.stderr}", "error")
                logging.error(f"Error removing qdisc from {interface} (rc={result.returncode}): {result.stderr}")
            else:
                flash(f"Network conditions removed from {display_name}", "success")
        else:
            logging.info(f"No custom qdisc to remove on {interface}")
    except subprocess.SubprocessError as e:
        flash(f"Failed to execute tc command for {interface}: {str(e)}", "error")
        logging.error(f"Subprocess error removing qdisc from {interface}: {str(e)}")
    except Exception as e:
        flash(f"Error removing network conditions from {interface}: {str(e)}", "error")
        logging.error(f"Error in remove_degradations for {interface}: {str(e)}")

def is_tcpdump_available():
    """Check if tcpdump is installed on the system"""
    try:
        result = subprocess.run(['which', 'tcpdump'], capture_output=True, text=True)
        logging.info(f"tcpdump availability check: {'Available' if result.returncode == 0 else 'Not available'}")
        return result.returncode == 0
    except Exception as e:
        logging.error(f"Error checking for tcpdump availability: {str(e)}")
        return False

def is_tc_available():
    """Check if tc utility is installed on the system"""
    try:
        result = subprocess.run(['which', 'tc'], capture_output=True, text=True)
        logging.info(f"tc utility availability check: {'Available' if result.returncode == 0 else 'Not available'}")
        return result.returncode == 0
    except Exception as e:
        logging.error(f"Error checking for tc availability: {str(e)}")
        return False

def is_ip_available():
    """Check if ip command is installed on the system (from iproute2 package)"""
    try:
        result = subprocess.run(['which', 'ip'], capture_output=True, text=True)
        logging.info(f"ip command availability check: {'Available' if result.returncode == 0 else 'Not available'}")
        return result.returncode == 0
    except Exception as e:
        logging.error(f"Error checking for ip command availability: {str(e)}")
        return False

def is_running_in_container():
    """Check if the application is running inside a Docker container."""
    # This is a common way to check, but might not be 100% foolproof in all container environments.
    return os.path.exists('/.dockerenv')

def is_iptables_available():
    """Check if iptables (and nsenter if in container) is available."""
    iptables_path = shutil.which('iptables')
    if not iptables_path:
        logging.warning("iptables command not found.")
        return False

    if is_running_in_container():
        nsenter_path = shutil.which('nsenter')
        if not nsenter_path:
            logging.warning("Running in container, but nsenter command not found. Cannot manage host NAT rules.")
            return False
        logging.info("iptables and nsenter are available in the container. Host NAT management will be attempted.")
    else:
        logging.info("iptables is available on the host.")
    return True

def get_nat_status(interface):
    """Check if NAT (Masquerade) is enabled for the given interface."""
    if not is_iptables_available():
        return False
    
    base_cmd_parts = ['iptables', '-t', 'nat', '-C', 'POSTROUTING', '-o', interface, '-j', 'MASQUERADE']
    final_cmd = []
    log_context_message = ""

    if is_running_in_container():
        final_cmd = ['nsenter', '--target', '1', '--net'] + base_cmd_parts
        log_context_message = "host (via nsenter from container)"
    else:
        final_cmd = ['sudo'] + base_cmd_parts
        log_context_message = "host (direct sudo)"

    try:
        result = subprocess.run(final_cmd, capture_output=True, text=True, check=False)
        log_command(final_cmd, f"Return code: {result.returncode}, Stdout: {result.stdout.strip()}, Stderr: {result.stderr.strip()} (Context: {log_context_message})")
        return result.returncode == 0
    except Exception as e:
        logging.error(f"Error checking NAT status for interface {interface} (Context: {log_context_message}): {str(e)}")
        return False

# ---------------------------------------------------------------------------
# Route table management helpers
# ---------------------------------------------------------------------------

def parse_routes(ip_version=4):
    """
    Run 'ip [−6] route show' and return a list of dicts with parsed fields.
    Each dict has: destination, gateway, interface, proto, metric, scope, src, raw.
    """
    try:
        cmd = ['ip', '-6', 'route', 'show'] if ip_version == 6 else ['ip', 'route', 'show']
        result = subprocess.run(cmd, capture_output=True, text=True)
        log_command(cmd, result.stdout)
        routes = []
        for line in result.stdout.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            route = {
                'destination': '', 'gateway': '', 'interface': '',
                'proto': '', 'metric': '', 'scope': '', 'src': '', 'raw': line,
            }
            parts = line.split()
            if parts:
                route['destination'] = parts[0]
            i = 1
            while i < len(parts):
                key = parts[i]
                if key in ('via', 'dev', 'proto', 'metric', 'scope', 'src') and i + 1 < len(parts):
                    field_map = {
                        'via': 'gateway', 'dev': 'interface', 'proto': 'proto',
                        'metric': 'metric', 'scope': 'scope', 'src': 'src',
                    }
                    route[field_map[key]] = parts[i + 1]
                    i += 2
                else:
                    i += 1
            routes.append(route)
        return routes
    except Exception as e:
        logging.error(f"Error parsing IPv{ip_version} routes: {str(e)}")
        return []


def exec_ip_route(args, ip_version=4):
    """
    Run 'sudo ip [−6] route <args>' and return (success, stderr).
    With --net=host in Docker, this modifies the host routing table directly.
    """
    cmd = ['sudo', 'ip']
    if ip_version == 6:
        cmd.append('-6')
    cmd.extend(['route'] + args)
    result = subprocess.run(cmd, capture_output=True, text=True)
    log_command(cmd, result.stdout + result.stderr)
    return result.returncode == 0, result.stderr.strip()


# ---------------------------------------------------------------------------
# Flask routes — main app
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    try:
        ip_available = is_ip_available()
        iptables_available = is_iptables_available()
        interfaces = list_interfaces() if ip_available else []
        hostname = socket.gethostname()
        tcpdump_available = is_tcpdump_available()
        tc_available = is_tc_available()
        cfg = load_admin_config()

        return render_template('index.html', interfaces=interfaces, hostname=hostname,
                              tcpdump_available=tcpdump_available, tc_available=tc_available,
                              ip_available=ip_available, iptables_available=iptables_available,
                              tools_column_disabled=cfg.get('disable_tools_column', False),
                              iface_overrides=cfg.get('interface_overrides', {}))
    except Exception as e:
        logging.error(f"Error in index route: {str(e)}")
        flash("An error occurred while loading the page", "error")
        hostname = "Unknown"
        return render_template('index.html', interfaces=[], hostname=hostname,
                              tcpdump_available=False, tc_available=False,
                              ip_available=False, iptables_available=False,
                              tools_column_disabled=False, iface_overrides={})

@app.route('/favicon.png')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static', 'img'),
                              'favicon.png', mimetype='image/png')

@app.route('/apply', methods=['POST'], endpoint='apply_interface')
def apply():
    try:
        # Check if interface is in the form data
        if 'interface' not in request.form:
            flash("No interface selected", "error")
            return redirect(url_for('index'))
            
        interface = request.form['interface'].split(' ')[0]  # Extract the interface name
        
        # Get form values
        latency = request.form.get('latency')
        loss = request.form.get('loss')
        jitter = request.form.get('jitter')
        bw_value = request.form.get('bandwidth_value', '').strip()
        bw_unit  = request.form.get('bandwidth_unit', 'mbit').strip()

        # Combine bandwidth value + unit
        bandwidth_raw = f"{bw_value}{bw_unit}" if bw_value else None

        # Validate inputs
        validation_errors = []

        latency_valid, latency_clean, latency_error = validate_latency_jitter(latency, 'Latency')
        if not latency_valid:
            validation_errors.append(latency_error)
            latency = None
        else:
            latency = latency_clean

        jitter_valid, jitter_clean, jitter_error = validate_latency_jitter(jitter, 'Jitter')
        if not jitter_valid:
            validation_errors.append(jitter_error)
            jitter = None
        else:
            jitter = jitter_clean

        loss_valid, loss_clean, loss_error = validate_loss(loss)
        if not loss_valid:
            validation_errors.append(loss_error)
            loss = None
        else:
            loss = loss_clean

        bw_valid, bw_clean, bw_error = validate_bandwidth(bandwidth_raw)
        if not bw_valid:
            validation_errors.append(bw_error)
            bandwidth_raw = None
        else:
            bandwidth_raw = bw_clean  # normalized, e.g. "10mbit"

        if validation_errors:
            for error in validation_errors:
                flash(error, 'error')
            return redirect(url_for('index'))

        apply_qdisc(interface, latency, loss, jitter, bandwidth_raw)
        
        return redirect(url_for('index'))
    except Exception as e:
        logging.error(f"Error in apply route: {str(e)}")
        flash(f"An unexpected error occurred: {str(e)}", "error")
        return redirect(url_for('index'))

@app.route('/remove', methods=['POST'], endpoint='remove_interface')
def remove():
    try:
        # Check if interface is in the form data
        if 'interface' not in request.form:
            flash("No interface selected", "error")
            return redirect(url_for('index'))
            
        interface = request.form['interface'].split(' ')[0]  # Extract the interface name
        remove_degradations(interface)
        return redirect(url_for('index'))
    except Exception as e:
        logging.error(f"Error in remove route: {str(e)}")
        flash(f"An unexpected error occurred: {str(e)}", "error")
        return redirect(url_for('index'))

@app.route('/reset_all', methods=['POST'], endpoint='reset_all_interfaces')
def reset_all():
    try:
        # Get all interfaces and remove degradations for each
        interfaces = list_interfaces()
        reset_count = 0
        reset_interfaces = []
        
        for interface_info in interfaces:
            try:
                interface_name = interface_info['name']
                
                check_result = subprocess.run(
                    ['sudo', 'tc', 'qdisc', 'show', 'dev', interface_name],
                    capture_output=True, text=True
                )

                if any(kw in check_result.stdout for kw in ('netem', 'htb', 'tbf')):
                    remove_degradations(interface_name)
                    reset_count += 1
                    
                    # Format the interface name with alias for display
                    alias = interface_info.get('alias', '')
                    if alias and alias != interface_name:
                        reset_interfaces.append(f"{interface_name} ({alias})")
                    else:
                        reset_interfaces.append(interface_name)
                else:
                    logging.info(f"No netem qdisc to remove on interface {interface_name}")
            except Exception as e:
                logging.error(f"Failed to reset interface {interface_info['name']}: {str(e)}")
        
        if reset_count > 0:
            # If fewer than 4 interfaces were reset, list them all
            if reset_count <= 3:
                interfaces_list = ", ".join(reset_interfaces)
                flash(f"Successfully reset network conditions on {reset_count} interfaces: {interfaces_list}", "success")
            else:
                flash(f"Successfully reset network conditions on {reset_count} interfaces", "success")
        else:
            flash("No active network conditions found to reset", "info")
            
        return redirect(url_for('index'))
    except Exception as e:
        logging.error(f"Error in reset_all route: {str(e)}")
        flash(f"An unexpected error occurred: {str(e)}", "error")
        return redirect(url_for('index'))

@app.route('/update_alias', methods=['POST'], endpoint='update_interface_alias')
def update_alias():
    try:
        # Check if required data is present
        if 'interface' not in request.form or 'alias' not in request.form:
            flash("Missing interface name or alias", "error")
            return redirect(url_for('index'))
            
        interface_name = request.form['interface']
        alias = request.form['alias'].strip()
        
        # Load current aliases
        aliases = load_interface_aliases()
        
        # Get current alias if it exists
        old_alias = aliases.get(interface_name, '')
        
        # Update the alias
        if alias:  # If alias is not empty
            aliases[interface_name] = alias
            if old_alias:
                flash(f"Alias for {interface_name} changed from '{old_alias}' to '{alias}'", "success")
            else:
                flash(f"Alias for {interface_name} set to '{alias}'", "success")
        else:  # If alias is empty, remove the alias
            if interface_name in aliases:
                del aliases[interface_name]
                flash(f"Alias for {interface_name} ('{old_alias}') removed", "success")
            else:
                flash(f"No alias was set for {interface_name}", "info")
        
        # Save the updated aliases
        save_interface_aliases(aliases)
        
        return redirect(url_for('index'))
    except Exception as e:
        logging.error(f"Error updating interface alias: {str(e)}")
        flash(f"Failed to update interface alias: {str(e)}", "error")
        return redirect(url_for('index'))

@app.route('/start_capture', methods=['POST'])
def start_capture():
    try:
        interface = request.form.get('interface')
        host_filter = request.form.get('host_filter', '')
        port_filter = request.form.get('port_filter', '')
        network_filter = request.form.get('network_filter', '')
        
        # Get separate filter logic for each filter type
        host_filter_logic = request.form.get('host_filter_logic', 'or')
        network_filter_logic = request.form.get('network_filter_logic', 'or')
        port_filter_logic = request.form.get('port_filter_logic', 'or')
        
        if not interface:
            return jsonify({'success': False, 'error': 'Interface not specified'}), 400
            
        # Get interface alias for display
        alias = get_interface_alias(interface)
        display_name = f"{interface} ({alias})" if alias and alias != interface else interface
        
        # Generate unique ID for this capture
        capture_id = str(uuid.uuid4())
        pcap_file = os.path.join(PCAP_DIR, f"capture_{interface}_{capture_id}.pcap")
        
        # Ensure capture directory exists with proper permissions
        if not os.path.exists(PCAP_DIR):
            try:
                os.makedirs(PCAP_DIR, exist_ok=True)
                # Ensure directory is writable
                os.chmod(PCAP_DIR, 0o755)
                logging.info(f"Created capture directory: {PCAP_DIR}")
            except Exception as e:
                logging.error(f"Error creating capture directory: {str(e)}")
                return jsonify({'success': False, 'error': f"Cannot create capture directory: {str(e)}"}), 500
        
        # Build complex tcpdump filter expression with separate logic for each filter type
        filter_parts = []
        
        # Process host filter
        if (host_filter):
            hosts = host_filter.split(',')
            hosts = [h.strip() for h in hosts if h.strip()]
            if hosts:
                if len(hosts) == 1:
                    filter_parts.append(f"host {hosts[0]}")
                else:
                    host_expr = f" {host_filter_logic} ".join([f"host {h}" for h in hosts])
                    filter_parts.append(f"({host_expr})")
        
        # Process network filter
        if network_filter:
            networks = network_filter.split(',')
            networks = [n.strip() for n in networks if n.strip()]
            if networks:
                if len(networks) == 1:
                    filter_parts.append(f"net {networks[0]}")
                else:
                    net_expr = f" {network_filter_logic} ".join([f"net {n}" for n in networks])
                    filter_parts.append(f"({net_expr})")
        
        # Process port filter
        if port_filter:
            ports = port_filter.split(',')
            ports = [p.strip() for p in ports if p.strip()]
            if ports:
                if len(ports) == 1:
                    filter_parts.append(f"port {ports[0]}")
                else:
                    port_expr = f" {port_filter_logic} ".join([f"port {p}" for p in ports])
                    filter_parts.append(f"({port_expr})")
        
        # Combine all parts with AND logic
        filter_expr = ""
        if filter_parts:
            filter_expr = " and ".join(filter_parts)
        
        # Build tcpdump command with packet count limit
        cmd = ['sudo', 'tcpdump', '-i', interface, '-w', pcap_file, '-c', '10000']  # Base command

        # Add -Z option only if not running inside a Docker container
        if not is_running_in_container(): # MODIFIED: Use the helper function
            try:
                # Get the login name of the user running the script
                login_user = os.getlogin()
                cmd.extend(['-Z', login_user])
                logging.info(f"Running locally, adding '-Z {login_user}' to tcpdump command.")
            except OSError as e:
                # os.getlogin() can fail if not connected to a tty
                logging.warning(f"Could not get login user (os.getlogin failed: {e}). Omitting -Z option.")
        else:
            logging.info("Running inside a container, omitting '-Z' option from tcpdump command.")

        # Add filter if present
        if filter_expr:
            cmd.extend([filter_expr])
        
        logging.info(f"Starting capture with command: {' '.join(cmd)}")
        
        # Start capture process with stderr redirected to pipe for error logging
        process = subprocess.Popen(cmd, stderr=subprocess.PIPE, text=True)
        
        # Start a thread to monitor stderr for errors
        def monitor_stderr():
            for line in process.stderr:
                logging.error(f"tcpdump error: {line.strip()}")
                
        stderr_thread = threading.Thread(target=monitor_stderr)
        stderr_thread.daemon = True
        stderr_thread.start()
        
        active_captures[capture_id] = {
            'process': process,
            'interface': interface,
            'display_name': display_name,
            'file': pcap_file,
            'start_time': time.time(),
            'filter': filter_expr,
            'stderr_thread': stderr_thread
        }
        
        return jsonify({
            'success': True, 
            'capture_id': capture_id, 
            'message': f"Capture started on {display_name}"
        })
        
    except Exception as e:
        logging.error(f"Error starting packet capture: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/stop_capture/<capture_id>', methods=['POST'])
def stop_capture(capture_id):
    try:
        if capture_id not in active_captures:
            return jsonify({'success': False, 'error': 'Capture not found'}), 404
        
        capture_info = active_captures[capture_id].copy()  # Copy the info
        process = capture_info['process']
        
        # Stop the capture process
        if process.poll() is None:  # Process is still running
            process.send_signal(signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                # Force kill if still running
                process.kill()
        
        # Wait a moment for tcpdump to flush its output
        time.sleep(1)
        
        # Store info in completed_captures for download
        completed_captures[capture_id] = {
            'file': capture_info['file'],
            'interface': capture_info['interface'],
            'display_name': capture_info['display_name'],
            'timestamp': time.time()
        }
        
        # Remove from active captures
        active_captures.pop(capture_id)
        
        # Ensure file exists
        if not os.path.exists(capture_info['file']):
            logging.error(f"PCAP file not found after capture: {capture_info['file']}")
            return jsonify({'success': False, 'error': 'Capture file not created. Try again or check tcpdump installation.'}), 500
            
        # Get file size for logging
        file_size = os.path.getsize(capture_info['file'])
        logging.info(f"Capture completed. File: {capture_info['file']}, Size: {file_size} bytes")
        
        return jsonify({
            'success': True, 
            'capture_id': capture_id,
            'file': os.path.basename(capture_info['file']),
            'message': f"Capture stopped on {capture_info['display_name']}"
        })
        
    except Exception as e:
        logging.error(f"Error stopping packet capture: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/download_capture/<capture_id>', methods=['GET'])
def download_capture(capture_id):
    try:
        # Check if it's an active capture
        if capture_id in active_captures:
            return jsonify({'success': False, 'error': 'Cannot download active capture. Stop it first.'}), 400
        
        # First check if we have this capture in our completed captures
        # This is more reliable than just using the filename from the request
        if capture_id in completed_captures:
            capture_info = completed_captures[capture_id]
            filepath = capture_info['file']
            filename = os.path.basename(filepath)
            
            if os.path.exists(filepath):
                logging.info(f"Sending capture file: {filepath}")
                
                # Schedule file for cleanup after download
                cleanup_pcap_file(filepath)
                
                # After successful download, remove from completed captures
                completed_captures.pop(capture_id, None)
                
                # Return the file
                return send_file(
                    filepath,
                    as_attachment=True,
                    download_name=filename,
                    mimetype='application/vnd.tcpdump.pcap'
                )
            else:
                logging.error(f"Capture file not found at expected location: {filepath}")
        
        # Fallback to using the filename from the request
        filename = request.args.get('file')
        if not filename:
            return jsonify({'success': False, 'error': 'Filename not specified and capture ID not found in completed captures'}), 400
        
        filepath = os.path.join(PCAP_DIR, filename)
        
        if not os.path.exists(filepath):
            logging.error(f"Requested capture file not found: {filepath}")
            return jsonify({'success': False, 'error': 'Capture file not found. It may have been deleted or never created.'}), 404
        
        # Schedule file for cleanup after download
        cleanup_pcap_file(filepath)
        
        # Return the file
        return send_file(
            filepath,
            as_attachment=True,
            download_name=filename,
            mimetype='application/vnd.tcpdump.pcap'
        )
        
    except Exception as e:
        logging.error(f"Error downloading packet capture: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/toggle_nat/<interface_name>', methods=['POST'])
def toggle_nat(interface_name):
    if not is_iptables_available():
        flash("iptables (and nsenter if in container) command not found.", "error")
        return redirect(url_for('index'))

    # Honour admin per-interface NAT disable
    cfg = load_admin_config()
    if cfg.get('interface_overrides', {}).get(interface_name, {}).get('hide_nat'):
        flash(f"NAT control is disabled for {interface_name} by admin.", "error")
        return redirect(url_for('index'))

    action = request.form.get('action')
    display_name = get_interface_alias(interface_name)

    iptables_base_cmd_parts = ['iptables', '-t', 'nat']
    rule_specific_parts = ['POSTROUTING', '-o', interface_name, '-j', 'MASQUERADE']
    
    cmd_prefix_list = []
    log_context_message = ""

    if is_running_in_container():
        cmd_prefix_list = ['nsenter', '--target', '1', '--net']
        log_context_message = "host (via nsenter from container)"
    else:
        cmd_prefix_list = ['sudo']
        log_context_message = "host (direct sudo)"

    # Determine current NAT status using the correct context
    current_nat_status = get_nat_status(interface_name)

    final_cmd = []
    success_msg = ""
    error_msg = ""

    if action == 'enable':
        if current_nat_status:
            flash(f"Source NAT (Masquerade) is already enabled on {log_context_message} for {display_name}.", "info")
            return redirect(url_for('index'))
        final_cmd = cmd_prefix_list + iptables_base_cmd_parts + ['-A'] + rule_specific_parts
        success_msg = f"Source NAT (Masquerade) enabled on {log_context_message} for {display_name}."
        error_msg = f"Error enabling Source NAT on {log_context_message} for {display_name}."
    elif action == 'disable':
        if not current_nat_status:
            flash(f"Source NAT (Masquerade) is already disabled on {log_context_message} for {display_name}.", "info")
            return redirect(url_for('index'))
        final_cmd = cmd_prefix_list + iptables_base_cmd_parts + ['-D'] + rule_specific_parts
        success_msg = f"Source NAT (Masquerade) disabled on {log_context_message} for {display_name}."
        error_msg = f"Error disabling Source NAT on {log_context_message} for {display_name}."
    else:
        flash("Invalid action specified for NAT operation.", "error")
        return redirect(url_for('index'))

    try:
        result = subprocess.run(final_cmd, capture_output=True, text=True, check=False)
        log_command(final_cmd, f"Return code: {result.returncode}, Stdout: {result.stdout.strip()}, Stderr: {result.stderr.strip()} (Context: {log_context_message})")
        if result.returncode == 0:
            flash(success_msg, "success")
        else:
            flash(f"{error_msg}: {result.stderr.strip()}", "error")
            logging.error(f"{error_msg} Command: {' '.join(final_cmd)}, Output: {result.stderr.strip()}")
    except Exception as e:
        logging.error(f"Exception during NAT operation for {interface_name} (Context: {log_context_message}): {str(e)}")
        flash(f"An unexpected error occurred while toggling NAT: {str(e)}", "error")
    
    return redirect(url_for('index'))

@app.route('/routes')
def routes_page():
    try:
        routes_v4 = parse_routes(4)
        routes_v6 = parse_routes(6)
        interfaces = list_interfaces()
        hostname = socket.gethostname()
        cfg = load_admin_config()
        return render_template('routes.html', routes_v4=routes_v4, routes_v6=routes_v6,
                               interfaces=interfaces, hostname=hostname,
                               disable_routes=cfg.get('disable_routes', False))
    except Exception as e:
        logging.error(f"Error in routes_page: {str(e)}")
        flash(f"Error loading route table: {str(e)}", "error")
        return redirect(url_for('index'))


@app.route('/routes/add', methods=['POST'])
def add_route_handler():
    if load_admin_config().get('disable_routes'):
        flash("Route modifications are disabled by admin.", "error")
        return redirect(url_for('routes_page'))
    try:
        destination = request.form.get('destination', '').strip()
        gateway     = request.form.get('gateway', '').strip()
        interface   = request.form.get('interface', '').strip()
        metric      = request.form.get('metric', '').strip()
        ip_version  = int(request.form.get('ip_version', 4))

        if not destination:
            flash("Destination prefix is required", "error")
            return redirect(url_for('routes_page'))
        if not gateway and not interface:
            flash("A gateway (via) or outgoing interface must be specified", "error")
            return redirect(url_for('routes_page'))

        args = ['add', destination]
        if gateway:
            args += ['via', gateway]
        if interface:
            args += ['dev', interface]
        if metric:
            args += ['metric', metric]

        ok, err = exec_ip_route(args, ip_version)
        if ok:
            flash(
                f"Route {destination} added. "
                "Note: route changes made here are temporary and will not survive a reboot.",
                "success"
            )
        else:
            flash(f"Failed to add route {destination}: {err}", "error")
    except Exception as e:
        logging.error(f"Error adding route: {str(e)}")
        flash(f"Unexpected error adding route: {str(e)}", "error")
    return redirect(url_for('routes_page'))


@app.route('/routes/del', methods=['POST'])
def del_route_handler():
    if load_admin_config().get('disable_routes'):
        flash("Route modifications are disabled by admin.", "error")
        return redirect(url_for('routes_page'))
    try:
        destination = request.form.get('destination', '').strip()
        gateway     = request.form.get('gateway', '').strip()
        interface   = request.form.get('interface', '').strip()
        ip_version  = int(request.form.get('ip_version', 4))

        if not destination:
            flash("Destination is required to delete a route", "error")
            return redirect(url_for('routes_page'))

        args = ['del', destination]
        if gateway:
            args += ['via', gateway]
        if interface:
            args += ['dev', interface]

        ok, err = exec_ip_route(args, ip_version)
        if ok:
            flash(f"Route {destination} removed", "success")
        else:
            flash(f"Failed to remove route {destination}: {err}", "error")
    except Exception as e:
        logging.error(f"Error deleting route: {str(e)}")
        flash(f"Unexpected error deleting route: {str(e)}", "error")
    return redirect(url_for('routes_page'))


# Ensure all captures are stopped and clean up the pcap directory when the application exits
import atexit
# ---------------------------------------------------------------------------
# Interface detail page — helpers
# ---------------------------------------------------------------------------

def read_proc_net_dev(interface):
    """Read rx/tx byte counters directly from /proc/net/dev (no subprocess)."""
    try:
        with open('/proc/net/dev', 'r') as f:
            for line in f:
                if ':' not in line:
                    continue
                iface, data = line.split(':', 1)
                if iface.strip() == interface:
                    fields = data.split()
                    return {'rx_bytes': int(fields[0]), 'tx_bytes': int(fields[8]),
                            'timestamp': time.time()}
    except Exception as e:
        logging.error(f"Error reading /proc/net/dev for {interface}: {e}")
    return None


def get_interface_addresses(interface):
    """Return list of dicts {address, family} for an interface via 'ip addr show'."""
    try:
        result = subprocess.run(['ip', 'addr', 'show', 'dev', interface],
                                capture_output=True, text=True)
        addrs = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith('inet ') or line.startswith('inet6 '):
                parts = line.split()
                family = parts[0]
                address = parts[1]
                addrs.append({'address': address, 'family': family})
        return addrs
    except Exception as e:
        logging.error(f"Error getting addresses for {interface}: {e}")
        return []


def exec_ip_addr(action, interface, address):
    """
    Run 'sudo ip addr add|del <address> dev <interface>'.
    With --net=host Docker deployments this modifies the host directly.
    Returns (success, stderr).
    """
    cmd = ['sudo', 'ip', 'addr', action, address, 'dev', interface]
    result = subprocess.run(cmd, capture_output=True, text=True)
    log_command(cmd, result.stdout + result.stderr)
    return result.returncode == 0, result.stderr.strip()


def get_mtu(interface):
    """Return current MTU for interface as an int, or None on failure."""
    try:
        mtu_path = f'/sys/class/net/{interface}/mtu'
        with open(mtu_path, 'r') as f:
            return int(f.read().strip())
    except Exception as e:
        logging.error(f"Error reading MTU for {interface}: {e}")
        return None


def set_mtu(interface, mtu):
    """
    Run 'sudo ip link set <interface> mtu <mtu>'.
    Returns (success, stderr).
    """
    cmd = ['sudo', 'ip', 'link', 'set', interface, 'mtu', str(mtu)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    log_command(cmd, result.stdout + result.stderr)
    return result.returncode == 0, result.stderr.strip()


# ---------------------------------------------------------------------------
# Interface detail page — routes
# ---------------------------------------------------------------------------

@app.route('/interface/<name>')
def interface_detail(name):
    try:
        hostname = socket.gethostname()
        alias = get_interface_alias(name)
        addresses = get_interface_addresses(name)
        stats = read_proc_net_dev(name)
        mtu = get_mtu(name)
        latency, loss, jitter, bandwidth = get_qdisc_settings(name)
        tc_available = is_tc_available()
        tcpdump_available = is_tcpdump_available()
        iptables_available = is_iptables_available()
        nat_status = get_nat_status(name)
        cfg = load_admin_config()
        iface_ov = cfg.get('interface_overrides', {}).get(name, {})
        tools_column_disabled = cfg.get('disable_tools_column', False)
        return render_template('interface.html',
                               hostname=hostname,
                               iface_name=name,
                               iface_alias=alias,
                               addresses=addresses,
                               initial_stats=stats,
                               mtu=mtu,
                               latency=latency,
                               loss=loss,
                               jitter=jitter,
                               bandwidth=bandwidth,
                               tc_available=tc_available,
                               tcpdump_available=tcpdump_available,
                               iptables_available=iptables_available,
                               nat_status=nat_status,
                               tools_column_disabled=tools_column_disabled,
                               disable_interface_ips=cfg.get('disable_interface_ips', False),
                               disable_mtu=cfg.get('disable_mtu', False),
                               iface_override=iface_ov)
    except Exception as e:
        logging.error(f"Error in interface_detail for {name}: {e}")
        flash(f"Error loading interface detail: {e}", "error")
        return redirect(url_for('index'))


@app.route('/interface/<name>/stats')
def interface_stats(name):
    """JSON endpoint — returns current rx/tx byte counters and timestamp."""
    stats = read_proc_net_dev(name)
    if stats is None:
        return jsonify({'error': f'Interface {name} not found in /proc/net/dev'}), 404
    return jsonify(stats)


@app.route('/interface/<name>/add_addr', methods=['POST'])
def interface_add_addr(name):
    if load_admin_config().get('disable_interface_ips'):
        flash("IP address changes are disabled by admin.", "error")
        return redirect(url_for('interface_detail', name=name))
    address = request.form.get('address', '').strip()
    if not address:
        flash("Address is required (e.g. 192.168.1.10/24 or 2001:db8::1/64)", "error")
        return redirect(url_for('interface_detail', name=name))
    ok, err = exec_ip_addr('add', name, address)
    if ok:
        flash(f"Added {address} to {name}. Note: address changes are temporary and will not survive a reboot.", "success")
    else:
        flash(f"Failed to add {address} to {name}: {err}", "error")
    return redirect(url_for('interface_detail', name=name))


@app.route('/interface/<name>/set_mtu', methods=['POST'])
def interface_set_mtu(name):
    if load_admin_config().get('disable_mtu'):
        flash("MTU changes are disabled by admin.", "error")
        return redirect(url_for('interface_detail', name=name))
    raw = request.form.get('mtu', '').strip()
    try:
        mtu = int(raw)
        if not (68 <= mtu <= 65535):
            raise ValueError
    except ValueError:
        flash(f"Invalid MTU '{raw}'. Must be an integer between 68 and 65535.", "error")
        return redirect(url_for('interface_detail', name=name))
    ok, err = set_mtu(name, mtu)
    if ok:
        flash(f"MTU on {name} set to {mtu}.", "success")
    else:
        flash(f"Failed to set MTU on {name}: {err}", "error")
    return redirect(url_for('interface_detail', name=name))


@app.route('/interface/<name>/del_addr', methods=['POST'])
def interface_del_addr(name):
    if load_admin_config().get('disable_interface_ips'):
        flash("IP address changes are disabled by admin.", "error")
        return redirect(url_for('interface_detail', name=name))
    address = request.form.get('address', '').strip()
    if not address:
        flash("No address specified.", "error")
        return redirect(url_for('interface_detail', name=name))
    ok, err = exec_ip_addr('del', name, address)
    if ok:
        flash(f"Removed {address} from {name}.", "success")
    else:
        flash(f"Failed to remove {address} from {name}: {err}", "error")
    return redirect(url_for('interface_detail', name=name))


# ---------------------------------------------------------------------------
# Admin routes
# ---------------------------------------------------------------------------
import functools
from flask import Response

def _check_admin_auth(username, password):
    if not ADMIN_PASSWORD:
        return True  # no password set — open access
    return password == ADMIN_PASSWORD

def _require_admin_auth(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not ADMIN_PASSWORD:
            return f(*args, **kwargs)  # no password set — open access
        auth = request.authorization
        if not auth or not _check_admin_auth(auth.username, auth.password):
            return Response(
                'Admin login required.',
                401,
                {'WWW-Authenticate': 'Basic realm="HyyperWAN Admin"'}
            )
        return f(*args, **kwargs)
    return decorated

@app.route('/admin', methods=['GET'])
@_require_admin_auth
def admin():
    cfg = load_admin_config()
    # Get live interface list for the interface overrides table
    all_interfaces = []
    try:
        result = subprocess.run(['ip', '-j', 'addr'], capture_output=True, text=True)
        data = json.loads(result.stdout)
        all_interfaces = [i['ifname'] for i in data if i['ifname'] != 'lo']
    except Exception:
        pass
    hostname = socket.gethostname()
    return render_template('admin.html',
                           hostname=hostname,
                           cfg=cfg,
                           all_interfaces=all_interfaces,
                           admin_password_set=bool(ADMIN_PASSWORD))

@app.route('/admin/save', methods=['POST'])
@_require_admin_auth
def admin_save():
    cfg = load_admin_config()

    # Global settings
    hidden_raw = request.form.get('hidden_interfaces', '')
    cfg['hidden_interfaces'] = [i.strip() for i in hidden_raw.split(',') if i.strip()]
    cfg['disable_tools_column']   = 'disable_tools_column'   in request.form
    cfg['default_theme']          = request.form.get('default_theme', '')
    cfg['disable_routes']         = 'disable_routes'         in request.form
    cfg['disable_interface_ips']  = 'disable_interface_ips'  in request.form
    cfg['disable_mtu']            = 'disable_mtu'            in request.form

    # Per-interface overrides — rebuild from form
    overrides = {}
    all_ifaces = request.form.getlist('iface_names')
    for iface in all_ifaces:
        overrides[iface] = {
            'hide_capture':   f'hide_capture_{iface}'   in request.form,
            'hide_nat':       f'hide_nat_{iface}'       in request.form,
            'hide_latency':   f'hide_latency_{iface}'   in request.form,
            'hide_jitter':    f'hide_jitter_{iface}'    in request.form,
            'hide_loss':      f'hide_loss_{iface}'      in request.form,
            'hide_bandwidth': f'hide_bandwidth_{iface}' in request.form,
        }
    cfg['interface_overrides'] = overrides

    ok, err = save_admin_config(cfg)
    if ok:
        flash('Settings saved successfully.', 'success')
    else:
        flash(f'Error saving settings: {err}', 'error')
    return redirect(url_for('admin'))


def cleanup_on_exit():
    # First stop any active captures
    for capture_id, capture_info in active_captures.items():
        try:
            process = capture_info['process']
            if process.poll() is None:  # Process is still running
                logging.info(f"Stopping active capture on exit: {capture_info['display_name']}")
                process.send_signal(signal.SIGTERM)
        except Exception as e:
            logging.error(f"Error stopping capture on exit: {str(e)}")
    
    # Clean up the pcap directory
    try:
        if os.path.exists(PCAP_DIR):
            logging.info(f"Removing pcap directory on exit: {PCAP_DIR}")
            shutil.rmtree(PCAP_DIR)
    except Exception as e:
        logging.error(f"Error removing pcap directory on exit: {str(e)}")

atexit.register(cleanup_on_exit)

if __name__ == '__main__':
    from werkzeug.serving import make_server

    # ---------------------------------------------------------------------------
    # Listener configuration
    #
    # New variables (preferred):
    #   ENABLE_HTTP=true|false    — serve HTTP  (default: true)
    #   ENABLE_HTTPS=true|false   — serve HTTPS (default: false)
    #   HTTP_PORT=8080            — HTTP  listen port
    #   HTTPS_PORT=8443           — HTTPS listen port
    #   SSL_CERT_PATH=...         — path to TLS certificate
    #   SSL_KEY_PATH=...          — path to TLS private key
    #
    # Legacy variables (still honoured for backward compatibility):
    #   USE_HTTPS=true            — equivalent to ENABLE_HTTPS=true, ENABLE_HTTP=false
    #   FLASK_RUN_PORT=...        — sets HTTP_PORT when ENABLE_HTTP is active
    #   FLASK_RUN_HOST=...        — bind address (default 0.0.0.0)
    #   FLASK_DEBUG=true          — enable Werkzeug debugger
    #
    # Docker examples:
    #   HTTP only (default):
    #     docker run -e ENABLE_HTTP=true ...
    #   HTTPS only:
    #     docker run -e ENABLE_HTTPS=true -e SSL_CERT_PATH=/certs/cert.pem -e SSL_KEY_PATH=/certs/key.pem ...
    #   Both HTTP and HTTPS:
    #     docker run -e ENABLE_HTTP=true -e ENABLE_HTTPS=true -e SSL_CERT_PATH=... -e SSL_KEY_PATH=... ...
    # ---------------------------------------------------------------------------

    host       = os.getenv('FLASK_RUN_HOST', '0.0.0.0')
    debug_mode = os.getenv('FLASK_DEBUG', 'false').lower() == 'true'

    # Port defaults — FLASK_RUN_PORT is a legacy alias for HTTP_PORT
    http_port  = int(os.getenv('HTTP_PORT',  os.getenv('FLASK_RUN_PORT', '8080')))
    https_port = int(os.getenv('HTTPS_PORT', '8443'))

    # Legacy USE_HTTPS flag: if set (and new flags not explicitly provided),
    # flip the defaults so HTTPS is on and HTTP is off.
    use_https_legacy = os.getenv('USE_HTTPS', 'false').lower() == 'true'
    default_http  = 'false' if use_https_legacy else 'true'
    default_https = 'true'  if use_https_legacy else 'false'

    enable_http  = os.getenv('ENABLE_HTTP',  default_http ).lower() == 'true'
    enable_https = os.getenv('ENABLE_HTTPS', default_https).lower() == 'true'

    # Validate SSL when HTTPS is requested
    ssl_context = None
    if enable_https:
        ssl_cert = os.getenv('SSL_CERT_PATH', 'certificates/cert.pem')
        ssl_key  = os.getenv('SSL_KEY_PATH',  'certificates/key.pem')
        cert_ok  = ssl_cert and os.path.exists(ssl_cert)
        key_ok   = ssl_key  and os.path.exists(ssl_key)
        if cert_ok and key_ok:
            ssl_context = (ssl_cert, ssl_key)
            logging.info(f"SSL configured: cert={ssl_cert}, key={ssl_key}")
        else:
            logging.error(
                f"HTTPS requested but SSL files missing or not found "
                f"(cert='{ssl_cert}' exists={cert_ok}, key='{ssl_key}' exists={key_ok}). "
                "Disabling HTTPS and falling back to HTTP."
            )
            enable_https = False
            enable_http  = True

    if not enable_http and not enable_https:
        logging.error("No listeners enabled — defaulting to HTTP on port 8080.")
        enable_http = True

    # Build server factories
    def make_http_server():
        logging.info(f"HTTP  listener starting on {host}:{http_port}")
        return make_server(host, http_port, app)

    def make_https_server():
        logging.info(f"HTTPS listener starting on {host}:{https_port}")
        return make_server(host, https_port, app, ssl_context=ssl_context)

    try:
        if enable_http and enable_https:
            # Run HTTP in a daemon thread; HTTPS blocks the main thread.
            def _serve_http():
                try:
                    make_http_server().serve_forever()
                except Exception as e:
                    logging.exception(f"HTTP listener error: {e}")

            http_thread = threading.Thread(target=_serve_http, name='http-server', daemon=True)
            http_thread.start()
            logging.info(f"HTTP  listener running in background thread on {host}:{http_port}")
            make_https_server().serve_forever()

        elif enable_http:
            make_http_server().serve_forever()

        else:
            make_https_server().serve_forever()

    except Exception as e:
        logging.exception(f"Failed to start server: {e}")