FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
COPY pyproject.toml README.md ./
COPY src ./src
ARG EXTRAS=llm,scrapling
RUN pip install ".[${EXTRAS}]"
EXPOSE 8000
CMD ["arte", "serve", "--host", "0.0.0.0"]
