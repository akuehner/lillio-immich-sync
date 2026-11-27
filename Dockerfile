FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir .
# State (record of synced items) lives in a volume
ENV STATE_DIR=/state
VOLUME /state
ENTRYPOINT ["lillio-immich-sync"]
# Long-running with the built-in schedule (SYNC_TIMES, SYNC_DAYS, TIMEZONE); use `sync` for a one-shot run
CMD ["daemon"]
