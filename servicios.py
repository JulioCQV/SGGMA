"""
servicios.py
Lógica de negocio y consultas SQL del SGGMA (sin código de Tkinter).

Convenciones:
  - Todas las funciones reciben `conexion` (de database.obtener_conexion) como primer parámetro.
  - Las reglas de negocio violadas lanzan `ErrorNegocio` con un mensaje listo para mostrar en la UI.
  - Las operaciones que tocan inventario usan database.transaccion (BEGIN/COMMIT/ROLLBACK).
  - Borrado lógico: nunca se hace DELETE sobre categorías ni productos.
"""

import hmac
import os
import re
import sqlite3
import textwrap
from datetime import datetime
from database import transaccion, ESTADO_ACTIVO, ESTADO_INACTIVO


class ErrorNegocio(Exception):
    """Error de validación o regla de negocio; el mensaje se muestra al usuario."""


# Valores por defecto y catálogos del negocio
METODOS_PAGO = ("Efectivo", "Tarjeta", "SINPE")
METODO_PAGO_POR_DEFECTO = "Efectivo"
IMPUESTO_POR_DEFECTO = 13.0        # % (IVA)
UTILIDAD_POR_DEFECTO = 0.0         # %
STOCK_MINIMO_POR_DEFECTO = 5       # unidades

# Datos del ticket impreso
NOMBRE_NEGOCIO = "GeMMa Mini Abastecedor"
ANCHO_TICKET = 40                  # caracteres por línea (papel de 80 mm aprox.)
CARPETA_PROYECTO = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# Utilidades de validación
# ---------------------------------------------------------------------------
def _texto_obligatorio(valor, nombre_campo, maximo):
    valor = (valor or "").strip()
    if not valor:
        raise ErrorNegocio(f"El campo '{nombre_campo}' es obligatorio.")
    if len(valor) > maximo:
        raise ErrorNegocio(f"El campo '{nombre_campo}' no puede superar {maximo} caracteres.")
    return valor


def _texto_opcional(valor, nombre_campo, maximo):
    valor = (valor or "").strip()
    if len(valor) > maximo:
        raise ErrorNegocio(f"El campo '{nombre_campo}' no puede superar {maximo} caracteres.")
    return valor or None


def _numero_no_negativo(valor, nombre_campo):
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        raise ErrorNegocio(f"'{nombre_campo}' debe ser un número válido.")
    if numero < 0:
        raise ErrorNegocio(f"'{nombre_campo}' no puede ser negativo.")
    return round(numero, 2)


def _entero(valor, nombre_campo, minimo):
    try:
        if isinstance(valor, float) and not valor.is_integer():
            raise ValueError
        numero = int(valor)
    except (TypeError, ValueError):
        raise ErrorNegocio(f"'{nombre_campo}' debe ser un número entero.")
    if numero < minimo:
        raise ErrorNegocio(f"'{nombre_campo}' debe ser mayor o igual a {minimo}.")
    return numero


def _validar_fecha(valor, nombre_campo="fecha de vencimiento"):
    """Fecha opcional en formato AAAA-MM-DD."""
    valor = (valor or "").strip()
    if not valor:
        return None
    from datetime import datetime
    try:
        datetime.strptime(valor, "%Y-%m-%d")
    except ValueError:
        raise ErrorNegocio(f"La {nombre_campo} debe tener el formato AAAA-MM-DD.")
    return valor


def _filtro_estado(estado):
    """Devuelve (cláusula SQL, parámetros) según 'activo', 'inactivo' o None (todos)."""
    if estado is None:
        return "", []
    if estado not in (ESTADO_ACTIVO, ESTADO_INACTIVO):
        raise ErrorNegocio("Estado de filtro inválido.")
    return " AND estado = ?", [estado]


# ===========================================================================
# CATEGORÍAS
# ===========================================================================
def crear_categoria(conexion, nombre, descripcion=None):
    nombre = _texto_obligatorio(nombre, "Nombre", 50)
    descripcion = _texto_opcional(descripcion, "Descripción", 200)
    try:
        cursor = conexion.execute(
            "INSERT INTO categorias (nombre, descripcion) VALUES (?, ?)",
            (nombre, descripcion),
        )
    except sqlite3.IntegrityError:
        raise ErrorNegocio("Ya existe una categoría con ese nombre.")
    return cursor.lastrowid


def listar_categorias(conexion, estado=ESTADO_ACTIVO, busqueda=""):
    """estado: 'activo' (por defecto), 'inactivo' o None para todas."""
    filtro, params = _filtro_estado(estado)
    sql = "SELECT * FROM categorias WHERE nombre LIKE ?" + filtro + " ORDER BY nombre"
    return conexion.execute(sql, [f"%{busqueda.strip()}%"] + params).fetchall()


def obtener_categoria(conexion, id_categoria):
    fila = conexion.execute(
        "SELECT * FROM categorias WHERE id_categoria = ?", (id_categoria,)
    ).fetchone()
    if fila is None:
        raise ErrorNegocio("La categoría no existe.")
    return fila


def modificar_categoria(conexion, id_categoria, nombre, descripcion=None):
    obtener_categoria(conexion, id_categoria)
    nombre = _texto_obligatorio(nombre, "Nombre", 50)
    descripcion = _texto_opcional(descripcion, "Descripción", 200)
    try:
        conexion.execute(
            "UPDATE categorias SET nombre = ?, descripcion = ? WHERE id_categoria = ?",
            (nombre, descripcion, id_categoria),
        )
    except sqlite3.IntegrityError:
        raise ErrorNegocio("Ya existe otra categoría con ese nombre.")


def desactivar_categoria(conexion, id_categoria):
    """Borrado lógico. Los productos existentes de la categoría se conservan."""
    obtener_categoria(conexion, id_categoria)
    conexion.execute(
        "UPDATE categorias SET estado = ? WHERE id_categoria = ?",
        (ESTADO_INACTIVO, id_categoria),
    )


def reactivar_categoria(conexion, id_categoria):
    obtener_categoria(conexion, id_categoria)
    conexion.execute(
        "UPDATE categorias SET estado = ? WHERE id_categoria = ?",
        (ESTADO_ACTIVO, id_categoria),
    )


# ===========================================================================
# PRODUCTOS
# ===========================================================================
def _validar_categoria_activa(conexion, id_categoria):
    categoria = obtener_categoria(conexion, id_categoria)
    if categoria["estado"] != ESTADO_ACTIVO:
        raise ErrorNegocio("La categoría seleccionada está inactiva.")


def _validar_proveedor_activo(conexion, id_proveedor):
    fila = conexion.execute(
        "SELECT estado FROM proveedores WHERE id_proveedor = ?", (id_proveedor,)
    ).fetchone()
    if fila is None:
        raise ErrorNegocio("El proveedor no existe.")
    if fila["estado"] != ESTADO_ACTIVO:
        raise ErrorNegocio("El proveedor seleccionado está inactivo.")


def _validar_porcentaje(valor, nombre_campo, maximo=None):
    """Porcentaje numérico >= 0 (y <= maximo si se indica)."""
    numero = _numero_no_negativo(valor, nombre_campo)
    if maximo is not None and numero > maximo:
        raise ErrorNegocio(f"'{nombre_campo}' no puede ser mayor a {maximo:g}.")
    return numero


def calcular_precio_sugerido(precio_compra, porcentaje_impuesto, porcentaje_utilidad):
    """
    Precio de venta sugerido = costo + impuesto + utilidad, donde ambos porcentajes
    se calculan sobre el costo (precio de compra):

        precio = costo + costo * impuesto% + costo * utilidad%

    Ejemplo: costo 1000, impuesto 13 %, utilidad 20 % -> 1000 + 130 + 200 = 1330.00
    Devuelve el precio redondeado a 2 decimales; lanza ErrorNegocio si algún dato es inválido.
    """
    costo = _numero_no_negativo(precio_compra, "Precio de compra")
    impuesto = costo * _validar_porcentaje(porcentaje_impuesto, "Porcentaje de impuesto", 100) / 100
    utilidad = costo * _validar_porcentaje(porcentaje_utilidad, "Porcentaje de utilidad") / 100
    return round(costo + impuesto + utilidad, 2)


def crear_producto(conexion, nombre, id_categoria, precio_compra, precio_venta,
                   existencia_inicial, id_proveedor=None, fecha_vencimiento=None,
                   porcentaje_impuesto=IMPUESTO_POR_DEFECTO,
                   porcentaje_utilidad=UTILIDAD_POR_DEFECTO,
                   stock_minimo=STOCK_MINIMO_POR_DEFECTO):
    """
    precio_venta: si es None o vacío se usa calcular_precio_sugerido(...).
    porcentaje_impuesto / porcentaje_utilidad: en % (13 = 13 %).
    stock_minimo: umbral de la alerta de stock bajo.
    """
    nombre = _texto_obligatorio(nombre, "Nombre", 100)
    precio_compra = _numero_no_negativo(precio_compra, "Precio de compra")
    porcentaje_impuesto = _validar_porcentaje(porcentaje_impuesto, "Porcentaje de impuesto", 100)
    porcentaje_utilidad = _validar_porcentaje(porcentaje_utilidad, "Porcentaje de utilidad")
    stock_minimo = _entero(stock_minimo, "Stock mínimo", 0)
    if precio_venta is None or str(precio_venta).strip() == "":
        precio_venta = calcular_precio_sugerido(precio_compra, porcentaje_impuesto, porcentaje_utilidad)
    else:
        precio_venta = _numero_no_negativo(precio_venta, "Precio de venta")
    existencia = _entero(existencia_inicial, "Existencia inicial", 0)
    fecha_vencimiento = _validar_fecha(fecha_vencimiento)
    _validar_categoria_activa(conexion, id_categoria)
    if id_proveedor:
        _validar_proveedor_activo(conexion, id_proveedor)
    else:
        id_proveedor = None
    try:
        cursor = conexion.execute(
            """INSERT INTO productos (nombre, id_categoria, precio_compra, precio_venta,
                                      existencia, id_proveedor, fecha_vencimiento,
                                      porcentaje_impuesto, porcentaje_utilidad, stock_minimo)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (nombre, id_categoria, precio_compra, precio_venta, existencia,
             id_proveedor, fecha_vencimiento,
             porcentaje_impuesto, porcentaje_utilidad, stock_minimo),
        )
    except sqlite3.IntegrityError:
        raise ErrorNegocio("Ya existe un producto con ese nombre en la categoría seleccionada.")
    return cursor.lastrowid


def listar_productos(conexion, estado=ESTADO_ACTIVO, busqueda="", id_categoria=None):
    """Incluye el nombre de la categoría y del proveedor habitual para mostrar en tablas."""
    sql = """
        SELECT p.*, c.nombre AS categoria, pr.nombre AS proveedor
        FROM productos p
        JOIN categorias c ON c.id_categoria = p.id_categoria
        LEFT JOIN proveedores pr ON pr.id_proveedor = p.id_proveedor
        WHERE p.nombre LIKE ?"""
    params = [f"%{busqueda.strip()}%"]
    if estado is not None:
        if estado not in (ESTADO_ACTIVO, ESTADO_INACTIVO):
            raise ErrorNegocio("Estado de filtro inválido.")
        sql += " AND p.estado = ?"
        params.append(estado)
    if id_categoria:
        sql += " AND p.id_categoria = ?"
        params.append(id_categoria)
    sql += " ORDER BY p.nombre"
    return conexion.execute(sql, params).fetchall()


def obtener_producto(conexion, id_producto):
    fila = conexion.execute(
        "SELECT * FROM productos WHERE id_producto = ?", (id_producto,)
    ).fetchone()
    if fila is None:
        raise ErrorNegocio("El producto no existe.")
    return fila


def modificar_producto(conexion, id_producto, nombre, id_categoria, precio_compra,
                       precio_venta, id_proveedor=None, fecha_vencimiento=None,
                       porcentaje_impuesto=None, porcentaje_utilidad=None, stock_minimo=None):
    """
    No modifica la existencia: el stock solo cambia mediante compras, ventas
    o correcciones del módulo Admin.
    porcentaje_impuesto / porcentaje_utilidad / stock_minimo: si son None se conserva el
    valor actual del producto. precio_venta None o vacío => precio sugerido.
    """
    actual = obtener_producto(conexion, id_producto)
    nombre = _texto_obligatorio(nombre, "Nombre", 100)
    precio_compra = _numero_no_negativo(precio_compra, "Precio de compra")
    if porcentaje_impuesto is None:
        porcentaje_impuesto = actual["porcentaje_impuesto"]
    if porcentaje_utilidad is None:
        porcentaje_utilidad = actual["porcentaje_utilidad"]
    if stock_minimo is None:
        stock_minimo = actual["stock_minimo"]
    porcentaje_impuesto = _validar_porcentaje(porcentaje_impuesto, "Porcentaje de impuesto", 100)
    porcentaje_utilidad = _validar_porcentaje(porcentaje_utilidad, "Porcentaje de utilidad")
    stock_minimo = _entero(stock_minimo, "Stock mínimo", 0)
    if precio_venta is None or str(precio_venta).strip() == "":
        precio_venta = calcular_precio_sugerido(precio_compra, porcentaje_impuesto, porcentaje_utilidad)
    else:
        precio_venta = _numero_no_negativo(precio_venta, "Precio de venta")
    fecha_vencimiento = _validar_fecha(fecha_vencimiento)
    # Solo se exige categoría/proveedor activos si cambiaron respecto al actual.
    if id_categoria != actual["id_categoria"]:
        _validar_categoria_activa(conexion, id_categoria)
    if id_proveedor:
        if id_proveedor != actual["id_proveedor"]:
            _validar_proveedor_activo(conexion, id_proveedor)
    else:
        id_proveedor = None
    try:
        conexion.execute(
            """UPDATE productos
               SET nombre = ?, id_categoria = ?, precio_compra = ?, precio_venta = ?,
                   id_proveedor = ?, fecha_vencimiento = ?,
                   porcentaje_impuesto = ?, porcentaje_utilidad = ?, stock_minimo = ?
               WHERE id_producto = ?""",
            (nombre, id_categoria, precio_compra, precio_venta,
             id_proveedor, fecha_vencimiento,
             porcentaje_impuesto, porcentaje_utilidad, stock_minimo, id_producto),
        )
    except sqlite3.IntegrityError:
        raise ErrorNegocio("Ya existe otro producto con ese nombre en la categoría seleccionada.")


def desactivar_producto(conexion, id_producto):
    """Borrado lógico: se conserva en el historial pero no se puede comprar ni vender."""
    obtener_producto(conexion, id_producto)
    conexion.execute(
        "UPDATE productos SET estado = ? WHERE id_producto = ?",
        (ESTADO_INACTIVO, id_producto),
    )


def reactivar_producto(conexion, id_producto):
    producto = obtener_producto(conexion, id_producto)
    _validar_categoria_activa(conexion, producto["id_categoria"])
    conexion.execute(
        "UPDATE productos SET estado = ? WHERE id_producto = ?",
        (ESTADO_ACTIVO, id_producto),
    )


# ===========================================================================
# PROVEEDORES
# ===========================================================================
def _validar_telefono(valor, obligatorio):
    """Teléfono de 8 a 12 dígitos numéricos (obligatorio u opcional)."""
    valor = (valor or "").strip()
    if not valor:
        if obligatorio:
            raise ErrorNegocio("El campo 'Teléfono' es obligatorio.")
        return None
    if not re.fullmatch(r"[0-9]{8,12}", valor):
        raise ErrorNegocio("El teléfono debe contener entre 8 y 12 dígitos numéricos.")
    return valor


def _validar_correo(valor):
    """Correo opcional con formato válido (usuario@dominio.ext)."""
    valor = (valor or "").strip()
    if not valor:
        return None
    if len(valor) > 100:
        raise ErrorNegocio("El correo no puede superar 100 caracteres.")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", valor):
        raise ErrorNegocio("El correo electrónico no tiene un formato válido.")
    return valor


def _validar_datos_proveedor(nombre, telefono, nombre_contacto, correo, direccion):
    return (
        _texto_obligatorio(nombre, "Nombre del proveedor", 100),
        _validar_telefono(telefono, obligatorio=True),
        _texto_opcional(nombre_contacto, "Nombre del contacto", 100),
        _validar_correo(correo),
        _texto_opcional(direccion, "Dirección", 200),
    )


def _existe_proveedor_con_nombre(conexion, nombre, excluir_id=None):
    fila = conexion.execute(
        "SELECT 1 FROM proveedores WHERE nombre = ? COLLATE NOCASE AND id_proveedor IS NOT ?",
        (nombre, excluir_id),
    ).fetchone()
    return fila is not None


def crear_proveedor(conexion, nombre, telefono, nombre_contacto=None, correo=None, direccion=None):
    nombre, telefono, nombre_contacto, correo, direccion = _validar_datos_proveedor(
        nombre, telefono, nombre_contacto, correo, direccion)
    if _existe_proveedor_con_nombre(conexion, nombre):
        raise ErrorNegocio("Ya existe un proveedor con ese nombre.")
    return conexion.execute(
        """INSERT INTO proveedores (nombre, nombre_contacto, telefono, correo, direccion)
           VALUES (?, ?, ?, ?, ?)""",
        (nombre, nombre_contacto, telefono, correo, direccion),
    ).lastrowid


def listar_proveedores(conexion, estado=ESTADO_ACTIVO, busqueda=""):
    """Busca por nombre, contacto o teléfono. estado: 'activo' (defecto), 'inactivo' o None."""
    filtro, params = _filtro_estado(estado)
    patron = f"%{busqueda.strip()}%"
    sql = ("SELECT * FROM proveedores "
           "WHERE (nombre LIKE ? OR nombre_contacto LIKE ? OR telefono LIKE ?)"
           + filtro + " ORDER BY nombre")
    return conexion.execute(sql, [patron, patron, patron] + params).fetchall()


def obtener_proveedor(conexion, id_proveedor):
    fila = conexion.execute(
        "SELECT * FROM proveedores WHERE id_proveedor = ?", (id_proveedor,)
    ).fetchone()
    if fila is None:
        raise ErrorNegocio("El proveedor no existe.")
    return fila


def modificar_proveedor(conexion, id_proveedor, nombre, telefono,
                        nombre_contacto=None, correo=None, direccion=None):
    obtener_proveedor(conexion, id_proveedor)
    nombre, telefono, nombre_contacto, correo, direccion = _validar_datos_proveedor(
        nombre, telefono, nombre_contacto, correo, direccion)
    if _existe_proveedor_con_nombre(conexion, nombre, excluir_id=id_proveedor):
        raise ErrorNegocio("Ya existe otro proveedor con ese nombre.")
    # Solo cambia el proveedor; las compras anteriores conservan su referencia (id).
    conexion.execute(
        """UPDATE proveedores
           SET nombre = ?, nombre_contacto = ?, telefono = ?, correo = ?, direccion = ?
           WHERE id_proveedor = ?""",
        (nombre, nombre_contacto, telefono, correo, direccion, id_proveedor),
    )


def desactivar_proveedor(conexion, id_proveedor):
    """Borrado lógico: se conserva en el historial pero no se usa en nuevas operaciones."""
    obtener_proveedor(conexion, id_proveedor)
    conexion.execute("UPDATE proveedores SET estado = ? WHERE id_proveedor = ?",
                     (ESTADO_INACTIVO, id_proveedor))


def reactivar_proveedor(conexion, id_proveedor):
    obtener_proveedor(conexion, id_proveedor)
    conexion.execute("UPDATE proveedores SET estado = ? WHERE id_proveedor = ?",
                     (ESTADO_ACTIVO, id_proveedor))


# ===========================================================================
# CLIENTES
# ===========================================================================
def _validar_datos_cliente(nombre, telefono, correo, direccion):
    return (
        _texto_obligatorio(nombre, "Nombre completo", 100),
        _validar_telefono(telefono, obligatorio=False),
        _validar_correo(correo),
        _texto_opcional(direccion, "Dirección", 200),
    )


def _existe_cliente_duplicado(conexion, nombre, telefono, excluir_id=None):
    """Mismo nombre y mismo teléfono (incluye el caso de ambos sin teléfono)."""
    fila = conexion.execute(
        """SELECT 1 FROM clientes
           WHERE nombre = ? COLLATE NOCASE AND telefono IS ? AND id_cliente IS NOT ?""",
        (nombre, telefono, excluir_id),
    ).fetchone()
    return fila is not None


def crear_cliente(conexion, nombre, telefono=None, correo=None, direccion=None):
    nombre, telefono, correo, direccion = _validar_datos_cliente(nombre, telefono, correo, direccion)
    if _existe_cliente_duplicado(conexion, nombre, telefono):
        raise ErrorNegocio("Ya existe un cliente con el mismo nombre y teléfono.")
    return conexion.execute(
        "INSERT INTO clientes (nombre, telefono, correo, direccion) VALUES (?, ?, ?, ?)",
        (nombre, telefono, correo, direccion),
    ).lastrowid


def listar_clientes(conexion, estado=ESTADO_ACTIVO, busqueda=""):
    """Busca por nombre o teléfono. estado: 'activo' (defecto), 'inactivo' o None."""
    filtro, params = _filtro_estado(estado)
    patron = f"%{busqueda.strip()}%"
    sql = ("SELECT * FROM clientes WHERE (nombre LIKE ? OR telefono LIKE ?)"
           + filtro + " ORDER BY nombre")
    return conexion.execute(sql, [patron, patron] + params).fetchall()


def obtener_cliente(conexion, id_cliente):
    fila = conexion.execute(
        "SELECT * FROM clientes WHERE id_cliente = ?", (id_cliente,)
    ).fetchone()
    if fila is None:
        raise ErrorNegocio("El cliente no existe.")
    return fila


def modificar_cliente(conexion, id_cliente, nombre, telefono=None, correo=None, direccion=None):
    obtener_cliente(conexion, id_cliente)
    nombre, telefono, correo, direccion = _validar_datos_cliente(nombre, telefono, correo, direccion)
    if _existe_cliente_duplicado(conexion, nombre, telefono, excluir_id=id_cliente):
        raise ErrorNegocio("Ya existe otro cliente con el mismo nombre y teléfono.")
    conexion.execute(
        "UPDATE clientes SET nombre = ?, telefono = ?, correo = ?, direccion = ? WHERE id_cliente = ?",
        (nombre, telefono, correo, direccion, id_cliente),
    )


def desactivar_cliente(conexion, id_cliente):
    """Borrado lógico: las ventas anteriores se conservan; las nuevas pueden ser a cliente general."""
    obtener_cliente(conexion, id_cliente)
    conexion.execute("UPDATE clientes SET estado = ? WHERE id_cliente = ?",
                     (ESTADO_INACTIVO, id_cliente))


def reactivar_cliente(conexion, id_cliente):
    obtener_cliente(conexion, id_cliente)
    conexion.execute("UPDATE clientes SET estado = ? WHERE id_cliente = ?",
                     (ESTADO_ACTIVO, id_cliente))


# ===========================================================================
# VENTAS Y COMPRAS (transaccionales)
# ===========================================================================
def _consolidar_items(items, con_precio):
    """
    Valida cantidades y une líneas repetidas del mismo producto.
    items de venta : [(id_producto, cantidad), ...]
    items de compra: [(id_producto, cantidad, precio_unitario), ...]
    Devuelve dict {id_producto: {"cantidad": n, "precio": p}} (precio solo en compras).
    Si un producto se repite con distinto precio en una compra, se mantienen
    líneas separadas usando la clave (id_producto, precio).
    """
    if not items:
        raise ErrorNegocio("Debe incluir al menos un producto.")
    consolidado = {}
    for item in items:
        id_producto = item[0]
        cantidad = _entero(item[1], "Cantidad", 1)
        if con_precio:
            precio = _numero_no_negativo(item[2], "Precio unitario")
            clave = (id_producto, precio)
        else:
            precio = None
            clave = (id_producto, None)
        if clave in consolidado:
            consolidado[clave]["cantidad"] += cantidad
        else:
            consolidado[clave] = {"id_producto": id_producto, "cantidad": cantidad, "precio": precio}
    return list(consolidado.values())


def _validar_metodo_pago(metodo_pago):
    """Devuelve el método de pago con su escritura oficial (acepta cualquier mayúscula/minúscula)."""
    texto = (metodo_pago or METODO_PAGO_POR_DEFECTO).strip().lower()
    for oficial in METODOS_PAGO:
        if oficial.lower() == texto:
            return oficial
    raise ErrorNegocio("Método de pago inválido. Opciones: " + ", ".join(METODOS_PAGO) + ".")


def registrar_venta(conexion, items, id_cliente=None, metodo_pago=METODO_PAGO_POR_DEFECTO):
    """
    Registra una venta y descuenta el stock en UNA sola transacción.

    items: [(id_producto, cantidad), ...]
    id_cliente: None => venta a cliente general.
    metodo_pago: 'Efectivo' (por defecto), 'Tarjeta' o 'SINPE'.
    El precio unitario SIEMPRE se lee de la BD (productos.precio_venta).
    Devuelve el id de la venta. Ante cualquier error hace ROLLBACK y lanza ErrorNegocio.
    """
    metodo_pago = _validar_metodo_pago(metodo_pago)
    lineas = _consolidar_items(items, con_precio=False)
    try:
        with transaccion(conexion):
            if id_cliente:
                cliente = conexion.execute(
                    "SELECT estado FROM clientes WHERE id_cliente = ?", (id_cliente,)
                ).fetchone()
                if cliente is None:
                    raise ErrorNegocio("El cliente no existe.")
                if cliente["estado"] != ESTADO_ACTIVO:
                    raise ErrorNegocio("El cliente seleccionado está inactivo.")
            else:
                id_cliente = None

            # Un producto repetido en varias líneas debe validarse por su cantidad total.
            detalles = []
            for linea in lineas:
                producto = conexion.execute(
                    "SELECT nombre, precio_venta, estado FROM productos WHERE id_producto = ?",
                    (linea["id_producto"],),
                ).fetchone()
                if producto is None:
                    raise ErrorNegocio("Un producto de la venta no existe.")
                if producto["estado"] != ESTADO_ACTIVO:
                    raise ErrorNegocio(f"El producto '{producto['nombre']}' está inactivo.")
                precio = producto["precio_venta"]
                detalles.append((linea["id_producto"], producto["nombre"], linea["cantidad"],
                                 precio, round(precio * linea["cantidad"], 2)))

            total = round(sum(d[4] for d in detalles), 2)
            id_venta = conexion.execute(
                "INSERT INTO ventas (id_cliente, total, metodo_pago) VALUES (?, ?, ?)",
                (id_cliente, total, metodo_pago)
            ).lastrowid

            for id_producto, nombre, cantidad, precio, subtotal in detalles:
                # UPDATE condicional: solo descuenta si hay stock suficiente (evita negativos).
                cursor = conexion.execute(
                    "UPDATE productos SET existencia = existencia - ? "
                    "WHERE id_producto = ? AND existencia >= ?",
                    (cantidad, id_producto, cantidad),
                )
                if cursor.rowcount == 0:
                    disponible = conexion.execute(
                        "SELECT existencia FROM productos WHERE id_producto = ?", (id_producto,)
                    ).fetchone()["existencia"]
                    raise ErrorNegocio(
                        f"Stock insuficiente para '{nombre}'. Disponible: {disponible}, solicitado: {cantidad}."
                    )
                conexion.execute(
                    """INSERT INTO detalle_ventas (id_venta, id_producto, cantidad, precio_unitario, subtotal)
                       VALUES (?, ?, ?, ?, ?)""",
                    (id_venta, id_producto, cantidad, precio, subtotal),
                )
        return id_venta
    except sqlite3.Error as error:
        # transaccion() ya hizo ROLLBACK; traducimos el error técnico.
        raise ErrorNegocio(f"No se pudo registrar la venta: {error}")


def _validar_nuevos_precios_venta(nuevos_precios, ids_en_compra):
    """
    (Formato anterior, aún aceptado.) Valida {id_producto: nuevo_precio_venta}.
    Los valores vacíos/None se ignoran (significa "no cambiar el precio").
    Solo se acepta actualizar productos que forman parte de la compra.
    """
    validados = {}
    for id_producto, precio in (nuevos_precios or {}).items():
        if precio is None or str(precio).strip() == "":
            continue
        if id_producto not in ids_en_compra:
            raise ErrorNegocio("Solo se puede actualizar el precio de venta de productos incluidos en la compra.")
        validados[id_producto] = _numero_no_negativo(precio, "Nuevo precio de venta")
    return validados


# Orden de los campos cuando una línea de compra llega como tupla en vez de diccionario.
CAMPOS_ITEM_COMPRA = ("id_producto", "cantidad", "precio_compra", "nuevo_precio_venta",
                      "nuevo_porcentaje_impuesto", "nuevo_porcentaje_utilidad")

# (clave en la línea, columna de productos, validador) de los datos opcionales de cada línea.
_ACTUALIZACIONES_PRODUCTO = (
    ("nuevo_precio_venta", "precio_venta",
     lambda v: _numero_no_negativo(v, "Nuevo precio de venta")),
    ("nuevo_porcentaje_impuesto", "porcentaje_impuesto",
     lambda v: _validar_porcentaje(v, "Nuevo porcentaje de impuesto", 100)),
    ("nuevo_porcentaje_utilidad", "porcentaje_utilidad",
     lambda v: _validar_porcentaje(v, "Nuevo porcentaje de utilidad")),
)


def _normalizar_items_compra(items):
    """
    Valida las líneas de una compra y devuelve (lineas, actualizaciones):
      lineas          -> [{"id_producto", "cantidad", "precio"}], uniendo las que repiten
                         producto Y precio.
      actualizaciones -> {id_producto: {columna: valor}}: siempre el nuevo costo (precio_compra)
                         y, además, solo lo opcional que el usuario envió (precio de venta,
                         % de impuesto y % de utilidad). Si un producto trae varias líneas,
                         vale el valor de la última.
    """
    if not items:
        raise ErrorNegocio("Debe incluir al menos un producto.")
    consolidado = {}
    actualizaciones = {}
    for item in items:
        if isinstance(item, dict):
            datos = item
        else:
            if not 3 <= len(item) <= len(CAMPOS_ITEM_COMPRA):
                raise ErrorNegocio("Una línea de la compra tiene un formato inválido.")
            datos = dict(zip(CAMPOS_ITEM_COMPRA, item))
        for obligatorio in ("id_producto", "cantidad", "precio_compra"):
            if obligatorio not in datos:
                raise ErrorNegocio(f"Falta el dato '{obligatorio}' en una línea de la compra.")

        id_producto = datos["id_producto"]
        cantidad = _entero(datos["cantidad"], "Cantidad", 1)
        precio = _numero_no_negativo(datos["precio_compra"], "Precio unitario")
        clave = (id_producto, precio)
        if clave in consolidado:
            consolidado[clave]["cantidad"] += cantidad
        else:
            consolidado[clave] = {"id_producto": id_producto, "cantidad": cantidad, "precio": precio}
        # El último costo pagado queda como costo actual del producto (productos.precio_compra).
        actualizaciones.setdefault(id_producto, {})["precio_compra"] = precio

        for campo, columna, validar in _ACTUALIZACIONES_PRODUCTO:
            valor = datos.get(campo)
            if valor is None or str(valor).strip() == "":
                continue   # vacío = no cambiar
            actualizaciones.setdefault(id_producto, {})[columna] = validar(valor)
    return list(consolidado.values()), actualizaciones


def registrar_compra(conexion, id_proveedor, items, nuevos_precios_venta=None):
    """
    Registra una compra y aumenta el stock en UNA sola transacción.

    items: lista de diccionarios con las claves
        id_producto, cantidad, precio_compra            (obligatorias)
        nuevo_precio_venta, nuevo_porcentaje_impuesto,
        nuevo_porcentaje_utilidad                       (opcionales; None o "" = no cambiar)
      (también se aceptan tuplas en ese mismo orden, de 3 a 6 elementos).
    precio_compra es el precio REAL pagado al proveedor (ingresado a mano).
    En la misma transacción se actualiza la tabla productos con: el stock, el nuevo costo
    (precio_compra) y lo opcional que se envíe (precio de venta final y % de impuesto/utilidad).
    Si algo falla, nada cambia.
    nuevos_precios_venta: {id_producto: precio} del formato anterior; se sigue aceptando y
    cualquier dato indicado en las líneas tiene prioridad sobre él.
    Devuelve el id de la compra. Ante cualquier error hace ROLLBACK y lanza ErrorNegocio.
    """
    if not id_proveedor:
        raise ErrorNegocio("Debe seleccionar un proveedor.")
    lineas, actualizaciones = _normalizar_items_compra(items)
    heredados = _validar_nuevos_precios_venta(
        nuevos_precios_venta, {linea["id_producto"] for linea in lineas})
    for id_producto, precio in heredados.items():
        actualizaciones.setdefault(id_producto, {}).setdefault("precio_venta", precio)
    try:
        with transaccion(conexion):
            _validar_proveedor_activo(conexion, id_proveedor)

            detalles = []
            for linea in lineas:
                producto = conexion.execute(
                    "SELECT nombre, estado FROM productos WHERE id_producto = ?",
                    (linea["id_producto"],),
                ).fetchone()
                if producto is None:
                    raise ErrorNegocio("Un producto de la compra no existe.")
                if producto["estado"] != ESTADO_ACTIVO:
                    raise ErrorNegocio(f"El producto '{producto['nombre']}' está inactivo.")
                detalles.append((linea["id_producto"], linea["cantidad"], linea["precio"],
                                 round(linea["precio"] * linea["cantidad"], 2)))

            total = round(sum(d[3] for d in detalles), 2)
            id_compra = conexion.execute(
                "INSERT INTO compras (id_proveedor, total) VALUES (?, ?)", (id_proveedor, total)
            ).lastrowid

            for id_producto, cantidad, precio, subtotal in detalles:
                conexion.execute(
                    """INSERT INTO detalle_compras (id_compra, id_producto, cantidad, precio_unitario, subtotal)
                       VALUES (?, ?, ?, ?, ?)""",
                    (id_compra, id_producto, cantidad, precio, subtotal),
                )
                conexion.execute(
                    "UPDATE productos SET existencia = existencia + ? WHERE id_producto = ?",
                    (cantidad, id_producto),
                )

            # Costo nuevo y, si se enviaron, precio de venta e impuesto/utilidad nuevos.
            # Los nombres de columna salen de _ACTUALIZACIONES_PRODUCTO, nunca del usuario.
            for id_producto, cambios in actualizaciones.items():
                if not cambios:
                    continue
                asignaciones = ", ".join(f"{columna} = ?" for columna in cambios)
                conexion.execute(
                    f"UPDATE productos SET {asignaciones} WHERE id_producto = ?",
                    list(cambios.values()) + [id_producto],
                )
        return id_compra
    except sqlite3.Error as error:
        raise ErrorNegocio(f"No se pudo registrar la compra: {error}")


# ===========================================================================
# REPORTES: cierre de caja y alertas de stock
# ===========================================================================
def obtener_cierre_caja_diario(conexion, fecha=None):
    """
    Resumen de caja de un día (por defecto HOY, hora local). Las transacciones
    anuladas NO cuentan. Devuelve un diccionario:
        {"fecha": "AAAA-MM-DD",
         "total_vendido": float, "cantidad_ventas": int,
         "total_comprado": float, "cantidad_compras": int,
         "ventas_por_metodo": {"Efectivo": float, "Tarjeta": float, "SINPE": float}}
    fecha: opcional, formato AAAA-MM-DD (para consultar un cierre anterior).
    """
    fecha = _validar_fecha(fecha, "fecha del cierre") or conexion.execute(
        "SELECT date('now', 'localtime')").fetchone()[0]

    por_metodo = {metodo: 0.0 for metodo in METODOS_PAGO}
    total_vendido = 0.0
    cantidad_ventas = 0
    for fila in conexion.execute(
            """SELECT metodo_pago, SUM(total) AS suma, COUNT(*) AS cantidad
               FROM ventas WHERE date(fecha_hora) = ? AND anulada = 0
               GROUP BY metodo_pago""", (fecha,)).fetchall():
        por_metodo[fila["metodo_pago"]] = round(fila["suma"], 2)
        total_vendido += fila["suma"]
        cantidad_ventas += fila["cantidad"]

    compras = conexion.execute(
        """SELECT COALESCE(SUM(total), 0) AS suma, COUNT(*) AS cantidad
           FROM compras WHERE date(fecha_hora) = ? AND anulada = 0""", (fecha,)).fetchone()

    return {
        "fecha": fecha,
        "total_vendido": round(total_vendido, 2),
        "cantidad_ventas": cantidad_ventas,
        "total_comprado": round(float(compras["suma"]), 2),
        "cantidad_compras": compras["cantidad"],
        "ventas_por_metodo": por_metodo,
    }


def listar_productos_stock_bajo(conexion):
    """
    Productos ACTIVOS cuya existencia es menor o igual a su stock mínimo
    (los más críticos primero). Incluye categoría y proveedor habitual.
    """
    return conexion.execute(
        """SELECT p.*, c.nombre AS categoria, pr.nombre AS proveedor
           FROM productos p
           JOIN categorias c ON c.id_categoria = p.id_categoria
           LEFT JOIN proveedores pr ON pr.id_proveedor = p.id_proveedor
           WHERE p.estado = 'activo' AND p.existencia <= p.stock_minimo
           ORDER BY p.existencia ASC, p.nombre""").fetchall()


# ===========================================================================
# TICKET DE VENTA
# ===========================================================================
def _linea_dos_columnas(izquierda, derecha, ancho=ANCHO_TICKET):
    """'izquierda ........ derecha' con la derecha pegada al borde del ticket."""
    espacio = max(1, ancho - len(izquierda) - len(derecha))
    return izquierda + " " * espacio + derecha


def generar_ticket_venta(conexion, id_venta, carpeta=None):
    """
    Genera un ticket de caja registradora (.txt, UTF-8) de la venta indicada y devuelve
    la ruta del archivo creado: ticket_venta_000123.txt.

    carpeta: dónde guardarlo (por defecto, la carpeta del proyecto). Si se vuelve a generar
    el ticket de la misma venta, el archivo se sobrescribe.
    Los datos salen de la base de datos (precios y cantidades registrados en la venta);
    una venta anulada se imprime marcada como ANULADA.
    """
    venta = conexion.execute(
        """SELECT v.id_venta, v.fecha_hora, v.total, v.anulada, v.metodo_pago,
                  COALESCE(c.nombre, 'Cliente General') AS cliente
           FROM ventas v LEFT JOIN clientes c ON c.id_cliente = v.id_cliente
           WHERE v.id_venta = ?""", (id_venta,)).fetchone()
    if venta is None:
        raise ErrorNegocio("La venta no existe.")
    detalles = obtener_detalle_venta(conexion, id_venta)

    ancho = ANCHO_TICKET
    doble = "=" * ancho
    simple = "-" * ancho
    try:
        fecha = datetime.strptime(venta["fecha_hora"], "%Y-%m-%d %H:%M:%S").strftime("%d/%m/%Y %H:%M")
    except ValueError:
        fecha = venta["fecha_hora"]

    lineas = [doble]
    lineas += [linea.center(ancho) for linea in textwrap.wrap(NOMBRE_NEGOCIO.upper(), ancho)]
    lineas += ["TICKET DE VENTA".center(ancho), doble,
               f"Ticket N.º: {venta['id_venta']:06d}",
               f"Fecha: {fecha}",
               f"Cliente: {venta['cliente']}"[:ancho]]
    if venta["anulada"]:
        lineas += ["", "*** VENTA ANULADA ***".center(ancho)]
    lineas += [simple, _linea_dos_columnas("DETALLE", "SUBTOTAL"), simple]
    for d in detalles:
        lineas += textwrap.wrap(d["producto"], ancho) or [""]
        lineas.append(_linea_dos_columnas(
            f"  {d['cantidad']} x {d['precio_unitario']:,.2f}", f"{d['subtotal']:,.2f}"))
    lineas += [simple,
               _linea_dos_columnas("TOTAL:", f"₡{venta['total']:,.2f}"),
               _linea_dos_columnas("Método de pago:", venta["metodo_pago"]),
               doble, "¡Gracias por su compra!".center(ancho), doble]

    carpeta = carpeta or CARPETA_PROYECTO
    try:
        os.makedirs(carpeta, exist_ok=True)
        ruta = os.path.join(carpeta, f"ticket_venta_{venta['id_venta']:06d}.txt")
        with open(ruta, "w", encoding="utf-8", newline="\n") as archivo:
            archivo.write("\n".join(lineas) + "\n")
    except OSError as error:
        raise ErrorNegocio(f"No se pudo guardar el ticket: {error}")
    return ruta


# ===========================================================================
# ADMINISTRACIÓN: historial, edición y anulación de transacciones (CRUD 5)
# ===========================================================================
# Contraseña maestra del módulo Admin. Cámbiela aquí (no se muestra en la interfaz).
CLAVE_MAESTRA = "admin123"

# Configuración de cada tipo de transacción. Los nombres de tablas/columnas son
# constantes del programa (nunca texto del usuario), por eso es seguro usarlos en el SQL.
_TIPOS_TRANSACCION = {
    "venta": {"padre": "ventas", "id_padre": "id_venta",
              "detalle": "detalle_ventas", "id_detalle": "id_detalle_venta",
              "signo_stock": -1},   # editar/crear una venta RESTA stock
    "compra": {"padre": "compras", "id_padre": "id_compra",
               "detalle": "detalle_compras", "id_detalle": "id_detalle_compra",
               "signo_stock": +1},  # editar/crear una compra SUMA stock
}


def verificar_clave_admin(clave):
    """True si la contraseña ingresada coincide con la contraseña maestra."""
    return hmac.compare_digest((clave or "").encode("utf-8"), CLAVE_MAESTRA.encode("utf-8"))


def _ajustar_stock(conexion, id_producto, cambio):
    """
    Suma `cambio` (positivo o negativo) a la existencia del producto.
    Rechaza la operación si el stock resultante quedaría negativo.
    """
    if cambio == 0:
        return
    cursor = conexion.execute(
        "UPDATE productos SET existencia = existencia + ? "
        "WHERE id_producto = ? AND existencia + ? >= 0",
        (cambio, id_producto, cambio),
    )
    if cursor.rowcount == 0:
        fila = conexion.execute(
            "SELECT nombre, existencia FROM productos WHERE id_producto = ?", (id_producto,)
        ).fetchone()
        raise ErrorNegocio(
            f"La operación dejaría el stock de '{fila['nombre']}' en negativo "
            f"(stock actual: {fila['existencia']}, ajuste: {cambio:+d})."
        )


def listar_ventas(conexion, busqueda="", incluir_anuladas=True):
    """
    Historial de ventas (más recientes primero). Busca por N.º, fecha o cliente.
    Las anuladas se incluyen por defecto (columna `anulada` = 1) para auditoría;
    use incluir_anuladas=False en reportes o consultas que deban ignorarlas.
    """
    patron = f"%{busqueda.strip()}%"
    filtro_anuladas = "" if incluir_anuladas else " AND v.anulada = 0"
    return conexion.execute(
        """SELECT v.id_venta, v.fecha_hora, v.total, v.id_cliente, v.anulada, v.metodo_pago,
                  COALESCE(c.nombre, 'Cliente General') AS cliente
           FROM ventas v LEFT JOIN clientes c ON c.id_cliente = v.id_cliente
           WHERE (CAST(v.id_venta AS TEXT) LIKE ? OR v.fecha_hora LIKE ?
                  OR COALESCE(c.nombre, 'Cliente General') LIKE ?)"""
        + filtro_anuladas +
        """ ORDER BY v.fecha_hora DESC, v.id_venta DESC""",
        (patron, patron, patron),
    ).fetchall()


def listar_compras(conexion, busqueda="", incluir_anuladas=True):
    """
    Historial de compras (más recientes primero). Busca por N.º, fecha o proveedor.
    Las anuladas se incluyen por defecto (columna `anulada` = 1) para auditoría;
    use incluir_anuladas=False en reportes o consultas que deban ignorarlas.
    """
    patron = f"%{busqueda.strip()}%"
    filtro_anuladas = "" if incluir_anuladas else " AND c.anulada = 0"
    return conexion.execute(
        """SELECT c.id_compra, c.fecha_hora, c.total, c.id_proveedor, c.anulada,
                  p.nombre AS proveedor
           FROM compras c JOIN proveedores p ON p.id_proveedor = c.id_proveedor
           WHERE (CAST(c.id_compra AS TEXT) LIKE ? OR c.fecha_hora LIKE ? OR p.nombre LIKE ?)"""
        + filtro_anuladas +
        """ ORDER BY c.fecha_hora DESC, c.id_compra DESC""",
        (patron, patron, patron),
    ).fetchall()


def obtener_detalle_venta(conexion, id_venta):
    return conexion.execute(
        """SELECT d.id_detalle_venta, d.id_producto, p.nombre AS producto,
                  d.cantidad, d.precio_unitario, d.subtotal
           FROM detalle_ventas d JOIN productos p ON p.id_producto = d.id_producto
           WHERE d.id_venta = ? ORDER BY d.id_detalle_venta""",
        (id_venta,),
    ).fetchall()


def obtener_detalle_compra(conexion, id_compra):
    return conexion.execute(
        """SELECT d.id_detalle_compra, d.id_producto, p.nombre AS producto,
                  d.cantidad, d.precio_unitario, d.subtotal
           FROM detalle_compras d JOIN productos p ON p.id_producto = d.id_producto
           WHERE d.id_compra = ? ORDER BY d.id_detalle_compra""",
        (id_compra,),
    ).fetchall()


def _exigir_transaccion_vigente(conexion, cfg, tipo, id_transaccion):
    """La transacción debe existir y no estar anulada."""
    fila = conexion.execute(
        f"SELECT anulada FROM {cfg['padre']} WHERE {cfg['id_padre']} = ?", (id_transaccion,)
    ).fetchone()
    if fila is None:
        raise ErrorNegocio(f"La {tipo} no existe.")
    if fila["anulada"]:
        raise ErrorNegocio(f"La {tipo} N.º {id_transaccion} ya está anulada.")


def _anular_transaccion(conexion, tipo, id_transaccion):
    cfg = _TIPOS_TRANSACCION[tipo]
    try:
        with transaccion(conexion):
            _exigir_transaccion_vigente(conexion, cfg, tipo, id_transaccion)
            # Se suma por producto para no rechazar por un negativo transitorio.
            por_producto = {}
            for d in conexion.execute(
                    f"SELECT id_producto, cantidad FROM {cfg['detalle']} WHERE {cfg['id_padre']} = ?",
                    (id_transaccion,)).fetchall():
                por_producto[d["id_producto"]] = por_producto.get(d["id_producto"], 0) + d["cantidad"]
            # Revertir: una venta devuelve stock (+), una compra lo resta (-).
            for id_producto, cantidad in por_producto.items():
                _ajustar_stock(conexion, id_producto, -cfg["signo_stock"] * cantidad)
            # NO se borra nada: se marca como anulada y el registro queda para auditoría.
            conexion.execute(
                f"UPDATE {cfg['padre']} SET anulada = 1 WHERE {cfg['id_padre']} = ?",
                (id_transaccion,),
            )
    except sqlite3.Error as error:
        raise ErrorNegocio(f"No se pudo anular la {tipo}: {error}")


def eliminar_venta(conexion, id_venta):
    """
    ANULA la venta (anulada = 1, sin DELETE) y DEVUELVE el stock de sus productos.
    El registro y su detalle se conservan para auditoría. Transaccional.
    """
    _anular_transaccion(conexion, "venta", id_venta)


def eliminar_compra(conexion, id_compra):
    """
    ANULA la compra (anulada = 1, sin DELETE) y RESTA el stock que aportó.
    Se rechaza si el stock quedaría negativo. El registro se conserva para auditoría.
    """
    _anular_transaccion(conexion, "compra", id_compra)


def _editar_transaccion(conexion, tipo, id_transaccion, lineas):
    """
    lineas: [(id_detalle, cantidad_nueva, precio_nuevo), ...]
    Solo se modifican cantidades y precios; fecha e identificador no cambian.
    El stock se ajusta según la DIFERENCIA de cantidades y el total se recalcula.
    """
    cfg = _TIPOS_TRANSACCION[tipo]
    if not lineas:
        raise ErrorNegocio("No hay líneas para modificar.")
    # Validación de datos antes de abrir la transacción.
    validadas = [(id_detalle,
                  _entero(cantidad, "Cantidad", 1),
                  _numero_no_negativo(precio, "Precio unitario"))
                 for id_detalle, cantidad, precio in lineas]
    try:
        with transaccion(conexion):
            _exigir_transaccion_vigente(conexion, cfg, tipo, id_transaccion)
            actuales = {
                f[cfg["id_detalle"]]: f for f in conexion.execute(
                    f"SELECT * FROM {cfg['detalle']} WHERE {cfg['id_padre']} = ?",
                    (id_transaccion,)).fetchall()
            }
            cambios_stock = {}
            for id_detalle, cantidad, _precio in validadas:
                if id_detalle not in actuales:
                    raise ErrorNegocio("Una de las líneas no pertenece a esta transacción.")
                anterior = actuales[id_detalle]
                diferencia = cantidad - anterior["cantidad"]
                cambios_stock[anterior["id_producto"]] = (
                    cambios_stock.get(anterior["id_producto"], 0) + cfg["signo_stock"] * diferencia)
            for id_producto, cambio in cambios_stock.items():
                _ajustar_stock(conexion, id_producto, cambio)
            for id_detalle, cantidad, precio in validadas:
                conexion.execute(
                    f"UPDATE {cfg['detalle']} SET cantidad = ?, precio_unitario = ?, subtotal = ? "
                    f"WHERE {cfg['id_detalle']} = ?",
                    (cantidad, precio, round(cantidad * precio, 2), id_detalle),
                )
            conexion.execute(
                f"UPDATE {cfg['padre']} SET total = "
                f"(SELECT ROUND(SUM(subtotal), 2) FROM {cfg['detalle']} WHERE {cfg['id_padre']} = ?) "
                f"WHERE {cfg['id_padre']} = ?",
                (id_transaccion, id_transaccion),
            )
            return conexion.execute(
                f"SELECT total FROM {cfg['padre']} WHERE {cfg['id_padre']} = ?", (id_transaccion,)
            ).fetchone()["total"]
    except sqlite3.Error as error:
        raise ErrorNegocio(f"No se pudo modificar la {tipo}: {error}")


def editar_venta(conexion, id_venta, lineas):
    """Modifica cantidades/precios de una venta y ajusta el stock. Devuelve el nuevo total."""
    return _editar_transaccion(conexion, "venta", id_venta, lineas)


def editar_compra(conexion, id_compra, lineas):
    """Modifica cantidades/precios de una compra y ajusta el stock. Devuelve el nuevo total."""
    return _editar_transaccion(conexion, "compra", id_compra, lineas)