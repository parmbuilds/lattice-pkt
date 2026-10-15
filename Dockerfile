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
COPY backend/ ./
COPY --from=frontend /frontend/dist ./static
ENV PATH="/app/.venv/bin:$PATH" LATTICE_STATIC_DIR=/app/static
RUN useradd --system --uid 10001 lattice
USER lattice
EXPOSE 8000
# Migrations run separately (the compose `migrate` service), as the owner role.
CMD ["uvicorn", "--factory", "app.main:create_app", "--host", "0.0.0.0", "--port", "8000"]
