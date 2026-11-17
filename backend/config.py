import socket

# ── Tamaño de bloque por nodo ──────────────────────────────────────────────────
BLOCK_SIZE = 1500

# ── Lista de nodos ─────────────────────────────────────────────────────────────
# Para agregar un nodo nuevo: añadir una entrada aquí y ejecutar en esa BD:
#   ALTER TABLE Clientes AUTO_INCREMENT = <indice> * BLOCK_SIZE + 1;
NODES = [
    {
        "host": "100.120.137.4",
        "port": 3306,
        "db": "sucursal_morelia",
        "user": "Compañero",
        "password": "UnaClaveSegura123!",
    },
    {
        "host": "100.119.128.65",
        "port": 3306,
        "db": "suc_patzcuaro",
        "user": "Compañero",
        "password": "UnaClaveSegura123!",
    },
]


def get_node_range(index: int) -> tuple[int, int]:
    """Calcula (id_min, id_max) para el nodo en la posición index."""
    id_min = index * BLOCK_SIZE + 1
    id_max = (index + 1) * BLOCK_SIZE
    return id_min, id_max


def detect_local_node() -> int:
    """
    Detecta el índice del nodo local comparando las IPs de red locales
    con los hosts definidos en NODES.
    Retorna el índice del nodo local, o -1 si no coincide ninguno
    (útil en entornos de desarrollo sin Tailscale).
    """
    local_ips: set[str] = set()

    # Obtener todas las IPs locales disponibles
    try:
        hostname = socket.gethostname()
        results = socket.getaddrinfo(hostname, None)
        for result in results:
            local_ips.add(result[4][0])
    except Exception:
        pass

    # Incluir 127.0.0.1 y localhost como fallback para desarrollo
    local_ips.add("127.0.0.1")
    local_ips.add("localhost")

    for i, node in enumerate(NODES):
        if node["host"] in local_ips:
            return i

    # Fallback: si no se detecta, usar el nodo 0 (útil en desarrollo local)
    return 0


# Índice del nodo local — calculado una vez al importar el módulo
LOCAL_NODE_INDEX: int = detect_local_node()
