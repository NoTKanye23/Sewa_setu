FROM python:3.11-slim

WORKDIR /srv
COPY mcp_service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/core.py /srv/
COPY app/config /srv/config
COPY evals/*.py /srv/evals/
ENV PYTHONPATH=/srv GIT_REV=unknown
# run on demand:  docker compose run --rm evals
CMD ["python", "/srv/evals/runner.py"]
