#!/bin/bash
set -e

REPO_DIR="/site/repo"

echo ">>> Reiniciando SOLO el servidor de Hugo (sin clonar el repo)..."

# 1. Matar TODOS los procesos Hugo (sin excepción)
echo ">>> Matando procesos Hugo existentes..."
pkill -f "argv0 hugo" 2>/dev/null || true

# Verificar si murió
sleep 1
if ps aux | grep -v grep | grep -q "argv0 hugo"; then
    echo ">>> ¡Cuidado! Todavía quedan procesos. Matando con fuerza..."
    pkill -9 -f "argv0 hugo" 2>/dev/null || true
fi

echo ">>> Procesos Hugo eliminados."

sleep 1

echo ">>> Lanzando nuevo servidor Hugo..."
cd "$REPO_DIR"

hugo server \
  --buildDrafts \
  --disableFastRender \
  --bind 0.0.0.0 \
  --baseURL http://localhost:1313 \
  --navigateToChanged \
  2>&1 | tee /proc/1/fd/1 &

NEW_PID=$!
echo "$NEW_PID" > /tmp/hugo.pid

echo ">>> Nuevo servidor Hugo iniciado con PID: $NEW_PID"

