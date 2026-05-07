#!/bin/bash
set -e
echo ">>> Ejecutando start.sh..."

REPO_URL="https://github.com/sherlockes/sherlockes.github.io.git"
REPO_DIR="/site/repo"

echo "🧹 Borrando contenido anterior..."
rm -rf "${REPO_DIR:?}/"*
rm -rf "${REPO_DIR:?}/."* 2>/dev/null || true

echo "📥 Clonando repositorio..."
git clone "$REPO_URL" "$REPO_DIR"

echo "👤 Ajustando permisos..."
chown -R "$(id -u)":"$(id -g)" "$REPO_DIR"

cd "$REPO_DIR" || exit 1

echo "🚀 Lanzando Hugo..."

while true; do
  echo ">>> Lanzando Hugo..."
  hugo server \
    --buildDrafts \
    --disableFastRender \
    --bind 0.0.0.0 \
    --baseURL http://localhost:1313 \
    --navigateToChanged

  EXIT_CODE=$?
  echo ">>> Hugo se ha parado con código $EXIT_CODE. Reiniciando en 2s..."
  sleep 2
done
