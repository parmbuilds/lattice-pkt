# Builds the SPA, then serves it from the FastAPI app (KTD12: same origin).
FROM node:24-slim AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim AS backend
COPY --from=ghcr.io/astral-sh/uv:0.13 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
RUN uv sync --locked --no-dev --no-install-project
RUN useradd --system --uid 10001 lattice
# Warm the embedding model into a fixed cache, checked against a pinned hash, so the runtime
# never reaches the network (KTD1). The hash is model_optimized.onnx under fastembed 0.9.0.
ENV FASTEMBED_CACHE_PATH=/app/models
RUN .venv/bin/python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')" \
    && echo "51f1bd0addd6e859e42c2c8021a5e5461385bb676a649f4b269aa445449f2431  $(find -L "$FASTEMBED_CACHE_PATH" -name model_optimized.onnx)" \
        | sha256sum -c - \
    && chown -R lattice "$FASTEMBED_CACHE_PATH"
COPY backend/ ./
COPY --from=frontend /frontend/dist ./static
ENV PATH="/app/.venv/bin:$PATH" LATTICE_STATIC_DIR=/app/static HF_HUB_OFFLINE=1
USER lattice
EXPOSE 8000
# Migrations run separately (the compose `migrate` service), as the owner role.
CMD ["uvicorn", "--factory", "app.main:create_app", "--host", "0.0.0.0", "--port", "8000"]
