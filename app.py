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
            interfaces.append({'name': interface_name, 'ip': ip_address, 'latency': latency, 'loss': loss})

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
    result = subprocess.run(['tc', 'qdisc', 'show', 'dev', interface], capture_output=True, text=True)
    output = result.stdout
    log_command(['tc', 'qdisc', 'show', 'dev', interface], output)
    latency_match = re.search(r'delay (\d+ms|\d+us)', output)
    loss_match = re.search(r'loss (\d+)%', output)
    jitter_match = re.search(r'delay \d+ms (\d+ms)', output)  # Extract jitter if present
    latency = latency_match.group(1) if latency_match else '0ms'
    loss = loss_match.group(1) + '%' if loss_match else '0%'
    jitter = jitter_match.group(1) if jitter_match else '0ms'
    return latency, loss, jitter

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
    latency = request.form.get('latency')
    loss = request.form.get('loss')
    jitter = request.form.get('jitter')  # Add jitter parameter

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