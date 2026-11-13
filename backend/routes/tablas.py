"""
routes/tablas.py — Gestión de tablas distribuidas.

Permite crear y eliminar tablas en TODOS los nodos simultáneamente.
El DDL se construye en el backend a partir de JSON — nunca se acepta SQL crudo.
"""

import re

from flask import Blueprint, jsonify, request

from db import execute_on_all_nodes, execute_on_node, get_local_node

bp_tablas = Blueprint("tablas", __name__)

# Tipos de columna permitidos (whitelist)
TIPOS_PERMITIDOS = {"VARCHAR", "BIGINT", "INT", "DECIMAL", "DATE", "BOOLEAN", "TEXT", "CHAR"}
_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$")


def _ok(data):
    return jsonify({"data": data, "error": None})


def _err(msg, status=400):
    return jsonify({"data": None, "error": msg}), status


def _valid_name(name: str) -> bool:
    return bool(_NAME_RE.match(name))


def _build_ddl(nombre: str, columnas: list[dict]) -> str:
    """Construye un CREATE TABLE seguro desde la definición JSON."""
    col_defs = []
    pk_cols = []

    for col in columnas:
        col_nombre = col.get("nombre", "").strip()
        col_tipo = col.get("tipo", "").strip().upper()
        longitud = col.get("longitud")
        not_null = col.get("not_null", False)
        is_pk = col.get("primary_key", False)

        if not _valid_name(col_nombre):
            raise ValueError(f"Nombre de columna inválido: '{col_nombre}'")
        if col_tipo not in TIPOS_PERMITIDOS:
            raise ValueError(f"Tipo de columna no permitido: '{col_tipo}'")

        # Construir tipo con longitud si aplica
        if col_tipo == "VARCHAR":
            try:
                lng = int(longitud) if longitud else 255
            except (ValueError, TypeError):
                lng = 255
            tipo_str = f"VARCHAR({lng})"
        elif col_tipo == "CHAR":
            try:
                lng = int(longitud) if longitud else 10
            except (ValueError, TypeError):
                lng = 10
            tipo_str = f"CHAR({lng})"
        elif col_tipo == "DECIMAL":
            # longitud puede venir como "10,2"
            try:
                p, s = str(longitud).split(",") if longitud and "," in str(longitud) else ("10", "2")
                tipo_str = f"DECIMAL({int(p)},{int(s)})"
            except Exception:
                tipo_str = "DECIMAL(10,2)"
        else:
            tipo_str = col_tipo

        nn_str = " NOT NULL" if not_null else ""
        col_defs.append(f"  `{col_nombre}` {tipo_str}{nn_str}")

        if is_pk:
            pk_cols.append(f"`{col_nombre}`")

    if pk_cols:
        col_defs.append(f"  PRIMARY KEY ({', '.join(pk_cols)})")

    cols_sql = ",\n".join(col_defs)
    return f"CREATE TABLE IF NOT EXISTS `{nombre}` (\n{cols_sql}\n)"


# ── Listar tablas ──────────────────────────────────────────────────────────────

@bp_tablas.get("/api/tablas")
def listar_tablas():
    node = get_local_node()
    try:
        rows = execute_on_node(node, "SHOW TABLES")
        # SHOW TABLES retorna dicts con una sola key dinámica
        tablas = [list(row.values())[0] for row in rows]
    except Exception as exc:
        return _err(f"Error al listar tablas: {exc}", 500)
    return _ok(tablas)


# ── Crear tabla en todos los nodos ─────────────────────────────────────────────

@bp_tablas.post("/api/tablas")
def crear_tabla():
    data = request.get_json(silent=True) or {}
    nombre = data.get("nombre", "").strip()
    columnas = data.get("columnas", [])

    if not nombre:
        return _err("El campo 'nombre' es obligatorio.")
    if not _valid_name(nombre):
        return _err("Nombre de tabla inválido. Use solo letras, números y guión bajo.")
    if not columnas or not isinstance(columnas, list):
        return _err("Se debe definir al menos una columna.")

    try:
        ddl = _build_ddl(nombre, columnas)
    except ValueError as exc:
        return _err(str(exc))

    results = execute_on_all_nodes(ddl, fetch=False)
    return _ok({"tabla": nombre, "nodos": results}), 201


# ── Eliminar tabla en todos los nodos ──────────────────────────────────────────

@bp_tablas.delete("/api/tablas/<nombre>")
def eliminar_tabla(nombre: str):
    if not _valid_name(nombre):
        return _err("Nombre de tabla inválido.")

    ddl = f"DROP TABLE IF EXISTS `{nombre}`"
    results = execute_on_all_nodes(ddl, fetch=False)
    return _ok({"tabla": nombre, "nodos": results})
