"""
routes/direcciones.py — CRUD de Direcciones con enrutamiento distribuido.

Las Direcciones siempre viven en el mismo nodo que su cliente.
Reglas de negocio:
  - POST → solo si el cliente pertenece al nodo local
  - PUT/DELETE → nodo propietario del IdCliente
  - GET → nodo propietario del IdCliente
"""

from flask import Blueprint, jsonify, request

from config import LOCAL_NODE_INDEX
from db import (
    execute_on_node,
    get_local_node,
    get_node_for_id,
    get_node_index_for_id,
)

bp_direcciones = Blueprint("direcciones", __name__)


def _ok(data, node_index=None):
    return jsonify({"data": data, "error": None, "node_index": node_index})


def _err(msg, status=400, node_index=None):
    return jsonify({"data": None, "error": msg, "node_index": node_index}), status


# ── Listar direcciones de un cliente ──────────────────────────────────────────

@bp_direcciones.get("/api/clientes/<int:client_id>/direcciones")
def listar_direcciones(client_id: int):
    try:
        node = get_node_for_id(client_id)
        node_index = get_node_index_for_id(client_id)
    except ValueError as e:
        return _err(str(e), 404)

    rows = execute_on_node(
        node,
        "SELECT Id, Calle, Numero, Colonia, Estado, CP, IdCliente FROM Direcciones WHERE IdCliente = %s ORDER BY Id",
        (client_id,),
    )
    for row in rows:
        row["node_index"] = node_index
    return _ok(rows, node_index)


# ── Crear dirección (solo si el cliente es del nodo local) ────────────────────

@bp_direcciones.post("/api/clientes/<int:client_id>/direcciones")
def crear_direccion(client_id: int):
    # Verificar que el cliente pertenece al nodo local
    client_node_index = get_node_index_for_id(client_id)
    if client_node_index != LOCAL_NODE_INDEX:
        return _err(
            f"Este cliente pertenece al nodo {client_node_index}. "
            "Solo puede registrar direcciones desde la sucursal propietaria.",
            403,
            client_node_index,
        )

    data = request.get_json(silent=True) or {}
    calle = data.get("Calle", "").strip()
    numero = data.get("Numero", "").strip()
    colonia = data.get("Colonia", "").strip()
    estado = data.get("Estado", "").strip()
    cp = data.get("CP", "").strip()

    if not all([calle, numero, colonia, estado, cp]):
        return _err("Todos los campos de dirección son obligatorios.")
    if len(cp) != 5 or not cp.isdigit():
        return _err("El CP debe ser exactamente 5 dígitos.")

    node = get_local_node()
    try:
        new_id = execute_on_node(
            node,
            "INSERT INTO Direcciones (Calle, Numero, Colonia, Estado, CP, IdCliente) VALUES (%s,%s,%s,%s,%s,%s)",
            (calle, numero, colonia, estado, cp, client_id),
            fetch=False,
        )
    except Exception as exc:
        return _err(f"Error al insertar: {exc}", 500, LOCAL_NODE_INDEX)

    return _ok({"Id": new_id}, LOCAL_NODE_INDEX), 201


# ── Actualizar dirección ──────────────────────────────────────────────────────

@bp_direcciones.put("/api/direcciones/<int:dir_id>")
def actualizar_direccion(dir_id: int):
    data = request.get_json(silent=True) or {}
    client_id = data.get("IdCliente")
    if not client_id:
        return _err("El campo IdCliente es obligatorio en el body para enrutar la operación.")

    calle = data.get("Calle", "").strip()
    numero = data.get("Numero", "").strip()
    colonia = data.get("Colonia", "").strip()
    estado = data.get("Estado", "").strip()
    cp = data.get("CP", "").strip()

    if not all([calle, numero, colonia, estado, cp]):
        return _err("Todos los campos de dirección son obligatorios.")
    if len(cp) != 5 or not cp.isdigit():
        return _err("El CP debe ser exactamente 5 dígitos.")

    try:
        node = get_node_for_id(int(client_id))
        node_index = get_node_index_for_id(int(client_id))
    except ValueError as e:
        return _err(str(e), 404)

    try:
        affected = execute_on_node(
            node,
            "UPDATE Direcciones SET Calle=%s, Numero=%s, Colonia=%s, Estado=%s, CP=%s WHERE Id=%s AND IdCliente=%s",
            (calle, numero, colonia, estado, cp, dir_id, client_id),
            fetch=False,
        )
    except Exception as exc:
        return _err(f"Error al actualizar: {exc}", 500, node_index)

    if not affected:
        return _err("Dirección no encontrada.", 404, node_index)
    return _ok({"Id": dir_id}, node_index)


# ── Eliminar dirección ────────────────────────────────────────────────────────

@bp_direcciones.delete("/api/direcciones/<int:dir_id>")
def eliminar_direccion(dir_id: int):
    data = request.get_json(silent=True) or {}
    client_id = data.get("IdCliente")
    if not client_id:
        return _err("El campo IdCliente es obligatorio en el body para enrutar la operación.")

    try:
        node = get_node_for_id(int(client_id))
        node_index = get_node_index_for_id(int(client_id))
    except ValueError as e:
        return _err(str(e), 404)

    try:
        affected = execute_on_node(
            node,
            "DELETE FROM Direcciones WHERE Id=%s AND IdCliente=%s",
            (dir_id, client_id),
            fetch=False,
        )
    except Exception as exc:
        return _err(f"Error al eliminar: {exc}", 500, node_index)

    if not affected:
        return _err("Dirección no encontrada.", 404, node_index)
    return _ok({"eliminado": dir_id}, node_index)
