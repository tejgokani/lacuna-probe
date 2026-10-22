FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends iputils-ping \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir .

RUN useradd --create-home lacuna && mkdir /data && chown lacuna /data
USER lacuna
ENV XDG_DATA_HOME=/data
VOLUME /data
EXPOSE 9400/udp 9401/tcp

ENTRYPOINT ["lacuna"]
CMD ["--help"]
