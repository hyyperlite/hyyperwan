FROM python:3.10-slim

# System dependencies: iproute2 (tc/ip), tcpdump, iptables, sudo, util-linux (nsenter), openssl (cert gen)
RUN apt-get update && apt-get install -y --no-install-recommends \
    iproute2 \
    tcpdump \
    iptables \
    sudo \
    util-linux \
    openssl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
COPY app.py .
COPY templates ./templates
COPY static ./static
COPY ignored_interfaces.json .

RUN pip install --no-cache-dir -r requirements.txt

# Generate a self-signed certificate baked into the image.
# This means ENABLE_HTTPS=true works out of the box with no extra config.
# To use your own certificate, mount it at runtime and override the paths:
#   -v /path/to/certs:/certs
#   -e SSL_CERT_PATH=/certs/cert.pem
#   -e SSL_KEY_PATH=/certs/key.pem
RUN mkdir -p /app/certificates && \
    openssl req -x509 -newkey rsa:4096 -nodes \
        -out /app/certificates/cert.pem \
        -keyout /app/certificates/key.pem \
        -days 3650 \
        -subj "/CN=hyyperwan/O=HyyperWAN/C=US"

ENV APP_HOME=/app
ENV SSL_CERT_PATH=/app/certificates/cert.pem
ENV SSL_KEY_PATH=/app/certificates/key.pem

# ---------------------------------------------------------------------------
# Listener defaults — override any of these with 'docker run -e VAR=value'
#
# HTTP only (default):
#   docker run --net=host --privileged hyyperwan
#
# HTTPS only (uses baked-in self-signed cert):
#   docker run --net=host --privileged \
#     -e ENABLE_HTTP=false -e ENABLE_HTTPS=true hyyperwan
#
# Both HTTP and HTTPS (uses baked-in self-signed cert):
#   docker run --net=host --privileged \
#     -e ENABLE_HTTPS=true hyyperwan
#
# HTTPS with your own certificate:
#   docker run --net=host --privileged \
#     -e ENABLE_HTTPS=true \
#     -e SSL_CERT_PATH=/certs/cert.pem \
#     -e SSL_KEY_PATH=/certs/key.pem \
#     -v /path/to/your/certs:/certs hyyperwan
# ---------------------------------------------------------------------------
ENV ENABLE_HTTP=true
ENV ENABLE_HTTPS=false
ENV HTTP_PORT=8080
ENV HTTPS_PORT=8443
ENV FLASK_RUN_HOST=0.0.0.0

# Document both ports; only the active ones need to be published.
EXPOSE 8080 8443

CMD ["python", "app.py"]
