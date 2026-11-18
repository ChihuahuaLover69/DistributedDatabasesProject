"""
routes/direcciones.py — CRUD de Direcciones con enrutamiento distribuido.

Las Direcciones siempre viven en el mismo nodo que su cliente.
Reglas de negocio:
  - POST (anidado bajo cliente) → solo si el cliente pertenece al nodo local
  - POST independiente → solo al nodo local (crea sin cliente o con cliente dado)
  - GET independiente  → todos los nodos en paralelo
  - GET por cliente    → nodo propietario del IdCliente
  - PUT/DELETE         → nodo propietario de la dirección (búsqueda en todos)
"""

from flask import Blueprint, jsonify, request

from config import LOCAL_NODE_INDEX, NODES
from db import (
    execute_on_all_nodes_parallel,
    execute_on_node,
    get_all_nodes,
    get_local_node,
    get_node_for_id,
    get_node_index_for_id,
)

bp_direcciones = Blueprint("direcciones", __name__)


def _ok(data, node_index=None):
    return jsonify({"data": data, "error": None, "node_index": node_index})


def _err(msg, status=400, node_index=None):
    return jsonify({"data": None, "error": msg, "node_index": node_index}), status


# ── Buscar dirección por ID en todos los nodos (fallback robusto) ──────────────

def _buscar_direccion_por_id(dir_id: int) -> tuple[dict | None, int]:
    """
    Busca una dirección por ID en TODOS los nodos.
    Retorna (fila_dict, node_index) o (None, -1) si no se encuentra.
    """
    for i, nodo in enumerate(get_all_nodes()):
        try:
            filas = execute_on_node(
                nodo,
                "SELECT Id, Calle, Numero, Colonia, Estado, CP, IdCliente "
                "FROM Direcciones WHERE Id = %s",
                (dir_id,),
            )
            if filas:
                return filas[0], i
        except Exception:
            continue
    return None, -1


# ── Listar todas las direcciones (todos los nodos) ────────────────────────────

@bp_direcciones.get("/api/direcciones")
def listar_todas_direcciones():
    """Lista todas las direcciones de todos los nodos en paralelo."""
    filas = execute_on_all_nodes_parallel(
        "SELECT Id, Calle, Numero, Colonia, Estado, CP, IdCliente "
        "FROM Direcciones ORDER BY Id"
    )
    return _ok(filas)


# ── Buscar direcciones ─────────────────────────────────────────────────────────

@bp_direcciones.get("/api/direcciones/buscar")
def buscar_direcciones():
    """Busca direcciones por calle, colonia, estado o CP en todos los nodos."""
    q = request.args.get("q", "").strip()
    campo = request.args.get("campo", "calle").lower()

    if not q:
        return _err("El parámetro 'q' es requerido.")

    like = f"%{q}%"
    campos_validos = {
        "calle":   "Calle",
        "colonia": "Colonia",
        "estado":  "Estado",
        "cp":      "CP",
    }

    if campo not in campos_validos:
        return _err("Campo de búsqueda inválido. Use: calle, colonia, estado, cp.")

    col = campos_validos[campo]
    consulta = (
        f"SELECT Id, Calle, Numero, Colonia, Estado, CP, IdCliente "
        f"FROM Direcciones WHERE {col} LIKE %s ORDER BY Id"
    )
    filas = execute_on_all_nodes_parallel(consulta, (like,))
    return _ok(filas)


# ── Obtener una dirección por ID ──────────────────────────────────────────────

@bp_direcciones.get("/api/direcciones/<int:dir_id>")
def obtener_direccion(dir_id: int):
    """Obtiene una dirección por su ID buscando en todos los nodos."""
    fila, node_index = _buscar_direccion_por_id(dir_id)
    if fila is None:
        return _err("Dirección no encontrada.", 404)
    fila["node_index"] = node_index
    return _ok(fila, node_index)


# ── Listar direcciones de un cliente ──────────────────────────────────────────

@bp_direcciones.get("/api/clientes/<int:client_id>/direcciones")
def listar_direcciones(client_id: int):
    try:
        nodo = get_node_for_id(client_id)
        node_index = get_node_index_for_id(client_id)
    except ValueError as e:
        return _err(str(e), 404)

    filas = execute_on_node(
        nodo,
        "SELECT Id, Calle, Numero, Colonia, Estado, CP, IdCliente "
        "FROM Direcciones WHERE IdCliente = %s ORDER BY Id",
        (client_id,),
    )
    for fila in filas:
        fila["node_index"] = node_index
    return _ok(filas, node_index)


# ── Crear dirección independiente (nodo local) ────────────────────────────────

@bp_direcciones.post("/api/direcciones")
def crear_direccion_independiente():
    """
    Crea una dirección en el nodo local.
    IdCliente es opcional: puede quedar NULL si no se asocia a un cliente aún.
    """
    datos = request.get_json(silent=True) or {}
    calle   = datos.get("Calle",   "").strip()
    numero  = datos.get("Numero",  "").strip()
    colonia = datos.get("Colonia", "").strip()
    estado  = datos.get("Estado",  "").strip()
    cp      = datos.get("CP",      "").strip()
    id_cliente = datos.get("IdCliente")  # puede ser None

    if not all([calle, numero, colonia, estado, cp]):
        return _err("Los campos Calle, Numero, Colonia, Estado y CP son obligatorios.")
    if len(cp) != 5 or not cp.isdigit():
        return _err("El CP debe ser exactamente 5 dígitos.")

    # Validar que el IdCliente existe en el nodo local si se proporcionó
    nodo = get_local_node()
    if id_cliente is not None:
        try:
            id_cliente = int(id_cliente)
        except (ValueError, TypeError):
            return _err("IdCliente debe ser un número entero.")
        filas = execute_on_node(nodo, "SELECT Id FROM Clientes WHERE Id = %s", (id_cliente,))
        if not filas:
            return _err(f"El cliente con ID {id_cliente} no existe en el nodo local.", 404)

    try:
        nuevo_id = execute_on_node(
            nodo,
            "INSERT INTO Direcciones (Calle, Numero, Colonia, Estado, CP, IdCliente) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (calle, numero, colonia, estado, cp, id_cliente),
            fetch=False,
        )
    except Exception as exc:
        return _err(f"Error al insertar: {exc}", 500, LOCAL_NODE_INDEX)

    return _ok({"Id": nuevo_id}, LOCAL_NODE_INDEX), 201


# ── Crear dirección (anidada bajo cliente) ─────────────────────────────────────

@bp_direcciones.post("/api/clientes/<int:client_id>/direcciones")
def crear_direccion(client_id: int):
    """Crea una dirección para un cliente; solo desde la sucursal propietaria."""
    # Verificar que el cliente pertenece al nodo local
    client_node_index = get_node_index_for_id(client_id)
    if client_node_index != LOCAL_NODE_INDEX:
        return _err(
            f"Este cliente pertenece al nodo {client_node_index}. "
            "Solo puede registrar direcciones desde la sucursal propietaria.",
            403,
            client_node_index,
        )

    datos = request.get_json(silent=True) or {}
    calle   = datos.get("Calle",   "").strip()
    numero  = datos.get("Numero",  "").strip()
    colonia = datos.get("Colonia", "").strip()
    estado  = datos.get("Estado",  "").strip()
    cp      = datos.get("CP",      "").strip()

    if not all([calle, numero, colonia, estado, cp]):
        return _err("Todos los campos de dirección son obligatorios.")
    if len(cp) != 5 or not cp.isdigit():
        return _err("El CP debe ser exactamente 5 dígitos.")

    nodo = get_local_node()
    try:
        nuevo_id = execute_on_node(
            nodo,
            "INSERT INTO Direcciones (Calle, Numero, Colonia, Estado, CP, IdCliente) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (calle, numero, colonia, estado, cp, client_id),
            fetch=False,
        )
    except Exception as exc:
        return _err(f"Error al insertar: {exc}", 500, LOCAL_NODE_INDEX)

    return _ok({"Id": nuevo_id}, LOCAL_NODE_INDEX), 201


# ── Actualizar dirección ──────────────────────────────────────────────────────

@bp_direcciones.put("/api/direcciones/<int:dir_id>")
def actualizar_direccion(dir_id: int):
    datos = request.get_json(silent=True) or {}
    id_cliente = datos.get("IdCliente")

    calle   = datos.get("Calle",   "").strip()
    numero  = datos.get("Numero",  "").strip()
    colonia = datos.get("Colonia", "").strip()
    estado  = datos.get("Estado",  "").strip()
    cp      = datos.get("CP",      "").strip()

    if not all([calle, numero, colonia, estado, cp]):
        return _err("Todos los campos de dirección son obligatorios.")
    if len(cp) != 5 or not cp.isdigit():
        return _err("El CP debe ser exactamente 5 dígitos.")

    # Si se proporcionó IdCliente, enrutar por él; si no, buscar en todos los nodos
    nodo = None
    node_index = -1

    if id_cliente is not None:
        try:
            nodo = get_node_for_id(int(id_cliente))
            node_index = get_node_index_for_id(int(id_cliente))
        except (ValueError, Exception):
            nodo = None

    if nodo is None:
        # Buscar la dirección en todos los nodos para encontrar su nodo
        fila, node_index = _buscar_direccion_por_id(dir_id)
        if node_index == -1:
            return _err("Dirección no encontrada.", 404)
        nodo = NODES[node_index]
        if id_cliente is None:
            id_cliente = fila.get("IdCliente")

    try:
        # Si tiene IdCliente, incluirlo en el WHERE para mayor seguridad
        if id_cliente is not None:
            afectadas = execute_on_node(
                nodo,
                "UPDATE Direcciones SET Calle=%s, Numero=%s, Colonia=%s, Estado=%s, CP=%s "
                "WHERE Id=%s AND IdCliente=%s",
                (calle, numero, colonia, estado, cp, dir_id, id_cliente),
                fetch=False,
            )
        else:
            afectadas = execute_on_node(
                nodo,
                "UPDATE Direcciones SET Calle=%s, Numero=%s, Colonia=%s, Estado=%s, CP=%s "
                "WHERE Id=%s",
                (calle, numero, colonia, estado, cp, dir_id),
                fetch=False,
            )
    except Exception as exc:
        return _err(f"Error al actualizar: {exc}", 500, node_index)

    if not afectadas:
        return _err("Dirección no encontrada.", 404, node_index)
    return _ok({"Id": dir_id}, node_index)


# ── Eliminar dirección ────────────────────────────────────────────────────────

@bp_direcciones.delete("/api/direcciones/<int:dir_id>")
def eliminar_direccion(dir_id: int):
    datos = request.get_json(silent=True) or {}
    id_cliente = datos.get("IdCliente")

    nodo = None
    node_index = -1

    if id_cliente is not None:
        try:
            nodo = get_node_for_id(int(id_cliente))
            node_index = get_node_index_for_id(int(id_cliente))
        except (ValueError, Exception):
            nodo = None

    if nodo is None:
        # Buscar la dirección en todos los nodos
        _, node_index = _buscar_direccion_por_id(dir_id)
        if node_index == -1:
            return _err("Dirección no encontrada.", 404)
        nodo = NODES[node_index]

    try:
        if id_cliente is not None:
            afectadas = execute_on_node(
                nodo,
                "DELETE FROM Direcciones WHERE Id=%s AND IdCliente=%s",
                (dir_id, id_cliente),
                fetch=False,
            )
        else:
            afectadas = execute_on_node(
                nodo,
                "DELETE FROM Direcciones WHERE Id=%s",
                (dir_id,),
                fetch=False,
            )
    except Exception as exc:
        return _err(f"Error al eliminar: {exc}", 500, node_index)

    if not afectadas:
        return _err("Dirección no encontrada.", 404, node_index)
    return _ok({"eliminado": dir_id}, node_index)
