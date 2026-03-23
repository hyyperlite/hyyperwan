FROM python:3.10-slim

# System dependencies: iproute2 (tc/ip), tcpdump, iptables, sudo, util-linux (nsenter)
RUN apt-get update && apt-get install -y --no-install-recommends \
    iproute2 \
    tcpdump \
    iptables \
    sudo \
    util-linux \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
COPY app.py .
COPY templates ./templates
COPY static ./static
COPY ignored_interfaces.json .

# Certificates are optional — mount them at runtime via -v or pass paths via -e.
# To bake certs into the image instead, uncomment the line below:
# COPY certificates ./certificates

RUN pip install --no-cache-dir -r requirements.txt

ENV APP_HOME=/app

# ---------------------------------------------------------------------------
# Listener defaults — override any of these with 'docker run -e VAR=value'
#
# HTTP only (default):
#   docker run --net=host --privileged hyyperwan
#
# HTTPS only:
#   docker run --net=host --privileged \
#     -e ENABLE_HTTP=false -e ENABLE_HTTPS=true \
#     -e SSL_CERT_PATH=/certs/cert.pem -e SSL_KEY_PATH=/certs/key.pem \
#     -v /path/to/certs:/certs hyyperwan
#
# Both HTTP and HTTPS:
#   docker run --net=host --privileged \
#     -e ENABLE_HTTP=true -e ENABLE_HTTPS=true \
#     -e SSL_CERT_PATH=/certs/cert.pem -e SSL_KEY_PATH=/certs/key.pem \
#     -v /path/to/certs:/certs hyyperwan
# ---------------------------------------------------------------------------
ENV ENABLE_HTTP=true
ENV ENABLE_HTTPS=false
ENV HTTP_PORT=8080
ENV HTTPS_PORT=8443
ENV FLASK_RUN_HOST=0.0.0.0
# SSL_CERT_PATH and SSL_KEY_PATH are intentionally unset here;
# set them at runtime when enabling HTTPS.

# Document both ports; only the active ones need to be published.
EXPOSE 8080 8443

CMD ["python", "app.py"]
