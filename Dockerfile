FROM python:3.13-slim

WORKDIR /app

# Build tools in case ripser has no wheel for the platform
RUN apt-get update && apt-get install -y --no-install-recommends gcc g++ \
    && rm -rf /var/lib/apt/lists/*

# Dependencies come from Pipfile.lock only
COPY Pipfile Pipfile.lock ./
RUN pip install --no-cache-dir pipenv \
    && pipenv install --system --deploy \
    && pip uninstall -y pipenv

COPY mc_service/ ./mc_service/
COPY frontend/ ./frontend/

ENV MPLCONFIGDIR=/tmp/matplotlib

RUN useradd --create-home --uid 1000 mc && mkdir -p /data/jobs && chown mc /data/jobs
ENV MC_JOB_DIR=/data/jobs
USER mc

EXPOSE 8020
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8020/health', timeout=3).status == 200 else 1)"
CMD ["python3", "-m", "uvicorn", "mc_service.main:app", "--host", "0.0.0.0", "--port", "8020"]
