# Sistema de Tiendas con Base de Datos Distribuida

Frontend web para un sistema de gestión de clientes con **fragmentación horizontal** sobre múltiples nodos MariaDB conectados via Tailscale.

---

## ¿Qué hace este sistema?

Cada sucursal (nodo) tiene su propia base de datos MariaDB con un fragmento del total de clientes. El sistema permite:

- **Registrar** clientes y direcciones únicamente desde la sucursal local
- **Consultar y actualizar** clientes desde cualquier sucursal
- **Buscar** por nombre, RFC o domicilio en todos los nodos simultáneamente
- **Crear tablas distribuidas** desde el frontend, propagando el esquema a todos los nodos

La lógica de enrutamiento es completamente transparente al usuario: el backend determina automáticamente a qué nodo enviar cada operación según el ID del cliente.

---

## Arquitectura

```
[Navegador — HTML/CSS/JS]
        │
        │ HTTP (fetch)
        ▼
[Backend Flask — Python]
        │
        ├─── INSERT (solo nodo local) ──────► [MariaDB Nodo 0: 100.120.137.4]
        ├─── UPDATE/DELETE (por ID) ────────► [Nodo correcto según rango]
        ├─── SELECT global (paralelo) ──────► [Todos los nodos — merge]
        └─── CREATE TABLE ──────────────────► [Todos los nodos]
```

### Fragmentación por rangos de IDs

| Nodo | IP            | Rango de IDs de Clientes |
|------|---------------|--------------------------|
| 0    | 100.120.137.4 | 1 – 1500                 |
| 1    | 100.119.128.65| 1501 – 3000              |

El tamaño del bloque (`BLOCK_SIZE = 1500`) y la lista de nodos se configuran en [`backend/config.py`](backend/config.py).

---

## Estructura del proyecto

```
├── start.sh                  ← Arranca el sistema completo
├── stop.sh                   ← Detiene el backend
│
├── backend/
│   ├── app.py                ← Instancia Flask
│   ├── config.py             ← Nodos, BLOCK_SIZE, detección de nodo local
│   ├── db.py                 ← Enrutamiento y helpers de BD
│   ├── requirements.txt      ← Dependencias Python
│   └── routes/
│       ├── clientes.py       ← CRUD de clientes (7 endpoints)
│       ├── direcciones.py    ← CRUD de direcciones (4 endpoints)
│       └── tablas.py         ← Gestión de tablas distribuidas (3 endpoints)
│
└── frontend/
    ├── index.html            ← Interfaz principal
    ├── style.css             ← Estilos
    └── app.js                ← Lógica JS
```

---

## Requisitos previos

- **Python 3.10+**
- **pip** con las dependencias instaladas:
  ```bash
  pip install -r backend/requirements.txt
  ```
- **MariaDB** accesible en los nodos via Tailscale
- Base de datos llamada `tienda` creada en cada nodo con las tablas:

```sql
CREATE TABLE Clientes (
    Id BIGINT UNSIGNED AUTO_INCREMENT,
    Nombre VARCHAR(80) NOT NULL,
    ApellidoPaterno VARCHAR(80) NOT NULL,
    ApellidoMaterno VARCHAR(80),
    RFC VARCHAR(13) NOT NULL,
    PRIMARY KEY (Id),
    UNIQUE (RFC)
);

CREATE TABLE Direcciones (
    Id BIGINT UNSIGNED AUTO_INCREMENT,
    Calle VARCHAR(120) NOT NULL,
    Numero VARCHAR(20) NOT NULL,
    Colonia VARCHAR(100) NOT NULL,
    Estado VARCHAR(100) NOT NULL,
    CP CHAR(5) NOT NULL,
    IdCliente BIGINT UNSIGNED NOT NULL,
    PRIMARY KEY (Id),
    FOREIGN KEY (IdCliente) REFERENCES Clientes(Id)
);
```

### Configurar AUTO_INCREMENT por nodo

Ejecutar **en cada nodo** para garantizar que los IDs caigan en el rango correcto:

| Nodo | SQL a ejecutar                               |
|------|----------------------------------------------|
| 0    | *(sin cambios, inicia en 1 por defecto)*     |
| 1    | `ALTER TABLE Clientes AUTO_INCREMENT = 1501;`|

---

## Cómo correr el sistema

```bash
bash start.sh
```

El script:
1. Verifica que Python y las dependencias estén disponibles
2. Arranca el backend Flask en `http://localhost:5000` (en segundo plano)
3. Espera a que el servidor esté listo
4. Abre el frontend en el navegador automáticamente

Para detener el backend:

```bash
bash stop.sh
```

Los logs del backend se guardan en `backend.log`.

---

## Endpoints del API

### Clientes

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/api/nodo-info` | Info del nodo local (índice, rango, host) |
| `GET` | `/api/clientes` | Lista todos los clientes de todos los nodos |
| `GET` | `/api/clientes/buscar?q=&campo=` | Busca por `nombre`, `rfc` o `domicilio` |
| `GET` | `/api/clientes/<id>` | Obtiene un cliente por ID |
| `POST` | `/api/clientes` | Crea un cliente *(solo en el nodo local)* |
| `PUT` | `/api/clientes/<id>` | Actualiza un cliente |
| `DELETE` | `/api/clientes/<id>` | Elimina un cliente y sus direcciones |

### Direcciones

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/api/clientes/<id>/direcciones` | Lista las direcciones de un cliente |
| `POST` | `/api/clientes/<id>/direcciones` | Agrega dirección *(solo si el cliente es del nodo local)* |
| `PUT` | `/api/direcciones/<id>` | Actualiza una dirección (requiere `IdCliente` en body) |
| `DELETE` | `/api/direcciones/<id>` | Elimina una dirección (requiere `IdCliente` en body) |

### Tablas distribuidas

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/api/tablas` | Lista las tablas existentes |
| `POST` | `/api/tablas` | Crea una tabla en **todos** los nodos |
| `DELETE` | `/api/tablas/<nombre>` | Elimina una tabla en **todos** los nodos |

---

## Cómo agregar un nodo nuevo

1. Editar [`backend/config.py`](backend/config.py) y agregar una entrada a `NODES`:

```python
NODES = [
    {"host": "100.120.137.4",  "port": 3306, "db": "tienda", "user": "Compañero", "password": "UnaContraseñaSegura123!"},
    {"host": "100.119.128.65", "port": 3306, "db": "tienda", "user": "Compañero", "password": "UnaContraseñaSegura123!"},
    {"host": "100.XXX.XXX.XX", "port": 3306, "db": "tienda", "user": "Compañero", "password": "UnaContraseñaSegura123!"},  # nuevo
]
```

2. Ejecutar en la BD del nuevo nodo (donde `N` es el índice del nodo × 1500 + 1):

```sql
ALTER TABLE Clientes AUTO_INCREMENT = 3001;
```

No se requiere ningún otro cambio. El backend recalcula los rangos automáticamente.

---

## Reglas de negocio

| Operación | Restricción |
|-----------|-------------|
| Insertar cliente o dirección | Solo desde la **sucursal local** (el nodo donde corre el backend) |
| Actualizar cliente o dirección | Desde **cualquier** sucursal |
| Buscar / listar clientes | Desde **cualquier** sucursal — consulta todos los nodos en paralelo |
| Crear / eliminar tablas | Se propaga a **todos** los nodos simultáneamente |
