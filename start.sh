#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# start.sh — Arranca el backend Flask y abre el frontend en el navegador
# Uso: bash start.sh
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/backend"
FRONTEND_DIR="$SCRIPT_DIR/frontend"
LOG_FILE="$SCRIPT_DIR/backend.log"
PID_FILE="$SCRIPT_DIR/backend.pid"

# ── Colores ────────────────────────────────────────────────────────────────────
GREEN="\033[0;32m"
YELLOW="\033[1;33m"
RED="\033[0;31m"
RESET="\033[0m"

info()    { echo -e "${GREEN}[✔]${RESET} $*"; }
warn()    { echo -e "${YELLOW}[!]${RESET} $*"; }
error()   { echo -e "${RED}[✘]${RESET} $*" >&2; }

# ── Verificar Python ───────────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
  error "python3 no encontrado. Instala Python 3.10+ para continuar."
  exit 1
fi

# ── Verificar que las dependencias estén instaladas ────────────────────────────
if ! python3 -c "import flask, pymysql, flask_cors" &>/dev/null; then
  warn "Dependencias no encontradas. Instalando desde requirements.txt..."
  pip install -r "$BACKEND_DIR/requirements.txt" --quiet
fi

# ── Matar instancia anterior si existe ────────────────────────────────────────
if [[ -f "$PID_FILE" ]]; then
  OLD_PID=$(cat "$PID_FILE")
  if kill -0 "$OLD_PID" 2>/dev/null; then
    warn "Deteniendo instancia anterior del backend (PID $OLD_PID)..."
    kill "$OLD_PID"
    sleep 1
  fi
  rm -f "$PID_FILE"
fi

# ── Arrancar backend en segundo plano ─────────────────────────────────────────
info "Iniciando backend Flask..."
cd "$BACKEND_DIR"
nohup python3 app.py > "$LOG_FILE" 2>&1 &
BACKEND_PID=$!
echo "$BACKEND_PID" > "$PID_FILE"

# ── Esperar a que el servidor esté listo (máx. 10 s) ─────────────────────────
info "Esperando que el backend esté listo..."
for i in $(seq 1 20); do
  if curl -sf http://localhost:5000/api/nodo-info >/dev/null 2>&1; then
    info "Backend listo en http://localhost:5000 (PID $BACKEND_PID)"
    break
  fi
  if [[ $i -eq 20 ]]; then
    error "El backend no respondió en 10 segundos. Revisa $LOG_FILE"
    exit 1
  fi
  sleep 0.5
done

# ── Abrir frontend en el navegador ────────────────────────────────────────────
FRONTEND_URL="file://$FRONTEND_DIR/index.html"

if command -v xdg-open &>/dev/null; then
  info "Abriendo frontend en el navegador..."
  xdg-open "$FRONTEND_URL" &
elif command -v open &>/dev/null; then
  open "$FRONTEND_URL" &
else
  warn "No se pudo abrir el navegador automáticamente."
  warn "Abre manualmente: $FRONTEND_URL"
fi

echo ""
echo -e "  ${GREEN}Sistema corriendo${RESET}"
echo -e "  Backend → ${YELLOW}http://localhost:5000${RESET}"
echo -e "  Frontend → ${YELLOW}$FRONTEND_URL${RESET}"
echo -e "  Logs    → ${YELLOW}$LOG_FILE${RESET}"
echo ""
echo -e "  Para detener: ${YELLOW}bash stop.sh${RESET}"
echo ""
