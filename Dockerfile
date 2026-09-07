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

# Shell form so $PORT expands. Railway injects it; 8000 is the local fallback.
CMD python -m uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}
