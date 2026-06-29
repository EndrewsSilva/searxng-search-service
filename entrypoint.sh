#!/bin/bash
set -e

# Garante dependências extras que podem não estar na imagem base
pip install reportlab==4.2.5 Pillow --quiet --no-cache-dir 2>/dev/null || true

exec python -m uvicorn app.asgi:app \
    --host 0.0.0.0 \
    --port 5006 \
    --reload
