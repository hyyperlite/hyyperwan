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

def list_interfaces():
    interfaces = []
    result = subprocess.run(['ip', '-j', 'addr'], capture_output=True, text=True)
    output = result.stdout
    log_command(['ip', '-j', 'addr'], output)
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
            latency = get_latency(interface_name)
            loss = get_loss(interface_name)
            bandwidth = get_bandwidth(interface_name)
            interfaces.append({'name': interface_name, 'ip': ip_address, 'latency': latency, 'loss': loss, 'bandwidth': bandwidth})

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


def get_bandwidth(interface):
    result = subprocess.run(['tc', 'class', 'show', 'dev', interface], capture_output=True, text=True)
    output = result.stdout
    log_command(['tc', 'class', 'show', 'dev', interface], output)
    match = re.search(r'rate (\d+Kbit)', output)
    if match:
        bandwidth_kbit = int(match.group(1).replace('Kbit', ''))
    else:
        # If no bandwidth is set, retrieve the negotiated speed using ethtool
        try:
            result = subprocess.run(['ethtool', interface], capture_output=True, text=True, check=True)
            output = result.stdout
            log_command(['ethtool', interface], output)
            match = re.search(r'Speed: (\d+)Mb/s', output)  # Corrected regex pattern
            if match:
                bandwidth_kbit = int(match.group(1)) * 1000  # Convert Mb/s to Kbit
            else:
                return 'N/A'
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            # ethtool is not installed or failed to run
            log_command(['ethtool', interface], str(e))
            return 'N/A'

    bandwidth = {
        'Kb': f"{bandwidth_kbit} Kb",
        'Mb': f"{round(bandwidth_kbit / 1000)} Mb",
        'Gb': f"{round(bandwidth_kbit / 1000000)} Gb"
    }
    return bandwidth


def get_qdisc_settings(interface):
    result = subprocess.run(['tc', 'qdisc', 'show', 'dev', interface], capture_output=True, text=True)
    output = result.stdout
    log_command(['tc', 'qdisc', 'show', 'dev', interface], output)
    latency_match = re.search(r'delay (\d+ms|\d+us)', output)
    loss_match = re.search(r'loss (\d+)%', output)
    latency = latency_match.group(1) if latency_match else '0ms'
    loss = loss_match.group(1) + '%' if loss_match else '0%'
    return latency, loss

def apply_qdisc(interface, latency=None, loss=None):
    current_latency, current_loss = get_qdisc_settings(interface)
    
    # Ensure latency is in milliseconds
    if latency and not latency.endswith(('ms', 'us')):
        latency += 'ms'
    
    latency = latency if latency else current_latency
    loss = loss if loss else current_loss

    command = ['sudo', 'tc', 'qdisc', 'replace', 'dev', interface, 'root', 'netem']
    if latency != '0ms':
        command.extend(['delay', latency])
    if loss != '0%':
        command.extend(['loss', loss])

    result = subprocess.run(command, capture_output=True, text=True)
    log_command(command, result.stdout)
    if result.returncode != 0:
        flash(f"Error applying qdisc: {result.stderr}")
        
        
def apply_bandwidth(interface, bandwidth):
    if bandwidth:
        remove_bandwidth(interface)  # Remove existing bandwidth setting
        result = subprocess.run(['sudo', 'tc', 'qdisc', 'add', 'dev', interface, 'root', 'handle', '1:', 'htb'], capture_output=True, text=True)
        log_command(['sudo', 'tc', 'qdisc', 'add', 'dev', interface, 'root', 'handle', '1:', 'htb'], result.stdout)
        if result.returncode != 0:
            flash(f"Error setting up root qdisc: {result.stderr}")
        result = subprocess.run(['sudo', 'tc', 'class', 'add', 'dev', interface, 'parent', '1:', 'classid', '1:1', 'htb', 'rate', bandwidth], capture_output=True, text=True)
        log_command(['sudo', 'tc', 'class', 'add', 'dev', interface, 'parent', '1:', 'classid', '1:1', 'htb', 'rate', bandwidth], result.stdout)
        if result.returncode != 0:
            flash(f"Error applying bandwidth: {result.stderr}")

def remove_bandwidth(interface):
    result = subprocess.run(['sudo', 'tc', 'qdisc', 'del', 'dev', interface, 'root', 'handle', '1:'], capture_output=True, text=True)
    log_command(['sudo', 'tc', 'qdisc', 'del', 'dev', interface, 'root', 'handle', '1:'], result.stdout)
    if result.returncode != 0:
        flash(f"Error removing bandwidth: {result.stderr}")

def remove_degradations(interface):
    result = subprocess.run(['sudo', 'tc', 'qdisc', 'del', 'dev', interface, 'root', 'netem'], capture_output=True, text=True)
    log_command(['sudo', 'tc', 'qdisc', 'del', 'dev', interface, 'root', 'netem'], result.stdout)
    if result.returncode != 0:
        flash(f"Error removing qdisc: {result.stderr}")
    remove_bandwidth(interface)

@app.route('/')
def index():
    interfaces = list_interfaces()
    return render_template('index.html', interfaces=interfaces)

@app.route('/apply', methods=['POST'], endpoint='apply_interface')
def apply():
    interface = request.form['interface'].split(' ')[0]  # Extract the interface name
    latency = request.form.get('latency')
    loss = request.form.get('loss')
    bandwidth = request.form.get('bandwidth')
    
    if latency or loss:
        apply_qdisc(interface, latency, loss)
    if bandwidth:
        apply_bandwidth(interface, bandwidth)
    
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