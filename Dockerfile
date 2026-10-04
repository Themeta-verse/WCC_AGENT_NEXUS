FROM python:3.12-slim AS nexus-runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    NEXUS_PRODUCT_ROOT=/opt/nexus

WORKDIR /opt/nexus
COPY requirements-standalone.txt ./
RUN python -m pip install --no-cache-dir -r requirements-standalone.txt
COPY nexus_independent ./nexus_independent
COPY runtime ./runtime
COPY pyproject.toml README.md README-INDEPENDENT.md ./

EXPOSE 8787
ENTRYPOINT ["python", "-m", "nexus_independent.cli"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8787"]
