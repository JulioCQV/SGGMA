"""
ui/ui_productos.py
Módulo de Productos: formulario de registro/edición + tabla de listado.
Toda la lógica de negocio está en servicios.py; aquí solo hay interfaz.

- Precio de venta = costo + impuesto + utilidad (botón "Calcular"); se puede editar a mano.
- Las filas con existencia <= stock mínimo se pintan de rojo.
"""

import re
import sqlite3
import unicodedata
from datetime import date, datetime
import tkinter as tk
from tkinter import ttk, messagebox

import servicios
from servicios import ErrorNegocio
from ui import formato

FILTROS_ESTADO = {"Todos": None, "Activos": "activo", "Inactivos": "inactivo"}
FILTROS_ALERTA = ("Todas", "Inventario bajo", "Sin existencias", "Vencidos",
                  "Vencen en 30 días", "Revisar fecha")
TODAS_CATEGORIAS = "Todas las categorías"
SIN_PROVEEDOR = "(Sin proveedor)"

# Valores iniciales del formulario (los mismos que usa la base de datos).
IMPUESTO_INICIAL = f"{servicios.IMPUESTO_POR_DEFECTO:g}"
UTILIDAD_INICIAL = f"{servicios.UTILIDAD_POR_DEFECTO:g}"
STOCK_MINIMO_INICIAL = str(servicios.STOCK_MINIMO_POR_DEFECTO)


def abrir_ventana(padre, conexion):
    """Función que llama ui_principal.py; devuelve el Toplevel creado."""
    return VentanaProductos(padre, conexion)


def _formato_dinero(valor):
    return f"₡{valor:,.2f}"


def _solo_digitos(texto_nuevo):
    return bool(re.fullmatch(r"[0-9]{0,9}", texto_nuevo))


def _solo_decimal(texto_nuevo):
    """Permite dígitos y un separador decimal (punto o coma)."""
    return len(texto_nuevo) <= 16 and bool(re.fullmatch(r"[0-9]*(?:[.,][0-9]{0,2})?", texto_nuevo))


def _solo_fecha(texto_nuevo):
    """Fecha DD/MM/AAAA: solo dígitos y barras, máximo 10 caracteres."""
    return bool(re.fullmatch(r"[0-9/-]{0,10}", texto_nuevo))


def _decimal(texto):
    """Acepta la coma como separador decimal."""
    return texto.strip().replace(",", ".")


def _fecha_vencimiento(producto):
    valor = producto["fecha_vencimiento"]
    if not valor:
        return None
    try:
        return datetime.strptime(valor, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _alertas_producto(producto, hoy=None):
    hoy = hoy or date.today()
    alertas = []
    if producto["existencia"] == 0:
        alertas.append("Sin existencias")
    elif producto["existencia"] <= producto["stock_minimo"]:
        alertas.append("Inventario bajo")
    vencimiento = _fecha_vencimiento(producto)
    if producto["fecha_vencimiento"] and vencimiento is None:
        alertas.append("Revisar fecha")
    elif vencimiento:
        dias = (vencimiento - hoy).days
        if dias < 0:
            alertas.append("Vencido")
        elif dias == 0:
            alertas.append("Vence hoy")
        elif dias <= 30:
            alertas.append(f"Vence en {dias} día(s)")
    return alertas


def _filtrar_productos(productos, busqueda="", alerta="Todas", hoy=None):
    hoy = hoy or date.today()
    def normalizar(texto):
        return "".join(c for c in unicodedata.normalize("NFD", texto.casefold())
                       if not unicodedata.combining(c))
    palabras = normalizar(busqueda).split()
    resultado = []
    for p in productos:
        texto = normalizar(" ".join((p["nombre"], p["categoria"], p["proveedor"] or "")))
        if not all(palabra in texto for palabra in palabras):
            continue
        vencimiento = _fecha_vencimiento(p)
        coincide = {
            "Todas": True,
            "Inventario bajo": p["existencia"] <= p["stock_minimo"],
            "Sin existencias": p["existencia"] == 0,
            "Vencidos": vencimiento is not None and vencimiento < hoy,
            "Vencen en 30 días": vencimiento is not None and 0 <= (vencimiento-hoy).days <= 30,
            "Revisar fecha": bool(p["fecha_vencimiento"]) and vencimiento is None,
        }.get(alerta, False)
        if coincide:
            resultado.append(p)
    return resultado


def _clave_orden(producto, columna, hoy=None):
    campos = {"id": "id_producto", "p_compra": "precio_compra", "p_venta": "precio_venta",
              "stock_min": "stock_minimo"}
    if columna == "vencimiento":
        return _fecha_vencimiento(producto) or date.max
    if columna == "alertas":
        return " · ".join(_alertas_producto(producto, hoy))
    valor = producto[campos.get(columna, columna)]
    return (valor or "").casefold() if isinstance(valor, str) or valor is None else valor


def _construir_mapa(filas, campo_id, campo_nombre):
    """
    Devuelve {texto_visible: id}. Si dos filas tienen el mismo nombre,
    se agrega el ID al texto para distinguirlas.
    """
    nombres = [f[campo_nombre] for f in filas]
    mapa = {}
    for fila in filas:
        texto = fila[campo_nombre]
        if nombres.count(texto) > 1:
            texto = f"{texto} (#{fila[campo_id]})"
        mapa[texto] = fila[campo_id]
    return mapa


class VentanaProductos(tk.Toplevel):
    def __init__(self, padre, conexion):
        super().__init__(padre)
        self.conexion = conexion
        self.id_seleccionado = None       # None = modo "registrar"; número = modo "editar"
        self.filas_por_id = {}            # id_producto -> fila completa de listar_productos
        self.mapa_categorias = {}         # texto visible -> id_categoria
        self.mapa_proveedores = {}        # texto visible -> id_proveedor (o None)
        self._extra_categoria = None      # (id, nombre) de una categoría inactiva en edición
        self._extra_proveedor = None      # (id, nombre) de un proveedor inactivo en edición

        self.title("GeMMa | Productos e inventario")
        ancho = min(1180, self.winfo_screenwidth() - 60)
        alto = min(780, self.winfo_screenheight() - 100)
        self.geometry(f"{ancho}x{alto}")
        self.minsize(min(940, ancho), min(650, alto))
        self.orden_columna = "nombre"
        self.orden_descendente = False
        self.mapa_filtro_categorias = {TODAS_CATEGORIAS: None}

        self.var_nombre = tk.StringVar()
        self.var_categoria = tk.StringVar()
        self.var_precio_compra = tk.StringVar()
        self.var_impuesto = tk.StringVar(value=IMPUESTO_INICIAL)
        self.var_utilidad = tk.StringVar(value=UTILIDAD_INICIAL)
        self.var_precio_venta = tk.StringVar()
        self.var_existencia = tk.StringVar(value="0")
        self.var_stock_minimo = tk.StringVar(value=STOCK_MINIMO_INICIAL)
        self.var_proveedor = tk.StringVar(value=SIN_PROVEEDOR)
        self.var_vencimiento = tk.StringVar()
        self.var_busqueda = tk.StringVar()
        self.var_filtro = tk.StringVar(value="Activos")
        self.var_filtro_categoria = tk.StringVar(value=TODAS_CATEGORIAS)
        self.var_alerta = tk.StringVar(value="Todas")
        self.var_resumen = tk.StringVar()
        self.var_sugerido = tk.StringVar()

        ttk.Label(self, text="Productos e inventario", font=("Segoe UI", 18, "bold")).pack(
            anchor="w", padx=16, pady=(10, 0))
        self._crear_formulario()
        self._crear_tabla()
        self.recargar_combos()
        self.var_busqueda.trace_add("write", lambda *_: self.cargar_tabla())
        for variable in (self.var_precio_compra, self.var_impuesto, self.var_utilidad):
            variable.trace_add("write", lambda *_: self._actualizar_sugerido())
        self._actualizar_sugerido()
        self.cargar_tabla()
        self._datos_cargados = self._datos_formulario()
        self.protocol("WM_DELETE_WINDOW", self.cerrar)
        self.entrada_nombre.focus_set()

    # ------------------------------------------------------------------
    # Construcción de la interfaz
    # ------------------------------------------------------------------
    def _crear_formulario(self):
        self.marco_form = ttk.LabelFrame(self, text="Registrar producto", padding=12)
        self.marco_form.pack(fill="x", padx=12, pady=(12, 6))
        for columna in (1, 3):
            self.marco_form.columnconfigure(columna, weight=1, uniform="campos")

        validar_decimal = (self.register(_solo_decimal), "%P")
        validar_entero = (self.register(_solo_digitos), "%P")

        # Fila 0: nombre y categoría
        ttk.Label(self.marco_form, text="Nombre (*):").grid(row=0, column=0, sticky="w", pady=4)
        self.entrada_nombre = ttk.Entry(self.marco_form, textvariable=self.var_nombre)
        self.entrada_nombre.grid(row=0, column=1, sticky="ew", padx=(8, 16), pady=4)

        ttk.Label(self.marco_form, text="Categoría (*):").grid(row=0, column=2, sticky="w", pady=4)
        self.combo_categoria = ttk.Combobox(self.marco_form, textvariable=self.var_categoria,
                                            state="readonly", postcommand=self.recargar_combos)
        self.combo_categoria.grid(row=0, column=3, sticky="ew", padx=(8, 0), pady=4)

        # Fila 1: precio de compra y precio de venta (con botón Calcular)
        ttk.Label(self.marco_form, text="Costo unitario (₡) (*):").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(self.marco_form, textvariable=self.var_precio_compra, validate="key",
                  validatecommand=validar_decimal).grid(row=1, column=1, sticky="ew", padx=(8, 16), pady=4)

        ttk.Label(self.marco_form, text="Precio venta (₡):").grid(row=1, column=2, sticky="w", pady=4)
        marco_precio = ttk.Frame(self.marco_form)
        marco_precio.grid(row=1, column=3, sticky="ew", padx=(8, 0), pady=4)
        marco_precio.columnconfigure(0, weight=1)
        ttk.Entry(marco_precio, textvariable=self.var_precio_venta, validate="key",
                  validatecommand=validar_decimal).grid(row=0, column=0, sticky="ew")
        ttk.Button(marco_precio, text="Calcular", width=9, command=self.calcular_precio).grid(
            row=0, column=1, padx=(6, 0))

        # Fila 2: impuesto y utilidad
        ttk.Label(self.marco_form, text="% Impuesto:").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(self.marco_form, textvariable=self.var_impuesto, validate="key",
                  validatecommand=validar_decimal).grid(row=2, column=1, sticky="ew", padx=(8, 16), pady=4)

        ttk.Label(self.marco_form, text="% Utilidad:").grid(row=2, column=2, sticky="w", pady=4)
        ttk.Entry(self.marco_form, textvariable=self.var_utilidad, validate="key",
                  validatecommand=validar_decimal).grid(row=2, column=3, sticky="ew", padx=(8, 0), pady=4)

        # Fila 3: existencia y stock mínimo
        self.etiqueta_existencia = ttk.Label(self.marco_form, text="Existencia inicial (*):")
        self.etiqueta_existencia.grid(row=3, column=0, sticky="w", pady=4)
        self.entrada_existencia = ttk.Entry(self.marco_form, textvariable=self.var_existencia,
                                            validate="key", validatecommand=validar_entero)
        self.entrada_existencia.grid(row=3, column=1, sticky="ew", padx=(8, 16), pady=4)

        ttk.Label(self.marco_form, text="Stock mínimo:").grid(row=3, column=2, sticky="w", pady=4)
        ttk.Entry(self.marco_form, textvariable=self.var_stock_minimo, validate="key",
                  validatecommand=validar_entero).grid(row=3, column=3, sticky="ew", padx=(8, 0), pady=4)

        # Fila 4: proveedor y vencimiento
        ttk.Label(self.marco_form, text="Proveedor habitual:").grid(row=4, column=0, sticky="w", pady=4)
        self.combo_proveedor = ttk.Combobox(self.marco_form, textvariable=self.var_proveedor,
                                            state="readonly", postcommand=self.recargar_combos)
        self.combo_proveedor.grid(row=4, column=1, sticky="ew", padx=(8, 16), pady=4)

        ttk.Label(self.marco_form, text="Vencimiento (DD/MM/AAAA):").grid(row=4, column=2, sticky="w", pady=4)
        ttk.Entry(self.marco_form, textvariable=self.var_vencimiento, validate="key",
                  validatecommand=(self.register(_solo_fecha), "%P")).grid(
            row=4, column=3, sticky="ew", padx=(8, 0), pady=4)

        ttk.Label(self.marco_form, text="Precio de venta vacío = precio sugerido. Montos sin separador de miles. Fecha y proveedor opcionales.",
                  foreground="#58685F", font=("Segoe UI", 9)).grid(
            row=5, column=0, columnspan=4, sticky="w", pady=(5, 0))
        ttk.Label(self.marco_form, textvariable=self.var_sugerido, foreground="#205C49").grid(
            row=6, column=0, columnspan=4, sticky="w", pady=3)
        botones = ttk.Frame(self.marco_form)
        botones.grid(row=7, column=0, columnspan=4, sticky="e", pady=(6, 0))
        self.boton_guardar = ttk.Button(botones, text="Crear producto", command=self.guardar)
        self.boton_guardar.pack(side="left", padx=4)
        ttk.Button(botones, text="Nuevo / Limpiar", style="Secundario.TButton", command=self.limpiar_formulario).pack(side="left", padx=4)
        self.boton_desactivar = ttk.Button(botones, text="Desactivar", command=self.desactivar,
                                           state="disabled")
        self.boton_desactivar.pack(side="left", padx=4)
        self.boton_reactivar = ttk.Button(botones, text="Reactivar", command=self.reactivar,
                                          state="disabled")
        self.boton_reactivar.pack(side="left", padx=4)

    def _crear_tabla(self):
        marco = ttk.LabelFrame(self, text="Listado de productos", padding=8)
        marco.pack(fill="both", expand=True, padx=12, pady=(6, 12))
        marco.rowconfigure(1, weight=1)
        marco.columnconfigure(0, weight=1)

        barra = ttk.Frame(marco)
        barra.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        for col in (1, 3, 5):
            barra.columnconfigure(col, weight=1)
        ttk.Label(barra, text="Buscar:").grid(row=0, column=0, sticky="w")
        ttk.Entry(barra, textvariable=self.var_busqueda).grid(
            row=0, column=1, columnspan=4, sticky="ew", padx=6, pady=(0, 6))
        ttk.Button(barra, text="Actualizar", command=self.actualizar_listado).grid(
            row=0, column=5, sticky="e", pady=(0, 6))
        for col, etiqueta, variable, valores, ancho in (
                (0, "Estado:", self.var_filtro, list(FILTROS_ESTADO), 10),
                (2, "Categoría:", self.var_filtro_categoria, [TODAS_CATEGORIAS], 22),
                (4, "Alertas:", self.var_alerta, FILTROS_ALERTA, 22)):
            ttk.Label(barra, text=etiqueta).grid(row=1, column=col, sticky="w")
            combo = ttk.Combobox(barra, textvariable=variable, values=valores, state="readonly", width=ancho)
            combo.grid(row=1, column=col+1, sticky="ew", padx=6)
            combo.bind("<<ComboboxSelected>>", lambda _e: self.cargar_tabla())
            if col == 2:
                self.combo_filtro_categoria = combo

        columnas = ("nombre", "categoria", "p_venta", "existencia", "alertas", "vencimiento",
                    "p_compra", "stock_min", "proveedor", "estado", "id")
        encabezados = (("id", "ID", 40, "center"), ("nombre", "Nombre", 160, "w"),
                       ("categoria", "Categoría", 120, "w"), ("p_compra", "P. Compra", 115, "e"),
                       ("p_venta", "P. Venta", 115, "e"), ("existencia", "Existencia", 90, "center"),
                       ("stock_min", "Stock mín.", 95, "center"), ("proveedor", "Proveedor", 140, "w"),
                       ("vencimiento", "Vence", 100, "center"), ("estado", "Estado", 75, "center"),
                       ("alertas", "Alertas", 235, "w"))
        self.tabla = ttk.Treeview(marco, columns=columnas, show="headings", selectmode="browse")
        for columna, titulo, ancho, alineacion in encabezados:
            self.tabla.heading(columna, text=titulo, command=lambda c=columna: self.ordenar(c))
            self.tabla.column(columna, width=ancho, minwidth=ancho, stretch=columna == "nombre", anchor=alineacion)
        self.tabla.tag_configure("inactivo", foreground="gray")
        self.tabla.tag_configure("stock_bajo", foreground="#b00020", background="#fde8e8")
        self.tabla.tag_configure("vencido", foreground="#9C2030", background="#FCE8EB")
        self.tabla.tag_configure("vence_pronto", foreground="#805500", background="#FFF3CD")
        self.tabla.tag_configure("revisar", foreground="#805500", background="#FFF3CD")
        self.tabla.grid(row=1, column=0, sticky="nsew")

        scroll = ttk.Scrollbar(marco, orient="vertical", command=self.tabla.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(marco, orient="horizontal", command=self.tabla.xview)
        horizontal.grid(row=2, column=0, sticky="ew")
        self.tabla.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        ttk.Label(marco, textvariable=self.var_resumen, foreground="#58685F").grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(5, 0))
        self.tabla.bind("<<TreeviewSelect>>", self.al_seleccionar_fila)

    # ------------------------------------------------------------------
    # Listas desplegables
    # ------------------------------------------------------------------
    def recargar_combos(self):
        """
        Llena Categoría y Proveedor con registros ACTIVOS. Se ejecuta al abrir la
        ventana y cada vez que se despliega una lista, para ver cambios hechos en
        otras ventanas. Si se edita un producto con categoría/proveedor inactivo,
        ese valor se incluye para no perderlo.
        """
        # Guardar IDs antes de reconstruir los textos evita perder una relación
        # cuando otra ventana cambia el nombre de la categoría o del proveedor.
        categoria_elegida = self.mapa_categorias.get(self.var_categoria.get())
        proveedor_elegido = self.mapa_proveedores.get(self.var_proveedor.get())
        filtro_elegido = self.mapa_filtro_categorias.get(self.var_filtro_categoria.get())
        proveedor_vacio = self.var_proveedor.get() == SIN_PROVEEDOR
        if self._extra_categoria:
            categoria = servicios.obtener_categoria(self.conexion, self._extra_categoria[0])
            self._extra_categoria = (categoria["id_categoria"], categoria["nombre"])
        if self._extra_proveedor:
            proveedor = servicios.obtener_proveedor(self.conexion, self._extra_proveedor[0])
            self._extra_proveedor = (proveedor["id_proveedor"], proveedor["nombre"])

        categorias = [dict(c) for c in servicios.listar_categorias(self.conexion)]
        if self._extra_categoria and self._extra_categoria[0] not in [c["id_categoria"] for c in categorias]:
            categorias.append({"id_categoria": self._extra_categoria[0],
                               "nombre": self._extra_categoria[1]})
        self.mapa_categorias = _construir_mapa(categorias, "id_categoria", "nombre")
        self.combo_categoria["values"] = list(self.mapa_categorias)
        self.var_categoria.set(self._texto_de_id(self.mapa_categorias, categoria_elegida))

        proveedores = [dict(p) for p in servicios.listar_proveedores(self.conexion)]
        if self._extra_proveedor and self._extra_proveedor[0] not in [p["id_proveedor"] for p in proveedores]:
            proveedores.append({"id_proveedor": self._extra_proveedor[0],
                                "nombre": self._extra_proveedor[1]})
        self.mapa_proveedores = {SIN_PROVEEDOR: None}
        for texto, identificador in _construir_mapa(proveedores, "id_proveedor", "nombre").items():
            if texto == SIN_PROVEEDOR:
                texto += f" (#{identificador})"
            self.mapa_proveedores[texto] = identificador
        self.combo_proveedor["values"] = list(self.mapa_proveedores)
        self.var_proveedor.set(SIN_PROVEEDOR if proveedor_vacio else
                               self._texto_de_id(self.mapa_proveedores, proveedor_elegido)
                               if proveedor_elegido is not None else "")
        todas = servicios.listar_categorias(self.conexion, estado=None)
        self.mapa_filtro_categorias = {TODAS_CATEGORIAS: None}
        for texto, identificador in _construir_mapa(todas, "id_categoria", "nombre").items():
            if texto == TODAS_CATEGORIAS:
                texto += f" (#{identificador})"
            self.mapa_filtro_categorias[texto] = identificador
        self.combo_filtro_categoria["values"] = list(self.mapa_filtro_categorias)
        self.var_filtro_categoria.set(self._texto_de_id(self.mapa_filtro_categorias, filtro_elegido)
                                      or TODAS_CATEGORIAS)

    @staticmethod
    def _texto_de_id(mapa, identificador):
        for texto, valor in mapa.items():
            if valor == identificador:
                return texto
        return ""

    # ------------------------------------------------------------------
    # Acciones
    # ------------------------------------------------------------------
    def actualizar_listado(self):
        self.recargar_combos()
        self.cargar_tabla()

    def ordenar(self, columna):
        self.orden_descendente = not self.orden_descendente if columna == self.orden_columna else False
        self.orden_columna = columna
        self.cargar_tabla()

    def _actualizar_sugerido(self):
        try:
            precio = servicios.calcular_precio_sugerido(
                _decimal(self.var_precio_compra.get()), _decimal(self.var_impuesto.get()),
                _decimal(self.var_utilidad.get()))
            self.var_sugerido.set(f"Sugerido: {_formato_dinero(precio)} · impuesto y utilidad calculados sobre el costo.")
        except ErrorNegocio:
            self.var_sugerido.set("Complete costo, impuesto y utilidad para consultar el precio sugerido.")

    def calcular_precio(self):
        """Autocompleta 'Precio venta' = costo + impuesto + utilidad (editable después)."""
        try:
            precio = servicios.calcular_precio_sugerido(
                _decimal(self.var_precio_compra.get()),
                _decimal(self.var_impuesto.get()),
                _decimal(self.var_utilidad.get()))
        except (ErrorNegocio, sqlite3.Error) as error:
            messagebox.showwarning("No se pudo calcular", str(error), parent=self)
            return
        self.var_precio_venta.set(f"{precio:.2f}")

    def cargar_tabla(self):
        """Recarga el Treeview según el filtro de estado y la búsqueda."""
        for fila in self.tabla.get_children():
            self.tabla.delete(fila)
        self.filas_por_id = {}
        productos = servicios.listar_productos(
            self.conexion,
            estado=FILTROS_ESTADO[self.var_filtro.get()],
            id_categoria=self.mapa_filtro_categorias.get(self.var_filtro_categoria.get()),
        )
        hoy = date.today()
        productos = _filtrar_productos(productos, self.var_busqueda.get(), self.var_alerta.get(), hoy)
        productos.sort(key=lambda p: _clave_orden(p, self.orden_columna, hoy), reverse=self.orden_descendente)
        for p in productos:
            self.filas_por_id[p["id_producto"]] = p
            alertas = _alertas_producto(p, hoy)
            if p["estado"] != "activo":
                etiqueta = ("inactivo",)
            elif "Vencido" in alertas:
                etiqueta = ("vencido",)
            elif p["existencia"] <= p["stock_minimo"]:
                etiqueta = ("stock_bajo",)
            elif any(a.startswith("Vence") for a in alertas):
                etiqueta = ("vence_pronto",)
            elif "Revisar fecha" in alertas:
                etiqueta = ("revisar",)
            else:
                etiqueta = ()
            self.tabla.insert("", "end", iid=str(p["id_producto"]), tags=etiqueta, values=(
                p["nombre"], p["categoria"], _formato_dinero(p["precio_venta"]), p["existencia"],
                " · ".join(alertas) or "Sin alertas",
                formato.formatear_fecha(p["fecha_vencimiento"]), _formato_dinero(p["precio_compra"]),
                p["stock_minimo"], p["proveedor"] or "", p["estado"].capitalize(), p["id_producto"]))
        self.var_resumen.set(f"{len(productos)} producto(s) mostrado(s). Vencen en 30 días incluye hoy. "
                             "Pulse un encabezado para ordenar.")

        if self.id_seleccionado is not None:
            if self.id_seleccionado in self.filas_por_id:
                self.tabla.selection_set(str(self.id_seleccionado))
                producto = self.filas_por_id[self.id_seleccionado]
                self.var_existencia.set(str(producto["existencia"]))
                self.boton_guardar.configure(state="normal")
                self.marco_form.configure(text=f"Editar producto (ID {self.id_seleccionado})")
                activo = producto["estado"] == "activo"
                self.boton_desactivar.configure(state="normal" if activo else "disabled")
                self.boton_reactivar.configure(state="disabled" if activo else "normal")
            elif self.hay_cambios_sin_guardar():
                self.marco_form.configure(text="Edición pendiente: producto fuera del filtro")
                self.boton_guardar.configure(state="disabled")
                self.boton_desactivar.configure(state="disabled")
                self.boton_reactivar.configure(state="disabled")
            else:
                self.limpiar_formulario(enfocar=False)

    def al_seleccionar_fila(self, _evento=None):
        seleccion = self.tabla.selection()
        if not seleccion:
            return
        producto = self.filas_por_id.get(int(seleccion[0]))
        if producto is None:
            return
        if self.id_seleccionado == producto["id_producto"]:
            return
        if self.hay_cambios_sin_guardar() and not messagebox.askyesno(
                "Cambios sin guardar", "Hay datos sin guardar. ¿Desea descartarlos y seleccionar otro producto?",
                parent=self):
            if self.id_seleccionado is not None and str(self.id_seleccionado) in self.tabla.get_children():
                self.tabla.selection_set(str(self.id_seleccionado))
            else:
                self.tabla.selection_remove(self.tabla.selection())
            return
        self._cargar_registro(producto)

    def _cargar_registro(self, producto):
        self.id_seleccionado = producto["id_producto"]
        self._producto_original = {campo: producto[campo] for campo in (
            "nombre", "id_categoria", "id_proveedor", "precio_compra", "precio_venta",
            "porcentaje_impuesto", "porcentaje_utilidad", "stock_minimo", "fecha_vencimiento")}

        # Si la categoría/proveedor del producto están inactivos, se incluyen en las listas.
        self._extra_categoria = (producto["id_categoria"], producto["categoria"])
        self._extra_proveedor = ((producto["id_proveedor"], producto["proveedor"])
                                 if producto["id_proveedor"] else None)
        self.recargar_combos()

        self.var_nombre.set(producto["nombre"])
        self.var_categoria.set(self._texto_de_id(self.mapa_categorias, producto["id_categoria"]))
        self.var_precio_compra.set(f"{producto['precio_compra']:.2f}")
        self.var_impuesto.set(f"{producto['porcentaje_impuesto']:g}")
        self.var_utilidad.set(f"{producto['porcentaje_utilidad']:g}")
        self.var_precio_venta.set(f"{producto['precio_venta']:.2f}")
        self.var_stock_minimo.set(str(producto["stock_minimo"]))
        self.var_proveedor.set(self._texto_de_id(self.mapa_proveedores, producto["id_proveedor"]) or SIN_PROVEEDOR)
        self.var_vencimiento.set(formato.formatear_fecha(producto["fecha_vencimiento"]))

        # En edición la existencia es de solo lectura: cambia únicamente con compras y ventas.
        self.var_existencia.set(str(producto["existencia"]))
        self.entrada_existencia.configure(state="disabled")
        self.etiqueta_existencia.configure(text="Existencia actual:")

        self.marco_form.configure(text=f"Editar producto (ID {self.id_seleccionado})")
        self.boton_guardar.configure(text="Guardar cambios")
        activo = producto["estado"] == "activo"
        self.boton_desactivar.configure(state="normal" if activo else "disabled")
        self.boton_reactivar.configure(state="disabled" if activo else "normal")

        self.boton_guardar.configure(state="normal")
        self._datos_cargados = self._datos_formulario()

    def limpiar_formulario(self, enfocar=True):
        self.id_seleccionado = None
        self._producto_original = None
        self._extra_categoria = None
        self._extra_proveedor = None
        self.var_nombre.set("")
        self.var_categoria.set("")
        self.var_precio_compra.set("")
        self.var_impuesto.set(IMPUESTO_INICIAL)
        self.var_utilidad.set(UTILIDAD_INICIAL)
        self.var_precio_venta.set("")
        self.var_existencia.set("0")
        self.var_stock_minimo.set(STOCK_MINIMO_INICIAL)
        self.var_proveedor.set(SIN_PROVEEDOR)
        self.var_vencimiento.set("")
        self.entrada_existencia.configure(state="normal")
        self.etiqueta_existencia.configure(text="Existencia inicial (*):")
        self.marco_form.configure(text="Registrar producto")
        self.boton_guardar.configure(text="Crear producto")
        self.boton_desactivar.configure(state="disabled")
        self.boton_reactivar.configure(state="disabled")
        self.tabla.selection_remove(self.tabla.selection())
        self.recargar_combos()
        self.boton_guardar.configure(state="normal")
        self._datos_cargados = self._datos_formulario()
        if enfocar:
            self.entrada_nombre.focus_set()

    def guardar(self):
        """Registra un producto nuevo o actualiza el seleccionado."""
        if self.id_seleccionado is not None and str(self.id_seleccionado) not in self.tabla.get_children():
            messagebox.showwarning("Guardar", "El producto está fuera del filtro. Ajuste el filtro o pulse Limpiar.", parent=self)
            return
        try:
            if self.id_seleccionado is not None:
                actual = servicios.obtener_producto(self.conexion, self.id_seleccionado)
                original = getattr(self, "_producto_original", None)
                if original and any(actual[campo] != valor for campo, valor in original.items()):
                    raise ErrorNegocio("Los datos del producto cambiaron en otra ventana. "
                                       "Pulse Nuevo / Limpiar y selecciónelo de nuevo antes de guardar.")
            self.recargar_combos()
        except (ErrorNegocio, sqlite3.Error) as error:
            messagebox.showwarning("No se pudo guardar", str(error), parent=self)
            return
        id_categoria = self.mapa_categorias.get(self.var_categoria.get())
        if id_categoria is None:
            messagebox.showwarning("No se pudo guardar", "Debe seleccionar una categoría.", parent=self)
            return
        if self.var_proveedor.get() not in self.mapa_proveedores:
            messagebox.showwarning("No se pudo guardar", "Seleccione nuevamente el proveedor "
                                   "o elija «(Sin proveedor)».", parent=self)
            return
        id_proveedor = self.mapa_proveedores[self.var_proveedor.get()]
        # Se acepta la coma como separador decimal.
        precio_compra = _decimal(self.var_precio_compra.get())
        precio_venta = _decimal(self.var_precio_venta.get())    # vacío => precio sugerido
        impuesto = _decimal(self.var_impuesto.get())
        utilidad = _decimal(self.var_utilidad.get())

        try:
            vencimiento = formato.a_fecha_iso(self.var_vencimiento.get())   # DD/MM/AAAA -> BD
            if self.id_seleccionado is None:
                servicios.crear_producto(
                    self.conexion, self.var_nombre.get(), id_categoria, precio_compra,
                    precio_venta, self.var_existencia.get(), id_proveedor,
                    vencimiento,
                    porcentaje_impuesto=impuesto, porcentaje_utilidad=utilidad,
                    stock_minimo=self.var_stock_minimo.get())
                mensaje = "Producto registrado correctamente."
            else:
                servicios.modificar_producto(
                    self.conexion, self.id_seleccionado, self.var_nombre.get(), id_categoria,
                    precio_compra, precio_venta, id_proveedor, vencimiento,
                    porcentaje_impuesto=impuesto, porcentaje_utilidad=utilidad,
                    stock_minimo=self.var_stock_minimo.get())
                mensaje = "Producto actualizado correctamente."
        except (ErrorNegocio, sqlite3.Error) as error:
            messagebox.showwarning("No se pudo guardar", str(error), parent=self)
            return
        messagebox.showinfo("Éxito", mensaje, parent=self)
        self.limpiar_formulario()
        self.cargar_tabla()

    def desactivar(self):
        if self.id_seleccionado is None:
            return
        if not messagebox.askyesno(
                "Confirmar",
                "¿Desea desactivar este producto?\nNo se podrá usar en compras ni ventas, "
                "pero se conserva en el historial.", parent=self):
            return
        try:
            servicios.desactivar_producto(self.conexion, self.id_seleccionado)
        except (ErrorNegocio, sqlite3.Error) as error:
            messagebox.showwarning("Error", str(error), parent=self)
            return
        self.limpiar_formulario()
        self.cargar_tabla()
        messagebox.showinfo("Estado actualizado", "Producto desactivado correctamente.", parent=self)

    def reactivar(self):
        if self.id_seleccionado is None:
            return
        try:
            servicios.reactivar_producto(self.conexion, self.id_seleccionado)
        except (ErrorNegocio, sqlite3.Error) as error:
            messagebox.showwarning("Error", str(error), parent=self)
            return
        self.limpiar_formulario()
        self.cargar_tabla()
        messagebox.showinfo("Estado actualizado", "Producto reactivado correctamente.", parent=self)

    def _datos_formulario(self):
        return (self.var_nombre.get(), self.mapa_categorias.get(self.var_categoria.get()),
                self.mapa_proveedores.get(self.var_proveedor.get()), self.var_precio_compra.get(),
                self.var_precio_venta.get(), self.var_impuesto.get(), self.var_utilidad.get(),
                self.var_stock_minimo.get(), self.var_vencimiento.get(),
                self.var_existencia.get() if self.id_seleccionado is None else None)

    def hay_cambios_sin_guardar(self):
        return self._datos_formulario() != self._datos_cargados

    def actualizar_al_volver(self):
        conservar_edicion = self.hay_cambios_sin_guardar()
        self.recargar_combos()
        self.cargar_tabla()
        if not conservar_edicion and self.id_seleccionado in self.filas_por_id:
            self._cargar_registro(self.filas_por_id[self.id_seleccionado])

    def cerrar(self):
        if self.hay_cambios_sin_guardar() and not messagebox.askyesno(
                "Cambios sin guardar", "Hay datos sin guardar. ¿Desea cerrar y descartarlos?", parent=self):
            return
        self.destroy()
