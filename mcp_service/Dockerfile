FROM python:3.11-slim

WORKDIR /srv
COPY mcp_service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# core.py and its config are the portal's own: the MCP servers and the web form share them
COPY app/core.py /srv/
COPY app/config /srv/config
COPY mcp_service/*.py /srv/
RUN mkdir -p /var/sewasetu/uploads
ENV PYTHONPATH=/srv

EXPOSE 8001 8002
CMD ["python", "citizen_server.py"]
