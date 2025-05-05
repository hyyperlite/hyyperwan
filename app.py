import subprocess
import json
import re
import os
import uuid
import time
import threading
import signal
import shutil
import socket
from flask import Flask, render_template, request, redirect, url_for, flash, send_file, jsonify
from flask import send_from_directory
import logging

# Make dotenv optional
try:
    from dotenv import load_dotenv
    load_dotenv()  # Load environment variables from .env
except ImportError:
    # dotenv is not installed, just continue without it
    logging.warning("python-dotenv not installed, continuing without loading .env file")

app = Flask(__name__)
app.secret_key = 'your_secret_key'  # Needed for flashing messages

# Configure logging
logging.basicConfig(filename='app.log', level=logging.INFO, format='%(asctime)s - %(message)s')

# Path to store interface aliases
ALIASES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'interface_aliases.json')

# Path to store ignored interfaces
IGNORED_INTERFACES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ignored_interfaces.json')

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

def load_ignored_interfaces():
    """
    Load ignored interface names from the JSON file.
    Returns a list of interface names to ignore.
    """
    try:
        if os.path.exists(IGNORED_INTERFACES_FILE):
            with open(IGNORED_INTERFACES_FILE, 'r') as f:
                ignored = json.load(f)
                if isinstance(ignored, list):
                    return ignored
                else:
                    logging.error(f"Invalid format in {IGNORED_INTERFACES_FILE}: Expected a JSON list.")
                    return []
        else:
            # If the file doesn't exist, create it with default ["docker0"]
            default_ignored = ["docker0"]
            try:
                with open(IGNORED_INTERFACES_FILE, 'w') as f:
                    json.dump(default_ignored, f, indent=4)
                logging.info(f"Created default ignored interfaces file: {IGNORED_INTERFACES_FILE}")
                return default_ignored
            except Exception as e:
                logging.error(f"Error creating default ignored interfaces file: {str(e)}")
                return [] # Return empty list on error
    except Exception as e:
        logging.error(f"Error loading ignored interfaces: {str(e)}")
        return []

def list_interfaces():
    interfaces = []
    try:
        result = subprocess.run(['ip', '-j', 'addr'], capture_output=True, text=True)
        output = result.stdout
        log_command(['ip', '-j', 'addr'], output)
        
        # Load interface aliases and ignored interfaces
        aliases = load_interface_aliases()
        ignored_interfaces = load_ignored_interfaces()
        # Always ignore 'lo' by default
        ignored_interfaces_set = set(ignored_interfaces + ['lo'])
        
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
                        latency, loss, jitter = get_qdisc_settings(interface_name)
                        # Include both real name and alias in the interface data
                        interfaces.append({
                            'name': interface_name, 
                            'alias': aliases.get(interface_name, ''),
                            'ip': ip_address, 
                            'latency': latency, 
                            'loss': loss, 
                            'jitter': jitter
                        })
                    except Exception as e:
                        # If we can't get settings for one interface, still show it with default values
                        logging.error(f"Error getting settings for interface {interface_name}: {str(e)}")
                        interfaces.append({
                            'name': interface_name, 
                            'alias': aliases.get(interface_name, ''),
                            'ip': ip_address, 
                            'latency': '0ms', 
                            'loss': '0%', 
                            'jitter': '0ms'
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
    try:
        result = subprocess.run(['sudo', 'tc', 'qdisc', 'show', 'dev', interface], capture_output=True, text=True)
        output = result.stdout
        log_command(['sudo', 'tc', 'qdisc', 'show', 'dev', interface], output)
        
        # Improved regex patterns for latency, jitter and loss
        latency_match = re.search(r'delay (\d+(?:ms|us))', output)
        # Updated jitter regex to handle the double space and any spacing between latency and jitter
        jitter_match = re.search(r'delay \d+(?:ms|us)\s+(\d+(?:ms|us))', output)
        loss_match = re.search(r'loss (\d+)%', output)
        
        latency = latency_match.group(1) if latency_match else '0ms'
        loss = loss_match.group(1) + '%' if loss_match else '0%'
        jitter = jitter_match.group(1) if jitter_match else '0ms'
        
        # For debugging - log the matched jitter value
        if jitter_match:
            logging.info(f"Captured jitter: {jitter}")
        else:
            logging.info("No jitter found in tc output")
        
        return latency, loss, jitter
    except subprocess.SubprocessError as e:
        logging.error(f"Error executing tc command for interface {interface}: {str(e)}")
        return '0ms', '0%', '0ms'
    except Exception as e:
        logging.error(f"Error getting qdisc settings for interface {interface}: {str(e)}")
        return '0ms', '0%', '0ms'

def apply_qdisc(interface, latency=None, loss=None, jitter=None):
    try:
        # Retrieve current settings
        current_latency, current_loss, current_jitter = get_qdisc_settings(interface)

        # Get interface alias for flash messages
        alias = get_interface_alias(interface)
        # Format interface name with alias for display
        display_name = f"{interface} ({alias})" if alias and alias != interface else interface

        # Merge new values with existing ones
        latency = latency if latency else current_latency
        loss = loss if loss else current_loss
        jitter = jitter if jitter else current_jitter

        # Ensure latency and jitter are in milliseconds
        if latency and not latency.endswith(('ms', 'us')):
            latency += 'ms'
        if jitter and not jitter.endswith(('ms', 'us')):
            jitter += 'ms'
            
        # If jitter is set but latency is not, set a minimal latency value
        # because tc netem requires latency to be set when using jitter
        if jitter != '0ms' and latency == '0ms':
            latency = '1ms'  # Set a minimal latency value
            logging.info(f"Setting minimal latency of 1ms to accommodate jitter setting")

        # Apply latency, loss, and jitter settings
        command = ['sudo', 'tc', 'qdisc', 'replace', 'dev', interface, 'root', 'netem']
        if latency != '0ms':
            if jitter != '0ms':
                # Format: delay <latency> <jitter>
                command.extend(['delay', latency, jitter])
            else:
                # Just latency without jitter
                command.extend(['delay', latency])
        if loss != '0%':
            command.extend(['loss', loss.replace('%', '')])

        try:
            result = subprocess.run(command, capture_output=True, text=True)
            log_command(command, result.stdout)
            if result.returncode != 0:
                flash(f"Error applying qdisc to interface {display_name}: {result.stderr}", "error")
                logging.error(f"Error applying qdisc to interface {interface} (return code {result.returncode}): {result.stderr}")
            else:
                flash(f"Network conditions applied successfully to interface {display_name}", "success")
        except subprocess.SubprocessError as e:
            flash(f"Failed to execute tc command for interface {display_name}: {str(e)}", "error")
            logging.error(f"Subprocess error when applying qdisc to interface {interface}: {str(e)}")
    except Exception as e:
        flash(f"Error applying network conditions to interface {interface}: {str(e)}", "error")
        logging.error(f"Error in apply_qdisc for interface {interface}: {str(e)}")

def remove_degradations(interface):
    try:
        # Get interface alias for flash messages
        alias = get_interface_alias(interface)
        # Format interface name with alias for display
        display_name = f"{interface} ({alias})" if alias and alias != interface else interface
        
        # First check if there's a netem qdisc to remove
        check_result = subprocess.run(['sudo', 'tc', 'qdisc', 'show', 'dev', interface], capture_output=True, text=True)
        log_command(['sudo', 'tc', 'qdisc', 'show', 'dev', interface], check_result.stdout)
        
        # Only attempt to remove if "netem" is in the output
        if "netem" in check_result.stdout:
            result = subprocess.run(['sudo', 'tc', 'qdisc', 'del', 'dev', interface, 'root', 'netem'], capture_output=True, text=True)
            log_command(['sudo', 'tc', 'qdisc', 'del', 'dev', interface, 'root', 'netem'], result.stdout)
            if result.returncode != 0:
                flash(f"Error removing qdisc from interface {display_name}: {result.stderr}", "error")
                logging.error(f"Error removing qdisc from interface {interface} (return code {result.returncode}): {result.stderr}")
            else:
                flash(f"Network conditions removed successfully from interface {display_name}", "success")
        else:
            logging.info(f"No netem qdisc to remove on interface {interface}")
            # Don't show a flash message for interfaces with no settings to remove
    except subprocess.SubprocessError as e:
        flash(f"Failed to execute tc command for interface {display_name}: {str(e)}", "error")
        logging.error(f"Subprocess error when removing qdisc from interface {interface}: {str(e)}")
    except Exception as e:
        flash(f"Error removing network conditions from interface {interface}: {str(e)}", "error")
        logging.error(f"Error in remove_degradations for interface {interface}: {str(e)}")

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

@app.route('/')
def index():
    try:
        # Check if ip command is available first
        ip_available = is_ip_available()
        
        # Only attempt to list interfaces if ip command is available
        interfaces = list_interfaces() if ip_available else []
        
        hostname = socket.gethostname()
        tcpdump_available = is_tcpdump_available()
        tc_available = is_tc_available()
        
        return render_template('index.html', interfaces=interfaces, hostname=hostname, 
                              tcpdump_available=tcpdump_available, tc_available=tc_available,
                              ip_available=ip_available)
    except Exception as e:
        logging.error(f"Error in index route: {str(e)}")
        flash("An error occurred while loading the page", "error")
        hostname = "Unknown"
        return render_template('index.html', interfaces=[], hostname=hostname, 
                              tcpdump_available=False, tc_available=False,
                              ip_available=False)

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
        
        # Validate inputs
        validation_errors = []
        
        # Validate latency
        latency_valid, latency_clean, latency_error = validate_latency_jitter(latency, 'Latency')
        if not latency_valid:
            validation_errors.append(latency_error)
            latency = None
        else:
            latency = latency_clean
        
        # Validate jitter
        jitter_valid, jitter_clean, jitter_error = validate_latency_jitter(jitter, 'Jitter')
        if not jitter_valid:
            validation_errors.append(jitter_error)
            jitter = None
        else:
            jitter = jitter_clean
        
        # Validate loss
        loss_valid, loss_clean, loss_error = validate_loss(loss)
        if not loss_valid:
            validation_errors.append(loss_error)
            loss = None
        else:
            loss = loss_clean
        
        # If there are validation errors, flash them and redirect
        if validation_errors:
            for error in validation_errors:
                flash(error, 'error')
            return redirect(url_for('index'))
        
        # Apply validated settings to qdisc
        apply_qdisc(interface, latency, loss, jitter)
        
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
                
                # Check if there's a netem qdisc to remove on this interface
                check_result = subprocess.run(['sudo', 'tc', 'qdisc', 'show', 'dev', interface_name], 
                                             capture_output=True, text=True)
                
                # Only count interfaces where netem was actually present
                if "netem" in check_result.stdout:
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
        if host_filter:
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
        if not os.path.exists('/.dockerenv'):
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

# Ensure all captures are stopped and clean up the pcap directory when the application exits
import atexit
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
    host = os.getenv('FLASK_RUN_HOST', '0.0.0.0')
    
    # Add safe port parsing with error handling
    try:
        port = int(os.getenv('FLASK_RUN_PORT', 8080))
    except ValueError:
        logging.error("Invalid port specified in environment variables, using default 8080")
        port = 8080
    
    # Get SSL configuration from environment variables
    use_https = os.getenv('USE_HTTPS', 'false').lower() == 'true'
    cert_path = os.getenv('SSL_CERT_PATH', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'certificates/cert.pem'))
    key_path = os.getenv('SSL_KEY_PATH', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'certificates/key.pem'))
    
    # Check if SSL is enabled and certificates exist
    if use_https and os.path.exists(cert_path) and os.path.exists(key_path):
        logging.info(f"Starting application with HTTPS on {host}:{port}")
        app.run(host=host, port=port, ssl_context=(cert_path, key_path), debug=True)
    else:
        if use_https:
            logging.warning("HTTPS was requested but certificates not found at:")
            logging.warning(f"Certificate: {cert_path}")
            logging.warning(f"Key: {key_path}")
            logging.warning("Falling back to HTTP")
        logging.info(f"Starting application with HTTP on {host}:{port}")
        app.run(host=host, port=port, debug=True)