# The SPA build output (frontend/) is gitignored, so the image has to build it
# rather than copy it in. Two stages keep node out of the runtime image.
FROM node:22-slim AS ui
WORKDIR /app/ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci
COPY ui/ ./
# vite.config.ts writes to ../frontend, i.e. /app/frontend.
RUN npm run build


FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY --from=ui /app/frontend/ ./frontend/
# The landing page, and only the landing page. Everything else under docs/ is
# interview preparation — .dockerignore keeps it out, and a single explicit
# COPY is what makes that hard to undo by accident.
COPY docs/pitch/index.html ./landing/index.html

# sh -c is what expands $PORT (Railway injects it; 8000 is the local fallback),
# and `exec` then replaces the shell with python so python is PID 1. Without the
# exec, SIGTERM on shutdown would reach /bin/sh and never the app, so the hook in
# main.py that hands pooled Postgres connections back would not run.
CMD ["sh", "-c", "exec python -m uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
