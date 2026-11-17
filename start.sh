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
ENV_FILE="$SCRIPT_DIR/.env"

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

# ── Verificar que existe el archivo .env ──────────────────────────────────────
if [[ ! -f "$ENV_FILE" ]]; then
  error "No se encontró el archivo .env"
  echo ""
  echo -e "  ${YELLOW}Solución:${RESET} copia la plantilla y rellena tus valores:"
  echo -e "    cp .env.example .env"
  echo -e "    nano .env   # (o el editor que prefieras)"
  echo ""
  echo -e "  Necesitas configurar:"
  echo -e "    NODE0_HOST, NODE0_DB, NODE0_USER, NODE0_PASSWORD"
  echo -e "    NODE1_HOST, NODE1_DB, NODE1_USER, NODE1_PASSWORD"
  echo ""
  exit 1
fi

# ── Verificar que las dependencias estén instaladas ────────────────────────────
if ! python3 -c "import flask, pymysql, flask_cors" &>/dev/null; then
  warn "Dependencias no encontradas. Instalando desde requirements.txt..."
  pip install -r "$BACKEND_DIR/requirements.txt" --quiet
fi

# ── Verificar que la configuración es válida antes de arrancar ────────────────
if ! python3 -c "
import sys
sys.path.insert(0, '$BACKEND_DIR')
try:
    from config import NODES, LOCAL_NODE_INDEX
    if not NODES:
        print('ERROR: No hay nodos configurados en .env')
        sys.exit(1)
    print(f'Configuración OK: {len(NODES)} nodo(s), nodo local: {LOCAL_NODE_INDEX}')
except Exception as e:
    print(f'ERROR en config.py: {e}')
    sys.exit(1)
" 2>&1; then
  error "La configuración del backend tiene errores. Revisa tu archivo .env"
  exit 1
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
setsid nohup python3 app.py > "$LOG_FILE" 2>&1 &
BACKEND_PID=$!
echo "$BACKEND_PID" > "$PID_FILE"

# ── Esperar a que el servidor esté listo (máx. 15 s) ─────────────────────────
info "Esperando que el backend esté listo..."
for i in $(seq 1 30); do
  if curl -sf http://localhost:5000/api/nodo-info >/dev/null 2>&1; then
    info "Backend listo en http://localhost:5000 (PID $BACKEND_PID)"
    break
  fi
  # Si el proceso ya murió, mostrar el log inmediatamente
  if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    error "El backend terminó inesperadamente. Últimas líneas del log:"
    echo ""
    tail -20 "$LOG_FILE" >&2
    echo ""
    error "Revisa el log completo en: $LOG_FILE"
    exit 1
  fi
  if [[ $i -eq 30 ]]; then
    error "El backend no respondió en 15 segundos. Últimas líneas del log:"
    echo ""
    tail -20 "$LOG_FILE" >&2
    echo ""
    error "Revisa el log completo en: $LOG_FILE"
    exit 1
  fi
  sleep 0.5
done

# ── Abrir frontend en el navegador ────────────────────────────────────────────
FRONTEND_URL="http://localhost:5000"

if command -v xdg-open &>/dev/null; then
  info "Abriendo frontend en el navegador..."
  xdg-open "$FRONTEND_URL" >/dev/null 2>&1 &
elif command -v open &>/dev/null; then
  open "$FRONTEND_URL" >/dev/null 2>&1 &
else
  warn "No se pudo abrir el navegador automáticamente."
  warn "Abre manualmente: $FRONTEND_URL"
fi

echo ""
echo -e "  ${GREEN}Sistema corriendo${RESET}"
echo -e "  URL → ${YELLOW}http://localhost:5000${RESET}"
echo -e "  Logs    → ${YELLOW}$LOG_FILE${RESET}"
echo ""
echo -e "  Para detener: ${YELLOW}bash stop.sh${RESET}"
echo ""
