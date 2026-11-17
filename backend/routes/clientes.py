"""
routes/clientes.py — CRUD de Clientes con enrutamiento distribuido.

Reglas de negocio:
  - POST  → solo al nodo local (sucursal que registra)
  - PUT/DELETE → nodo propietario del ID, con fallback a búsqueda global
  - GET (lista/buscar) → todos los nodos en paralelo
  - GET por RFC → todos los nodos (el RFC es único globalmente)

Bug fix: get_node_for_id fallaba con IDs fuera del rango esperado (ej. cuando
  AUTO_INCREMENT no estaba configurado correctamente). Se agrega un fallback
  que busca en todos los nodos antes de devolver 404.
"""

from flask import Blueprint, jsonify, request

from config import LOCAL_NODE_INDEX, NODES, get_node_range
from db import (
    execute_on_all_nodes_parallel,
    execute_on_node,
    get_all_nodes,
    get_local_node,
    get_node_for_id,
    get_node_index_for_id,
)

bp_clientes = Blueprint("clientes", __name__)


def _ok(data, node_index=None):
    return jsonify({"data": data, "error": None, "node_index": node_index})


def _err(msg, status=400, node_index=None):
    return jsonify({"data": None, "error": msg, "node_index": node_index}), status


# ── Búsqueda en todos los nodos por ID (fallback robusto) ─────────────────────

def _buscar_cliente_por_id(client_id: int) -> tuple[dict | None, int]:
    """
    Busca un cliente por ID en TODOS los nodos.
    Retorna (fila_dict, node_index) o (None, -1) si no se encuentra.

    Se usa como fallback cuando get_node_for_id apunta al nodo incorrecto,
    lo que puede suceder si el AUTO_INCREMENT no estaba configurado correctamente
    al momento de insertar el registro.
    """
    for i, node in enumerate(get_all_nodes()):
        try:
            rows = execute_on_node(
                node,
                "SELECT Id, Nombre, ApellidoPaterno, ApellidoMaterno, RFC "
                "FROM Clientes WHERE Id = %s",
                (client_id,),
            )
            if rows:
                return rows[0], i
        except Exception:
            continue
    return None, -1


# ── Info del nodo local ────────────────────────────────────────────────────────

@bp_clientes.get("/api/nodo-info")
def nodo_info():
    id_min, id_max = get_node_range(LOCAL_NODE_INDEX)
    node = NODES[LOCAL_NODE_INDEX]
    return _ok({
        "node_index": LOCAL_NODE_INDEX,
        "host": node["host"],
        "id_min": id_min,
        "id_max": id_max,
        "total_nodos": len(NODES),
    })


# ── Listar todos ───────────────────────────────────────────────────────────────

@bp_clientes.get("/api/clientes")
def listar_clientes():
    rows = execute_on_all_nodes_parallel(
        "SELECT Id, Nombre, ApellidoPaterno, ApellidoMaterno, RFC FROM Clientes ORDER BY Id"
    )
    return _ok(rows)


# ── Buscar ─────────────────────────────────────────────────────────────────────

@bp_clientes.get("/api/clientes/buscar")
def buscar_clientes():
    q = request.args.get("q", "").strip()
    campo = request.args.get("campo", "nombre").lower()

    if not q:
        return _err("El parámetro 'q' es requerido.")

    like = f"%{q}%"

    if campo == "nombre":
        query = """
            SELECT Id, Nombre, ApellidoPaterno, ApellidoMaterno, RFC
            FROM Clientes
            WHERE CONCAT(Nombre, ' ', ApellidoPaterno, ' ', IFNULL(ApellidoMaterno,'')) LIKE %s
            ORDER BY Id
        """
        params = (like,)
    elif campo == "rfc":
        query = """
            SELECT Id, Nombre, ApellidoPaterno, ApellidoMaterno, RFC
            FROM Clientes
            WHERE RFC LIKE %s
            ORDER BY Id
        """
        params = (like,)
    elif campo == "domicilio":
        query = """
            SELECT DISTINCT c.Id, c.Nombre, c.ApellidoPaterno, c.ApellidoMaterno, c.RFC
            FROM Clientes c
            JOIN Direcciones d ON d.IdCliente = c.Id
            WHERE d.Calle LIKE %s
               OR d.Colonia LIKE %s
               OR d.Estado LIKE %s
               OR d.CP LIKE %s
            ORDER BY c.Id
        """
        params = (like, like, like, like)
    else:
        return _err("Campo de búsqueda inválido. Use: nombre, rfc, domicilio.")

    rows = execute_on_all_nodes_parallel(query, params)
    return _ok(rows)


# ── Buscar por RFC (identificador único global) ───────────────────────────────

@bp_clientes.get("/api/clientes/rfc/<string:rfc>")
def obtener_cliente_por_rfc(rfc: str):
    """Busca un cliente por RFC en todos los nodos. El RFC es único globalmente."""
    rfc = rfc.strip().upper()
    rows = execute_on_all_nodes_parallel(
        "SELECT Id, Nombre, ApellidoPaterno, ApellidoMaterno, RFC "
        "FROM Clientes WHERE RFC = %s",
        (rfc,),
    )
    if not rows:
        return _err(f"Cliente con RFC '{rfc}' no encontrado.", 404)
    row = rows[0]
    return _ok(row, row.get("node_index"))


# ── Obtener uno por ID ─────────────────────────────────────────────────────────

@bp_clientes.get("/api/clientes/<int:client_id>")
def obtener_cliente(client_id: int):
    """
    Obtiene un cliente por ID.
    Primero intenta en el nodo esperado por rango; si no lo encuentra,
    busca en todos los nodos (fallback para IDs mal asignados por AUTO_INCREMENT).
    """
    # Intentar primero en el nodo que debería tener este ID según el rango
    try:
        node = get_node_for_id(client_id)
        node_index = get_node_index_for_id(client_id)
        rows = execute_on_node(
            node,
            "SELECT Id, Nombre, ApellidoPaterno, ApellidoMaterno, RFC FROM Clientes WHERE Id = %s",
            (client_id,),
        )
        if rows:
            row = rows[0]
            row["node_index"] = node_index
            return _ok(row, node_index)
    except (ValueError, Exception):
        pass

    # Fallback: buscar en todos los nodos
    row, node_index = _buscar_cliente_por_id(client_id)
    if row is None:
        return _err("Cliente no encontrado.", 404)
    row["node_index"] = node_index
    return _ok(row, node_index)


# ── Crear (solo nodo local) ────────────────────────────────────────────────────

@bp_clientes.post("/api/clientes")
def crear_cliente():
    data = request.get_json(silent=True) or {}
    nombre = data.get("Nombre", "").strip()
    ap_pat = data.get("ApellidoPaterno", "").strip()
    ap_mat = data.get("ApellidoMaterno", "").strip() or None
    rfc = data.get("RFC", "").strip().upper()

    if not nombre or not ap_pat or not rfc:
        return _err("Nombre, ApellidoPaterno y RFC son obligatorios.")

    node = get_local_node()
    try:
        new_id = execute_on_node(
            node,
            "INSERT INTO Clientes (Nombre, ApellidoPaterno, ApellidoMaterno, RFC) VALUES (%s, %s, %s, %s)",
            (nombre, ap_pat, ap_mat, rfc),
            fetch=False,
        )
    except Exception as exc:
        msg = str(exc)
        if "Duplicate entry" in msg and "RFC" in msg:
            return _err("El RFC ya está registrado.", 409, LOCAL_NODE_INDEX)
        return _err(f"Error al insertar: {msg}", 500, LOCAL_NODE_INDEX)

    return _ok({"Id": new_id}, LOCAL_NODE_INDEX), 201


# ── Actualizar ─────────────────────────────────────────────────────────────────

@bp_clientes.put("/api/clientes/<int:client_id>")
def actualizar_cliente(client_id: int):
    data = request.get_json(silent=True) or {}
    nombre = data.get("Nombre", "").strip()
    ap_pat = data.get("ApellidoPaterno", "").strip()
    ap_mat = data.get("ApellidoMaterno", "").strip() or None
    rfc = data.get("RFC", "").strip().upper()

    if not nombre or not ap_pat or not rfc:
        return _err("Nombre, ApellidoPaterno y RFC son obligatorios.")

    # Encontrar el nodo correcto con fallback a búsqueda global
    node = None
    node_index = -1
    try:
        node = get_node_for_id(client_id)
        node_index = get_node_index_for_id(client_id)
        # Verificar que el cliente realmente está en ese nodo
        rows = execute_on_node(
            node,
            "SELECT Id FROM Clientes WHERE Id = %s",
            (client_id,),
        )
        if not rows:
            node = None  # no está ahí — forzar fallback
    except (ValueError, Exception):
        node = None

    if node is None:
        # Fallback: buscar en todos los nodos
        _, node_index = _buscar_cliente_por_id(client_id)
        if node_index == -1:
            return _err("Cliente no encontrado.", 404)
        node = NODES[node_index]

    try:
        affected = execute_on_node(
            node,
            "UPDATE Clientes SET Nombre=%s, ApellidoPaterno=%s, ApellidoMaterno=%s, RFC=%s WHERE Id=%s",
            (nombre, ap_pat, ap_mat, rfc, client_id),
            fetch=False,
        )
    except Exception as exc:
        msg = str(exc)
        if "Duplicate entry" in msg and "RFC" in msg:
            return _err("El RFC ya está registrado.", 409, node_index)
        return _err(f"Error al actualizar: {msg}", 500, node_index)

    if not affected:
        return _err("Cliente no encontrado.", 404, node_index)
    return _ok({"Id": client_id}, node_index)


# ── Eliminar ───────────────────────────────────────────────────────────────────

@bp_clientes.delete("/api/clientes/<int:client_id>")
def eliminar_cliente(client_id: int):
    # Encontrar el nodo correcto con fallback a búsqueda global
    node = None
    node_index = -1
    try:
        node = get_node_for_id(client_id)
        node_index = get_node_index_for_id(client_id)
        rows = execute_on_node(
            node,
            "SELECT Id FROM Clientes WHERE Id = %s",
            (client_id,),
        )
        if not rows:
            node = None
    except (ValueError, Exception):
        node = None

    if node is None:
        _, node_index = _buscar_cliente_por_id(client_id)
        if node_index == -1:
            return _err("Cliente no encontrado.", 404)
        node = NODES[node_index]

    try:
        execute_on_node(node, "DELETE FROM Direcciones WHERE IdCliente = %s", (client_id,), fetch=False)
        affected = execute_on_node(node, "DELETE FROM Clientes WHERE Id = %s", (client_id,), fetch=False)
    except Exception as exc:
        return _err(f"Error al eliminar: {exc}", 500, node_index)

    if not affected:
        return _err("Cliente no encontrado.", 404, node_index)
    return _ok({"eliminado": client_id}, node_index)
