import subprocess
import json
import re
import os
from flask import Flask, render_template, request, redirect, url_for, flash
from dotenv import load_dotenv
import logging

load_dotenv()  # Load environment variables from .env
app = Flask(__name__)
app.secret_key = 'your_secret_key'  # Needed for flashing messages

# Configure logging
logging.basicConfig(filename='app.log', level=logging.INFO, format='%(asctime)s - %(message)s')

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

def list_interfaces():
    interfaces = []
    try:
        result = subprocess.run(['ip', '-j', 'addr'], capture_output=True, text=True)
        output = result.stdout
        log_command(['ip', '-j', 'addr'], output)
        
        try:
            data = json.loads(output)
            
            for interface in data:
                interface_name = interface['ifname']
                if interface_name == 'lo':
                    continue  # Skip interfaces with the name 'lo'
                
                ip_address = None
                for addr_info in interface.get('addr_info', []):
                    if addr_info['family'] == 'inet':
                        ip_address = addr_info['local']
                        break
                if ip_address:
                    try:
                        # Get current network condition settings
                        latency, loss, jitter = get_qdisc_settings(interface_name)
                        interfaces.append({'name': interface_name, 'ip': ip_address, 'latency': latency, 'loss': loss, 'jitter': jitter})
                    except Exception as e:
                        # If we can't get settings for one interface, still show it with default values
                        logging.error(f"Error getting settings for interface {interface_name}: {str(e)}")
                        interfaces.append({'name': interface_name, 'ip': ip_address, 'latency': '0ms', 'loss': '0%', 'jitter': '0ms'})
        
        except json.JSONDecodeError as e:
            logging.error(f"Error parsing JSON output from ip command: {str(e)}")
            flash("Error retrieving network interfaces", "error")
    
    except Exception as e:
        logging.error(f"Error listing interfaces: {str(e)}")
        flash("Error retrieving network interfaces", "error")
    
    return interfaces

def get_latency(interface):
    result = subprocess.run(['tc', 'qdisc', 'show', 'dev', interface], capture_output=True, text=True)
    output = result.stdout
    log_command(['tc', 'qdisc', 'show', 'dev', interface], output)
    match = re.search(r'delay (\d+ms|\d+us)', output)
    return match.group(1) if match else '0ms'

def get_loss(interface):
    result = subprocess.run(['tc', 'qdisc', 'show', 'dev', interface], capture_output=True, text=True)
    output = result.stdout
    log_command(['tc', 'qdisc', 'show', 'dev', interface], output)
    match = re.search(r'loss (\d+)%', output)
    return match.group(1) + '%' if match else '0%'

def get_qdisc_settings(interface):
    try:
        result = subprocess.run(['tc', 'qdisc', 'show', 'dev', interface], capture_output=True, text=True)
        output = result.stdout
        log_command(['tc', 'qdisc', 'show', 'dev', interface], output)
        
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
    # Retrieve current settings
    current_latency, current_loss, current_jitter = get_qdisc_settings(interface)

    # Merge new values with existing ones
    latency = latency if latency else current_latency
    loss = loss if loss else current_loss
    jitter = jitter if jitter else current_jitter

    # Ensure latency and jitter are in milliseconds
    if latency and not latency.endswith(('ms', 'us')):
        latency += 'ms'
    if jitter and not jitter.endswith(('ms', 'us')):
        jitter += 'ms'

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

    result = subprocess.run(command, capture_output=True, text=True)
    log_command(command, result.stdout)
    if result.returncode != 0:
        flash(f"Error applying qdisc: {result.stderr}")

def remove_degradations(interface):
    result = subprocess.run(['sudo', 'tc', 'qdisc', 'del', 'dev', interface, 'root', 'netem'], capture_output=True, text=True)
    log_command(['sudo', 'tc', 'qdisc', 'del', 'dev', interface, 'root', 'netem'], result.stdout)
    if result.returncode != 0:
        flash(f"Error removing qdisc: {result.stderr}")

@app.route('/')
def index():
    interfaces = list_interfaces()
    return render_template('index.html', interfaces=interfaces)

@app.route('/apply', methods=['POST'], endpoint='apply_interface')
def apply():
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

@app.route('/remove', methods=['POST'], endpoint='remove_interface')
def remove():
    interface = request.form['interface'].split(' ')[0]  # Extract the interface name
    remove_degradations(interface)
    return redirect(url_for('index'))

if __name__ == '__main__':
    host = os.getenv('FLASK_RUN_HOST', '0.0.0.0')
    port = int(os.getenv('FLASK_RUN_PORT', 8080))
    app.run(host=host, port=port, debug=True)