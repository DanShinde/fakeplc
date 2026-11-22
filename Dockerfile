# One-line fake PLC for non-Python tools (Node-RED, Grafana, Ignition, docker-compose demos).
# Published as ghcr.io/danshinde/fakeplc on version tags by .github/workflows/docker.yml.
FROM python:3.13-slim

WORKDIR /app

# Install the package before copying the rest so dependency layers stay cached.
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir .

COPY examples ./examples

# 4840 = OPC UA, 5020 = Modbus TCP.
EXPOSE 4840 5020

# Run as non-root: both ports are above 1024, so nothing else changes.
RUN useradd --create-home --uid 1000 plc
USER plc

ENTRYPOINT ["fakeplc"]
CMD ["serve", "/app/examples/line1.yaml", "--host", "0.0.0.0"]
