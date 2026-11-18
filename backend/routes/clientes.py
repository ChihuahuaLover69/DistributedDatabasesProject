"""
routes/clientes.py — CRUD de Clientes con enrutamiento distribuido.

Reglas de negocio:
  - POST  → solo al nodo local (sucursal que registra)
  - PUT/DELETE → nodo propietario del ID, con fallback a búsqueda global
  - GET (lista/buscar) → todos los nodos en paralelo
  - GET por RFC → todos los nodos (el RFC es único globalmente)

Relación cliente-dirección:
  - Al crear un cliente se puede pasar IdDireccion (opcional) para asociarlo
    a una dirección existente en el nodo local.
  - La columna IdDireccion en Clientes es NULLABLE.

Validación RFC:
  - Se acepta cualquier cadena no vacía; la validación de formato se realiza
    opcionalmente en el frontend pero nunca es un bloqueo del backend.
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
    """
    for i, nodo in enumerate(get_all_nodes()):
        try:
            filas = execute_on_node(
                nodo,
                "SELECT Id, Nombre, ApellidoPaterno, ApellidoMaterno, RFC, IdDireccion "
                "FROM Clientes WHERE Id = %s",
                (client_id,),
            )
            if filas:
                return filas[0], i
        except Exception:
            continue
    return None, -1


# ── Info del nodo local ────────────────────────────────────────────────────────

@bp_clientes.get("/api/nodo-info")
def nodo_info():
    id_min, id_max = get_node_range(LOCAL_NODE_INDEX)
    nodo = NODES[LOCAL_NODE_INDEX]
    return _ok({
        "node_index": LOCAL_NODE_INDEX,
        "host": nodo["host"],
        "id_min": id_min,
        "id_max": id_max,
        "total_nodos": len(NODES),
    })


# ── Listar todos ───────────────────────────────────────────────────────────────

@bp_clientes.get("/api/clientes")
def listar_clientes():
    filas = execute_on_all_nodes_parallel(
        "SELECT Id, Nombre, ApellidoPaterno, ApellidoMaterno, RFC, IdDireccion "
        "FROM Clientes ORDER BY Id"
    )
    return _ok(filas)


# ── Buscar ─────────────────────────────────────────────────────────────────────

@bp_clientes.get("/api/clientes/buscar")
def buscar_clientes():
    q = request.args.get("q", "").strip()
    campo = request.args.get("campo", "nombre").lower()

    if not q:
        return _err("El parámetro 'q' es requerido.")

    like = f"%{q}%"

    if campo == "nombre":
        consulta = """
            SELECT Id, Nombre, ApellidoPaterno, ApellidoMaterno, RFC, IdDireccion
            FROM Clientes
            WHERE CONCAT(Nombre, ' ', ApellidoPaterno, ' ', IFNULL(ApellidoMaterno,'')) LIKE %s
            ORDER BY Id
        """
        parametros = (like,)
    elif campo == "rfc":
        consulta = """
            SELECT Id, Nombre, ApellidoPaterno, ApellidoMaterno, RFC, IdDireccion
            FROM Clientes
            WHERE RFC LIKE %s
            ORDER BY Id
        """
        parametros = (like,)
    elif campo == "domicilio":
        consulta = """
            SELECT DISTINCT c.Id, c.Nombre, c.ApellidoPaterno, c.ApellidoMaterno, c.RFC, c.IdDireccion
            FROM Clientes c
            JOIN Direcciones d ON d.IdCliente = c.Id
            WHERE d.Calle LIKE %s
               OR d.Colonia LIKE %s
               OR d.Estado LIKE %s
               OR d.CP LIKE %s
            ORDER BY c.Id
        """
        parametros = (like, like, like, like)
    else:
        return _err("Campo de búsqueda inválido. Use: nombre, rfc, domicilio.")

    filas = execute_on_all_nodes_parallel(consulta, parametros)
    return _ok(filas)


# ── Buscar por RFC ─────────────────────────────────────────────────────────────

@bp_clientes.get("/api/clientes/rfc/<string:rfc>")
def obtener_cliente_por_rfc(rfc: str):
    """Busca un cliente por RFC en todos los nodos. El RFC es único globalmente."""
    rfc = rfc.strip().upper()
    filas = execute_on_all_nodes_parallel(
        "SELECT Id, Nombre, ApellidoPaterno, ApellidoMaterno, RFC, IdDireccion "
        "FROM Clientes WHERE RFC = %s",
        (rfc,),
    )
    if not filas:
        return _err(f"Cliente con RFC '{rfc}' no encontrado.", 404)
    fila = filas[0]
    return _ok(fila, fila.get("node_index"))


# ── Obtener uno por ID ─────────────────────────────────────────────────────────

@bp_clientes.get("/api/clientes/<int:client_id>")
def obtener_cliente(client_id: int):
    """
    Obtiene un cliente por ID.
    Primero intenta en el nodo esperado por rango; si no, busca en todos.
    """
    try:
        nodo = get_node_for_id(client_id)
        node_index = get_node_index_for_id(client_id)
        filas = execute_on_node(
            nodo,
            "SELECT Id, Nombre, ApellidoPaterno, ApellidoMaterno, RFC, IdDireccion "
            "FROM Clientes WHERE Id = %s",
            (client_id,),
        )
        if filas:
            fila = filas[0]
            fila["node_index"] = node_index
            return _ok(fila, node_index)
    except (ValueError, Exception):
        pass

    fila, node_index = _buscar_cliente_por_id(client_id)
    if fila is None:
        return _err("Cliente no encontrado.", 404)
    fila["node_index"] = node_index
    return _ok(fila, node_index)


# ── Crear (solo nodo local) ────────────────────────────────────────────────────

@bp_clientes.post("/api/clientes")
def crear_cliente():
    datos = request.get_json(silent=True) or {}
    nombre  = datos.get("Nombre",          "").strip()
    ap_pat  = datos.get("ApellidoPaterno",  "").strip()
    ap_mat  = datos.get("ApellidoMaterno",  "").strip() or None
    rfc     = datos.get("RFC",             "").strip().upper()
    id_dir  = datos.get("IdDireccion")  # puede ser None

    if not nombre or not ap_pat or not rfc:
        return _err("Nombre, ApellidoPaterno y RFC son obligatorios.")

    # Normalizar IdDireccion
    if id_dir is not None:
        try:
            id_dir = int(id_dir)
        except (ValueError, TypeError):
            return _err("IdDireccion debe ser un número entero.")

    nodo = get_local_node()

    # Verificar que la dirección existe en el nodo local si se proporcionó
    if id_dir is not None:
        filas = execute_on_node(nodo, "SELECT Id FROM Direcciones WHERE Id = %s", (id_dir,))
        if not filas:
            return _err(f"La dirección con ID {id_dir} no existe en el nodo local.", 404)

    try:
        nuevo_id = execute_on_node(
            nodo,
            "INSERT INTO Clientes (Nombre, ApellidoPaterno, ApellidoMaterno, RFC, IdDireccion) "
            "VALUES (%s, %s, %s, %s, %s)",
            (nombre, ap_pat, ap_mat, rfc, id_dir),
            fetch=False,
        )
    except Exception as exc:
        msg = str(exc)
        if "Unknown column" in msg and "IdDireccion" in msg:
            # La columna aún no existe en la BD — reintentar sin ella
            try:
                nuevo_id = execute_on_node(
                    nodo,
                    "INSERT INTO Clientes (Nombre, ApellidoPaterno, ApellidoMaterno, RFC) "
                    "VALUES (%s, %s, %s, %s)",
                    (nombre, ap_pat, ap_mat, rfc),
                    fetch=False,
                )
            except Exception as exc2:
                msg2 = str(exc2)
                if "Duplicate entry" in msg2 and "RFC" in msg2:
                    return _err("El RFC ya está registrado.", 409, LOCAL_NODE_INDEX)
                return _err(f"Error al insertar: {msg2}", 500, LOCAL_NODE_INDEX)
        elif "Duplicate entry" in msg and "RFC" in msg:
            return _err("El RFC ya está registrado.", 409, LOCAL_NODE_INDEX)
        else:
            return _err(f"Error al insertar: {msg}", 500, LOCAL_NODE_INDEX)

    return _ok({"Id": nuevo_id}, LOCAL_NODE_INDEX), 201


# ── Actualizar ─────────────────────────────────────────────────────────────────

@bp_clientes.put("/api/clientes/<int:client_id>")
def actualizar_cliente(client_id: int):
    datos = request.get_json(silent=True) or {}
    nombre  = datos.get("Nombre",          "").strip()
    ap_pat  = datos.get("ApellidoPaterno",  "").strip()
    ap_mat  = datos.get("ApellidoMaterno",  "").strip() or None
    rfc     = datos.get("RFC",             "").strip().upper()
    id_dir  = datos.get("IdDireccion")

    if not nombre or not ap_pat or not rfc:
        return _err("Nombre, ApellidoPaterno y RFC son obligatorios.")

    if id_dir is not None:
        try:
            id_dir = int(id_dir)
        except (ValueError, TypeError):
            return _err("IdDireccion debe ser un número entero.")

    # Encontrar el nodo correcto con fallback a búsqueda global
    nodo = None
    node_index = -1
    try:
        nodo = get_node_for_id(client_id)
        node_index = get_node_index_for_id(client_id)
        filas = execute_on_node(nodo, "SELECT Id FROM Clientes WHERE Id = %s", (client_id,))
        if not filas:
            nodo = None
    except (ValueError, Exception):
        nodo = None

    if nodo is None:
        _, node_index = _buscar_cliente_por_id(client_id)
        if node_index == -1:
            return _err("Cliente no encontrado.", 404)
        nodo = NODES[node_index]

    try:
        afectadas = execute_on_node(
            nodo,
            "UPDATE Clientes SET Nombre=%s, ApellidoPaterno=%s, ApellidoMaterno=%s, "
            "RFC=%s, IdDireccion=%s WHERE Id=%s",
            (nombre, ap_pat, ap_mat, rfc, id_dir, client_id),
            fetch=False,
        )
    except Exception as exc:
        msg = str(exc)
        if "Unknown column" in msg and "IdDireccion" in msg:
            # Fallback si la columna no existe aún
            try:
                afectadas = execute_on_node(
                    nodo,
                    "UPDATE Clientes SET Nombre=%s, ApellidoPaterno=%s, "
                    "ApellidoMaterno=%s, RFC=%s WHERE Id=%s",
                    (nombre, ap_pat, ap_mat, rfc, client_id),
                    fetch=False,
                )
            except Exception as exc2:
                msg2 = str(exc2)
                if "Duplicate entry" in msg2 and "RFC" in msg2:
                    return _err("El RFC ya está registrado.", 409, node_index)
                return _err(f"Error al actualizar: {msg2}", 500, node_index)
        elif "Duplicate entry" in msg and "RFC" in msg:
            return _err("El RFC ya está registrado.", 409, node_index)
        else:
            return _err(f"Error al actualizar: {msg}", 500, node_index)

    if not afectadas:
        return _err("Cliente no encontrado.", 404, node_index)
    return _ok({"Id": client_id}, node_index)


# ── Eliminar ───────────────────────────────────────────────────────────────────

@bp_clientes.delete("/api/clientes/<int:client_id>")
def eliminar_cliente(client_id: int):
    nodo = None
    node_index = -1
    try:
        nodo = get_node_for_id(client_id)
        node_index = get_node_index_for_id(client_id)
        filas = execute_on_node(nodo, "SELECT Id FROM Clientes WHERE Id = %s", (client_id,))
        if not filas:
            nodo = None
    except (ValueError, Exception):
        nodo = None

    if nodo is None:
        _, node_index = _buscar_cliente_por_id(client_id)
        if node_index == -1:
            return _err("Cliente no encontrado.", 404)
        nodo = NODES[node_index]

    try:
        execute_on_node(nodo, "DELETE FROM Direcciones WHERE IdCliente = %s", (client_id,), fetch=False)
        afectadas = execute_on_node(nodo, "DELETE FROM Clientes WHERE Id = %s", (client_id,), fetch=False)
    except Exception as exc:
        return _err(f"Error al eliminar: {exc}", 500, node_index)

    if not afectadas:
        return _err("Cliente no encontrado.", 404, node_index)
    return _ok({"eliminado": client_id}, node_index)
