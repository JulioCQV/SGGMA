"""
ui/ui_compras.py
Módulo de Compras: proveedor obligatorio, carrito temporal y confirmación.

- El Precio Unitario (costo) se escribe a mano: es el precio REAL pagado al proveedor.
- Al elegir un producto, los campos % Impuesto y % Utilidad se autocompletan con los suyos y
  una sola etiqueta muestra "Precio Actual | Sugerido" (el sugerido se recalcula en vivo).
  El 'Nuevo Precio de Venta Final' se autocompleta con el sugerido, pero se puede cambiar.
- El carrito vive en memoria; la BD y el inventario solo cambian al "Confirmar Compra",
  que llama a servicios.registrar_compra (transacción con COMMIT / ROLLBACK).
"""

import math
import re
import unicodedata
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

import servicios
from servicios import ErrorNegocio
from ui import ui_admin

MONEDA = "₡"


def abrir_ventana(padre, conexion):
    """Función que llama ui_principal.py; devuelve el Toplevel creado."""
    return VentanaCompras(padre, conexion)


def _dinero(valor):
    return f"{MONEDA}{valor:,.2f}"


def _solo_digitos(texto_nuevo):
    return bool(re.fullmatch(r"[0-9]{0,9}", texto_nuevo))


def _solo_decimal(texto_nuevo):
    """Permite dígitos y un separador decimal (punto o coma)."""
    return len(texto_nuevo) <= 16 and bool(re.fullmatch(r"[0-9]*(?:[.,][0-9]{0,2})?", texto_nuevo))


def _leer_decimal(texto, nombre_campo, obligatorio=False, maximo=None):
    """
    Convierte el texto de un campo en float (acepta coma). Devuelve None si está vacío
    y no es obligatorio. Lanza ErrorNegocio con un mensaje claro si es inválido.
    """
    texto = (texto or "").strip().replace(",", ".")
    if not texto:
        if obligatorio:
            raise ErrorNegocio(f"El campo '{nombre_campo}' es obligatorio.")
        return None
    try:
        numero = round(float(texto), 2)
    except (ValueError, OverflowError):
        raise ErrorNegocio(f"'{nombre_campo}' debe ser un número válido.")
    if not math.isfinite(numero):
        raise ErrorNegocio(f"'{nombre_campo}' debe ser un número finito.")
    if numero < 0:
        raise ErrorNegocio(f"'{nombre_campo}' no puede ser negativo.")
    if maximo is not None and numero > maximo:
        raise ErrorNegocio(f"'{nombre_campo}' no puede ser mayor a {maximo:g}.")
    return numero


def _filtrar_productos(productos, busqueda):
    def normalizar(texto):
        return "".join(c for c in unicodedata.normalize("NFD", texto.casefold())
                       if not unicodedata.combining(c))
    palabras = normalizar(busqueda).split()
    return [p for p in productos if p["estado"] == "activo"
            and all(palabra in normalizar(" ".join((p["nombre"], p["categoria"],
                                                    p["proveedor"] or ""))) for palabra in palabras)]


def _preparar_lineas(carrito, nueva):
    """Une producto+costo y aplica una única configuración de venta por producto.

    Los datos se copian: una validación fallida no modifica el carrito original.
    Un producto puede tener distintos costos de compra, pero solo un precio de venta.
    """
    lineas = [dict(linea) for linea in carrito]
    for indice, linea in enumerate(lineas):
        if linea["id_producto"] == nueva["id_producto"] and linea["precio"] == nueva["precio"]:
            cantidad = linea["cantidad"] + nueva["cantidad"]
            if cantidad > 999999999:
                raise ErrorNegocio("La cantidad de una línea no puede superar 999999999.")
            linea["cantidad"] = cantidad
            # La última entrada determina el costo actual al confirmar la compra.
            lineas.append(lineas.pop(indice))
            break
    else:
        lineas.append(dict(nueva))
    for linea in lineas:
        if linea["id_producto"] == nueva["id_producto"]:
            for campo in ("nuevo_precio_venta", "nuevo_porcentaje_impuesto",
                          "nuevo_porcentaje_utilidad", "cambia"):
                linea[campo] = nueva[campo]
    return lineas


def _resumen_carrito(carrito):
    return {"lineas": len(carrito), "productos": len({l["id_producto"] for l in carrito}),
            "unidades": sum(l["cantidad"] for l in carrito),
            "total": round(sum(round(l["precio"] * l["cantidad"], 2) for l in carrito), 2)}


class VentanaCompras(tk.Toplevel):
    def __init__(self, padre, conexion):
        super().__init__(padre)
        self.conexion = conexion
        # Cada línea: id_producto, nombre, precio (costo), cantidad, nuevo_precio_venta,
        # nuevo_porcentaje_impuesto, nuevo_porcentaje_utilidad, cambia
        self.carrito = []
        self.mapa_proveedores = {}      # texto visible -> id_proveedor
        self.mapa_productos = {}        # texto visible -> id_producto
        self.producto_actual = None     # datos actuales (BD) del producto seleccionado
        self.productos = []
        self._precio_final_manual = False   # True si el usuario escribió el precio final
        self._autocompletando = False       # True mientras el programa escribe el precio final

        self.title("GeMMa | Registrar compra")
        ancho = min(1140, self.winfo_screenwidth() - 60)
        alto = min(780, self.winfo_screenheight() - 100)
        self.geometry(f"{ancho}x{alto}")
        self.minsize(min(800, ancho), min(520, alto))
        self.protocol("WM_DELETE_WINDOW", self.cerrar)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self._crear_area_desplazable()

        self.var_proveedor = tk.StringVar()
        self.var_producto = tk.StringVar()
        self.var_busqueda = tk.StringVar()
        self.var_resultados = tk.StringVar()
        self.var_conservar_precio = tk.BooleanVar(value=False)
        self.var_resumen = tk.StringVar()
        self.var_cantidad = tk.StringVar(value="1")
        self.var_precio = tk.StringVar()                 # costo unitario real pagado
        self.var_impuesto = tk.StringVar()               # % impuesto editable
        self.var_utilidad = tk.StringVar()               # % utilidad editable
        self.var_precio_final = tk.StringVar()           # nuevo precio de venta final
        self.var_info = tk.StringVar()                   # "Precio Actual: ₡X | Sugerido: ₡Y"
        self.var_total = tk.StringVar(value=_dinero(0))
        self._mostrar_info_vacia()

        cabecera = ttk.Frame(self.contenido, padding=(16, 10, 16, 0))
        cabecera.pack(fill="x")
        ttk.Label(cabecera, text="Registrar compra", font=("Segoe UI", 18, "bold")).pack(side="left")
        ttk.Button(cabecera, text="Ver historial", style="Secundario.TButton",
                   command=self.abrir_historial).pack(side="right")
        self._crear_seleccion()
        self._crear_carrito()
        self._crear_pie()
        self.bind("<MouseWheel>", self._desplazar_contenido, add="+")
        self.bind("<Button-4>", self._desplazar_contenido, add="+")
        self.bind("<Button-5>", self._desplazar_contenido, add="+")
        self.recargar_combos()

        # Recalcula el precio sugerido en tiempo real (después de crear todos los widgets).
        for variable in (self.var_precio, self.var_impuesto, self.var_utilidad):
            variable.trace_add("write", lambda *_: self._recalcular_sugerido())
        self.var_precio_final.trace_add("write", lambda *_: self._al_cambiar_precio_final())
        self.var_busqueda.trace_add("write", lambda *_: self._filtrar_lista())
        self._actualizar_carrito()
        self.combo_proveedor.focus_set()

    # ------------------------------------------------------------------
    # Construcción de la interfaz
    # ------------------------------------------------------------------
    def _crear_area_desplazable(self):
        """El formulario y la tabla se desplazan; las acciones y el total quedan fijos."""
        area = ttk.Frame(self)
        area.grid(row=0, column=0, sticky="nsew")
        area.rowconfigure(0, weight=1)
        area.columnconfigure(0, weight=1)
        fondo = ttk.Style(self).lookup("TFrame", "background") or "#F3F6F3"
        self.canvas_contenido = tk.Canvas(area, background=fondo, highlightthickness=0, bd=0)
        self.canvas_contenido.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(area, orient="vertical", command=self.canvas_contenido.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(area, orient="horizontal", command=self.canvas_contenido.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.canvas_contenido.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.contenido = ttk.Frame(self.canvas_contenido)
        self._ventana_contenido = self.canvas_contenido.create_window(
            0, 0, window=self.contenido, anchor="nw")
        self.canvas_contenido.bind("<Configure>", self._ajustar_area)
        self.contenido.bind("<Configure>", self._ajustar_area)

    def _ajustar_area(self, _evento=None):
        ancho = max(self.canvas_contenido.winfo_width(), self.contenido.winfo_reqwidth())
        alto = max(self.canvas_contenido.winfo_height(), self.contenido.winfo_reqheight())
        self.canvas_contenido.itemconfigure(self._ventana_contenido, width=ancho, height=alto)
        self.canvas_contenido.configure(scrollregion=self.canvas_contenido.bbox("all"))

    def _desplazar_contenido(self, evento):
        # Las listas y la tabla conservan su propio desplazamiento.
        if isinstance(evento.widget, (ttk.Treeview, ttk.Combobox, ttk.Scrollbar)):
            return
        if getattr(evento, "num", None) in (4, 5):
            unidades = -1 if evento.num == 4 else 1
        else:
            delta = getattr(evento, "delta", 0)
            if not delta:
                return
            unidades = (-1 if delta > 0 else 1) * max(1, abs(int(delta)) // 120)
        self.canvas_contenido.yview_scroll(unidades, "units")

    def _crear_seleccion(self):
        marco = ttk.LabelFrame(self.contenido, text="Datos de la compra", padding=12)
        marco.pack(fill="x", padx=12, pady=(12, 6))
        for columna in (1, 3):
            marco.columnconfigure(columna, weight=1, uniform="campos")

        validar_entero = (self.register(_solo_digitos), "%P")
        validar_decimal = (self.register(_solo_decimal), "%P")

        ttk.Label(marco, text="Proveedor (*):").grid(row=0, column=0, sticky="w", pady=4)
        self.combo_proveedor = ttk.Combobox(marco, textvariable=self.var_proveedor, state="readonly",
                                            postcommand=self.recargar_combos)
        self.combo_proveedor.grid(row=0, column=1, columnspan=3, sticky="ew", padx=(8, 0), pady=4)
        ttk.Button(marco, text="Actualizar lista", command=self.recargar_combos).grid(
            row=0, column=4, sticky="ew", padx=(8, 0), pady=4)
        ttk.Label(marco, text="Buscar producto:").grid(row=1, column=0, sticky="w", pady=4)
        self.entrada_busqueda = ttk.Entry(marco, textvariable=self.var_busqueda)
        self.entrada_busqueda.grid(row=1, column=1, columnspan=4, sticky="ew", padx=(8, 0), pady=4)

        ttk.Label(marco, text="Producto (*):").grid(row=2, column=0, sticky="w", pady=4)
        self.combo_producto = ttk.Combobox(marco, textvariable=self.var_producto, state="readonly",
                                           postcommand=self.recargar_combos)
        self.combo_producto.grid(row=2, column=1, columnspan=4, sticky="ew", padx=(8, 0), pady=4)
        self.combo_producto.bind("<<ComboboxSelected>>", lambda _e: self._al_seleccionar_producto())

        # Fila 2: cantidad y costo
        ttk.Label(marco, text="Cantidad (*):").grid(row=3, column=0, sticky="w", pady=4)
        self.entrada_cantidad = ttk.Entry(marco, textvariable=self.var_cantidad, width=10,
                                          validate="key", validatecommand=validar_entero)
        self.entrada_cantidad.grid(row=3, column=1, sticky="w", padx=(8, 16), pady=4)

        ttk.Label(marco, text="Costo pagado por unidad (₡) (*):").grid(row=3, column=2, sticky="w", pady=4)
        ttk.Entry(marco, textvariable=self.var_precio, validate="key",
                  validatecommand=validar_decimal).grid(row=3, column=3, columnspan=2, sticky="ew", padx=(8, 0), pady=4)

        # Fila 3: porcentajes editables
        ttk.Label(marco, text="% Impuesto:").grid(row=4, column=0, sticky="w", pady=4)
        ttk.Entry(marco, textvariable=self.var_impuesto, width=10, validate="key",
                  validatecommand=validar_decimal).grid(row=4, column=1, sticky="w", padx=(8, 16), pady=4)

        ttk.Label(marco, text="% Utilidad:").grid(row=4, column=2, sticky="w", pady=4)
        ttk.Entry(marco, textvariable=self.var_utilidad, validate="key",
                  validatecommand=validar_decimal).grid(row=4, column=3, columnspan=2, sticky="ew", padx=(8, 0), pady=4)

        # Fila 4: etiqueta informativa única
        estilo = ttk.Style(self)
        estilo.configure("Info.TLabel", font=("Segoe UI", 11, "bold"), foreground="#0a5c0a")
        ttk.Label(marco, textvariable=self.var_info, style="Info.TLabel").grid(
            row=5, column=0, columnspan=5, sticky="w", pady=(8, 4))

        # Fila 5: precio final y botón agregar
        ttk.Label(marco, text="Venta final por unidad (₡):").grid(row=6, column=0, sticky="w", pady=4)
        marco_final = ttk.Frame(marco)
        marco_final.grid(row=6, column=1, columnspan=3, sticky="ew", pady=4)
        marco_final.columnconfigure(0, weight=1)
        self.entrada_final = ttk.Entry(marco_final, textvariable=self.var_precio_final, validate="key",
                                  validatecommand=validar_decimal)
        self.entrada_final.grid(row=0, column=0, sticky="ew", padx=(8, 0))
        self.entrada_final.bind("<Return>", lambda _e: self.agregar_al_carrito())
        ttk.Button(marco_final, text="Usar sugerido", command=self.usar_precio_sugerido).grid(
            row=0, column=1, padx=(6, 0))
        ttk.Checkbutton(marco_final, text="Conservar actual", variable=self.var_conservar_precio,
                        command=self._aplicar_politica_precio).grid(row=0, column=2, padx=8)
        ttk.Button(marco, text="Agregar línea", command=self.agregar_al_carrito).grid(
            row=6, column=4, sticky="e", padx=(12, 0), pady=4)
        ttk.Label(marco, textvariable=self.var_resultados, foreground="#58685F", font=("Segoe UI", 9)).grid(
            row=7, column=0, columnspan=4, sticky="w", pady=(3, 0))
        ttk.Button(marco, text="Limpiar campos", style="Secundario.TButton",
                   command=self.limpiar_campos).grid(row=7, column=4, sticky="e", pady=(3, 0))

    def _crear_carrito(self):
        marco = ttk.LabelFrame(self.contenido, text="Carrito de compra", padding=8)
        marco.pack(fill="both", expand=True, padx=12, pady=6)
        marco.rowconfigure(0, weight=1)
        marco.columnconfigure(0, weight=1)

        columnas = ("producto", "precio", "cantidad", "subtotal", "pv_final", "imp", "util")
        encabezados = (("producto", "Producto", 230, "w"), ("precio", "Costo unit.", 90, "e"),
                       ("cantidad", "Cantidad", 90, "center"), ("subtotal", "Total a proveedor", 140, "e"),
                       ("pv_final", "Venta final", 140, "e"), ("imp", "% Imp.", 65, "center"),
                       ("util", "% Util.", 65, "center"))
        self.tabla = ttk.Treeview(marco, columns=columnas, show="headings", selectmode="browse", height=6)
        for columna, titulo, ancho, alineacion in encabezados:
            self.tabla.heading(columna, text=titulo)
            self.tabla.column(columna, width=ancho, minwidth=ancho, stretch=columna == "producto", anchor=alineacion)
        self.tabla.grid(row=0, column=0, sticky="nsew")

        scroll = ttk.Scrollbar(marco, orient="vertical", command=self.tabla.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(marco, orient="horizontal", command=self.tabla.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.tabla.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)

    def _crear_pie(self):
        # Fuera del Canvas: los controles del carrito siempre quedan visibles.
        self.pie_compra = ttk.Frame(self, padding=(16, 8, 16, 12))
        pie = self.pie_compra
        pie.grid(row=1, column=0, sticky="ew")
        pie.columnconfigure(0, weight=1)

        self.barra_acciones = ttk.Frame(pie)
        self.barra_acciones.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        self.botones_carrito = [
            ttk.Button(self.barra_acciones, text="Cambiar cantidad", command=self.cambiar_cantidad),
            ttk.Button(self.barra_acciones, text="Quitar seleccionado", style="Secundario.TButton",
                       command=self.quitar_seleccionado),
            ttk.Button(self.barra_acciones, text="Vaciar carrito", style="Secundario.TButton",
                       command=self.vaciar_carrito),
        ]
        self._columnas_acciones = None
        self.barra_acciones.bind("<Configure>", self._ajustar_acciones)
        self._ajustar_acciones()

        resumen = ttk.Label(pie, textvariable=self.var_resumen, foreground="#58685F",
                            font=("Segoe UI", 9), wraplength=760, width=1)
        resumen.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        resumen.bind("<Configure>", lambda evento: resumen.configure(wraplength=max(120, evento.width)))

        estilo = ttk.Style(self)
        estilo.configure("CompraTotal.TLabel", font=("Segoe UI", 22, "bold"), foreground="#205C49")
        ttk.Label(pie, text="Total a pagar al proveedor", font=("Segoe UI", 11)).grid(
            row=2, column=0, sticky="w")
        self.etiqueta_total = ttk.Label(pie, textvariable=self.var_total, style="CompraTotal.TLabel")
        self.etiqueta_total.grid(row=3, column=0, sticky="w", pady=(2, 0))
        self.boton_confirmar = ttk.Button(pie, text="Confirmar compra", command=self.confirmar_compra)
        self.boton_confirmar.grid(row=2, column=1, rowspan=2, sticky="e", padx=(12, 0))

    def _ajustar_acciones(self, _evento=None):
        """Acomoda los botones en otra fila si el ancho o la escala lo requieren."""
        ancho = self.barra_acciones.winfo_width()
        anchos = [boton.winfo_reqwidth() for boton in self.botones_carrito]
        if ancho >= 3 * max(anchos) + 24:
            columnas = 3
        elif ancho >= 2 * max(anchos) + 16:
            columnas = 2
        else:
            columnas = 1
        if columnas == self._columnas_acciones:
            return
        self._columnas_acciones = columnas
        for columna in range(3):
            self.barra_acciones.columnconfigure(columna, weight=1 if columna < columnas else 0,
                                                uniform="acciones" if columna < columnas else "")
        for indice, boton in enumerate(self.botones_carrito):
            boton.grid(row=indice // columnas, column=indice % columnas, sticky="ew", padx=4, pady=3)

    # ------------------------------------------------------------------
    # Listas desplegables
    # ------------------------------------------------------------------
    def abrir_historial(self):
        ui_admin.abrir_historial(self, self.conexion, "compra")

    def actualizar_al_volver(self):
        self.recargar_combos()
        ui_admin.actualizar_historial_abierto(self)

    def hay_cambios_sin_guardar(self):
        return bool(self.carrito or self.var_precio.get().strip())

    def recargar_combos(self):
        """Proveedores y productos ACTIVOS. Se refresca al desplegar cada lista."""
        seleccionado = self.mapa_proveedores.get(self.var_proveedor.get())
        self.mapa_proveedores = {}
        for p in servicios.listar_proveedores(self.conexion):
            texto = p["nombre"]
            if texto in self.mapa_proveedores:
                texto = f"{texto} #{p['id_proveedor']}"
            self.mapa_proveedores[texto] = p["id_proveedor"]
        self.combo_proveedor["values"] = list(self.mapa_proveedores)
        self.var_proveedor.set(next((texto for texto, identificador in self.mapa_proveedores.items()
                                     if identificador == seleccionado), ""))

        self.productos = servicios.listar_productos(self.conexion)
        self._filtrar_lista(conservar_seleccion=True)

    def _filtrar_lista(self, conservar_seleccion=False):
        seleccionado = self.mapa_productos.get(self.var_producto.get())
        pendiente = conservar_seleccion and bool(self.var_precio.get().strip())
        filtrados = _filtrar_productos(self.productos, self.var_busqueda.get())
        if pendiente:
            actual = next((p for p in self.productos if p["id_producto"] == seleccionado), None)
            if actual is not None and all(p["id_producto"] != seleccionado for p in filtrados):
                filtrados.append(actual)
        self.mapa_productos = {}
        for p in filtrados:
            texto = f"{p['nombre']} [{p['categoria']}] · Existencia: {p['existencia']} · #{p['id_producto']}"
            self.mapa_productos[texto] = p["id_producto"]
        self.combo_producto["values"] = list(self.mapa_productos)
        texto = next((t for t, identificador in self.mapa_productos.items() if identificador == seleccionado), "")
        if not texto and len(self.mapa_productos) == 1 and not pendiente:
            texto = next(iter(self.mapa_productos))
        self.var_producto.set(texto)
        if texto:
            nuevo = self.mapa_productos[texto]
            if self.producto_actual is None or self.producto_actual["id_producto"] != nuevo:
                self._al_seleccionar_producto()
            else:
                # Refresca los datos sin borrar un costo que ya se esté escribiendo.
                anterior = self.producto_actual
                self.producto_actual = servicios.obtener_producto(self.conexion, nuevo)
                sin_edicion = (not self.var_precio.get().strip()
                               and not self._precio_final_manual
                               and not self.var_conservar_precio.get()
                               and self.var_impuesto.get() == f"{anterior['porcentaje_impuesto']:g}"
                               and self.var_utilidad.get() == f"{anterior['porcentaje_utilidad']:g}")
                if sin_edicion:
                    self._cargar_datos_producto()
                elif self.var_conservar_precio.get():
                    self._aplicar_politica_precio()
                self._recalcular_sugerido()
        else:
            self.var_producto.set("")
            self.producto_actual = None
            if not pendiente:
                self.limpiar_campos()
            self._mostrar_info_vacia()
        self.var_resultados.set(f"{len(self.mapa_productos)} producto(s). Busque por nombre, categoría o proveedor. Montos sin separador de miles.")

    # ------------------------------------------------------------------
    # Datos actuales del producto y precio sugerido
    # ------------------------------------------------------------------
    def _mostrar_info_vacia(self):
        self.var_info.set("")

    def _actualizar_info(self, sugerido):
        """Etiqueta única: 'Precio Actual: ₡X | Sugerido: ₡Y' (solo con un producto elegido)."""
        if self.producto_actual is None:
            self.var_info.set("")
            return
        texto_sugerido = _dinero(sugerido) if sugerido is not None else "—"
        self.var_info.set(f"Venta actual: {_dinero(self.producto_actual['precio_venta'])}"
                          f"  |  Venta sugerida: {texto_sugerido}")

    def _al_seleccionar_producto(self):
        id_producto = self.mapa_productos.get(self.var_producto.get())
        if id_producto is None:
            self.producto_actual = None
            self._mostrar_info_vacia()
            return
        self.producto_actual = servicios.obtener_producto(self.conexion, id_producto)
        self.var_precio.set("")
        self.var_cantidad.set("1")
        self._cargar_datos_producto()

    def _cargar_datos_producto(self):
        """Autocompleta los % editables con los del producto y refresca la etiqueta."""
        p = self.producto_actual
        self.var_conservar_precio.set(False)
        self.entrada_final.configure(state="normal")
        self._precio_final_manual = False
        self._poner_precio_final("")
        self.var_impuesto.set(f"{p['porcentaje_impuesto']:g}")
        self.var_utilidad.set(f"{p['porcentaje_utilidad']:g}")
        self._recalcular_sugerido()

    def _calcular_sugerido(self):
        """Precio sugerido con los datos del formulario, o None si faltan/son inválidos."""
        textos = [t.strip().replace(",", ".") for t in
                  (self.var_precio.get(), self.var_impuesto.get(), self.var_utilidad.get())]
        if not all(textos):
            return None
        try:
            return servicios.calcular_precio_sugerido(*textos)
        except ErrorNegocio:
            return None

    def _recalcular_sugerido(self):
        """Se ejecuta cada vez que cambia el costo o un porcentaje."""
        sugerido = self._calcular_sugerido()
        self._actualizar_info(sugerido)
        if not self._precio_final_manual and not self.var_conservar_precio.get():
            self._poner_precio_final(f"{sugerido:.2f}" if sugerido is not None else "")

    def _poner_precio_final(self, texto):
        """Escribe el precio final desde el programa (sin contarlo como edición del usuario)."""
        self._autocompletando = True
        try:
            self.var_precio_final.set(texto)
        finally:
            self._autocompletando = False

    def _al_cambiar_precio_final(self):
        if self._autocompletando:
            return
        # Si el usuario escribe un valor, se respeta; si borra el campo, vuelve el autocompletado.
        self._precio_final_manual = bool(self.var_precio_final.get().strip())

    def mantener_precio_actual(self):
        """Pone como precio final el precio de venta vigente (es decir, sin cambio de precio)."""
        if self.producto_actual is None:
            messagebox.showinfo("Compra", "Seleccione un producto.", parent=self)
            return
        self.var_conservar_precio.set(True)
        self._aplicar_politica_precio()

    def _aplicar_politica_precio(self):
        conservar = self.var_conservar_precio.get()
        self.entrada_final.configure(state="disabled" if conservar else "normal")
        self._precio_final_manual = conservar
        if conservar:
            self._poner_precio_final(f"{self.producto_actual['precio_venta']:.2f}" if self.producto_actual else "")
        else:
            self._recalcular_sugerido()

    def usar_precio_sugerido(self):
        sugerido = self._calcular_sugerido()
        if self.producto_actual is None or sugerido is None:
            messagebox.showwarning("Compra", "Seleccione un producto y complete costo, impuesto y utilidad.", parent=self)
            return
        self.var_conservar_precio.set(False)
        self.entrada_final.configure(state="normal")
        self._precio_final_manual = False
        self._poner_precio_final(f"{sugerido:.2f}")

    def limpiar_campos(self):
        self.var_cantidad.set("1")
        self.var_precio.set("")
        self.var_conservar_precio.set(False)
        self.entrada_final.configure(state="normal")
        self._precio_final_manual = False
        if self.producto_actual is not None:
            self._cargar_datos_producto()
        else:
            self.var_impuesto.set("")
            self.var_utilidad.set("")
            self._poner_precio_final("")
            self._mostrar_info_vacia()

    # ------------------------------------------------------------------
    # Carrito
    # ------------------------------------------------------------------
    def agregar_al_carrito(self):
        id_producto = self.mapa_productos.get(self.var_producto.get())
        if id_producto is None:
            messagebox.showwarning("Compra", "Seleccione un producto.", parent=self)
            return
        try:
            texto_cantidad = self.var_cantidad.get()
            cantidad = int(texto_cantidad) if re.fullmatch(r"[0-9]{1,9}", texto_cantidad) else 0
        except ValueError:
            cantidad = 0
        if cantidad < 1:
            messagebox.showwarning("Compra", "La cantidad debe ser un entero mayor o igual a 1.", parent=self)
            return
        try:
            costo = _leer_decimal(self.var_precio.get(), "Costo unitario", obligatorio=True)
            impuesto = _leer_decimal(self.var_impuesto.get(), "% Impuesto", maximo=100)
            utilidad = _leer_decimal(self.var_utilidad.get(), "% Utilidad")
            precio_final = (None if self.var_conservar_precio.get() else
                            _leer_decimal(self.var_precio_final.get(), "Precio de venta final"))
            producto = servicios.obtener_producto(self.conexion, id_producto)
        except ErrorNegocio as error:
            messagebox.showwarning("Compra", str(error), parent=self)
            return
        if producto["estado"] != "activo":
            messagebox.showwarning("Compra", f"El producto '{producto['nombre']}' está inactivo.", parent=self)
            self.recargar_combos()
            return

        # ¿Esta línea modifica algo del producto respecto a lo que hoy tiene guardado?
        cambia = ((precio_final is not None and precio_final != producto["precio_venta"])
                  or (impuesto is not None and impuesto != producto["porcentaje_impuesto"])
                  or (utilidad is not None and utilidad != producto["porcentaje_utilidad"]))

        try:
            self.carrito = _preparar_lineas(self.carrito, {
                "id_producto": id_producto, "nombre": producto["nombre"],
                "precio": costo, "cantidad": cantidad,
                "nuevo_precio_venta": precio_final,
                "nuevo_porcentaje_impuesto": impuesto,
                "nuevo_porcentaje_utilidad": utilidad,
                "cambia": cambia,
            })
        except ErrorNegocio as error:
            messagebox.showwarning("Compra", str(error), parent=self)
            return

        # Preparar el formulario para la siguiente línea.
        self.var_cantidad.set("1")
        self.var_precio.set("")
        self.producto_actual = producto
        self._cargar_datos_producto()
        self._actualizar_carrito()

    def cambiar_cantidad(self):
        seleccion = self.tabla.selection()
        if not seleccion:
            messagebox.showinfo("Compra", "Seleccione una línea del carrito.", parent=self)
            return
        indice = int(seleccion[0])
        linea = self.carrito[indice]
        cantidad = simpledialog.askinteger("Cambiar cantidad", f"Nueva cantidad de {linea['nombre']}:",
                                          initialvalue=linea["cantidad"], minvalue=1,
                                          maxvalue=999999999, parent=self)
        if cantidad is not None:
            linea["cantidad"] = cantidad
            self._actualizar_carrito()

    def quitar_seleccionado(self):
        seleccion = self.tabla.selection()
        if not seleccion:
            messagebox.showinfo("Compra", "Seleccione una línea del carrito.", parent=self)
            return
        del self.carrito[int(seleccion[0])]
        self._actualizar_carrito()

    def vaciar_carrito(self):
        if self.carrito and not messagebox.askyesno("Confirmar", "¿Desea vaciar el carrito?", parent=self):
            return
        self.carrito.clear()
        self._actualizar_carrito()

    def _actualizar_carrito(self):
        """Redibuja el Treeview (iid = posición en la lista) y recalcula el total."""
        for fila in self.tabla.get_children():
            self.tabla.delete(fila)
        total = 0.0
        for posicion, linea in enumerate(self.carrito):
            subtotal = round(linea["precio"] * linea["cantidad"], 2)
            total += subtotal
            pv = linea["nuevo_precio_venta"]
            imp = linea["nuevo_porcentaje_impuesto"]
            util = linea["nuevo_porcentaje_utilidad"]
            self.tabla.insert("", "end", iid=str(posicion), values=(
                linea["nombre"], _dinero(linea["precio"]), linea["cantidad"], _dinero(subtotal),
                _dinero(pv) if pv is not None else "Conservar actual",
                f"{imp:g}" if imp is not None else "-",
                f"{util:g}" if util is not None else "-"))
        self.var_total.set(_dinero(round(total, 2)))
        resumen = _resumen_carrito(self.carrito)
        self.var_resumen.set(f"{resumen['productos']} producto(s) · {resumen['unidades']} unidad(es). "
                             "Precio final y porcentajes compartidos entre líneas del mismo producto.")
        self.boton_confirmar.configure(state="normal" if self.carrito else "disabled")

    # ------------------------------------------------------------------
    # Confirmación
    # ------------------------------------------------------------------
    def confirmar_compra(self):
        id_proveedor = self.mapa_proveedores.get(self.var_proveedor.get())
        if id_proveedor is None:
            messagebox.showwarning("Compra", "Debe seleccionar un proveedor.", parent=self)
            return
        if not self.carrito:
            messagebox.showwarning("Compra", "El carrito está vacío.", parent=self)
            return
        if self.var_precio.get().strip():
            messagebox.showwarning("Compra", "Tiene un costo escrito en el formulario. "
                                   "Agregue esa línea o pulse «Limpiar campos» antes de confirmar.", parent=self)
            return

        cambios = []
        try:
            proveedor = servicios.obtener_proveedor(self.conexion, id_proveedor)
            if proveedor["estado"] != "activo":
                raise ErrorNegocio("El proveedor está inactivo. Seleccione otro.")
            configuraciones = {l["id_producto"]: l for l in self.carrito}
            for identificador, linea in configuraciones.items():
                producto = servicios.obtener_producto(self.conexion, identificador)
                final = linea["nuevo_precio_venta"]
                if final is not None and final != producto["precio_venta"]:
                    cambios.append(f"{producto['nombre']}: {_dinero(producto['precio_venta'])} → {_dinero(final)}")
        except ErrorNegocio as error:
            messagebox.showwarning("Compra", str(error), parent=self)
            self.recargar_combos()
            return
        resumen = _resumen_carrito(self.carrito)
        aviso = "\n\nCambios de precio de venta:\n" + "\n".join(cambios[:5]) if cambios else ""
        if len(cambios) > 5:
            aviso += f"\nY {len(cambios)-5} producto(s) más."
        if not messagebox.askyesno("Confirmar compra",
                                   f"Proveedor: {proveedor['nombre']}\nProductos: {resumen['productos']}\n"
                                   f"Unidades: {resumen['unidades']}\nTotal a pagar: {_dinero(resumen['total'])}"
                                   f"{aviso}\n\n¿Registrar la compra?",
                                   parent=self):
            return

        items = [{
            "id_producto": l["id_producto"],
            "cantidad": l["cantidad"],
            "precio_compra": l["precio"],
            "nuevo_precio_venta": l["nuevo_precio_venta"],
            "nuevo_porcentaje_impuesto": l["nuevo_porcentaje_impuesto"],
            "nuevo_porcentaje_utilidad": l["nuevo_porcentaje_utilidad"],
        } for l in self.carrito]
        try:
            id_compra = servicios.registrar_compra(self.conexion, id_proveedor, items)
        except ErrorNegocio as error:
            # La transacción ya hizo ROLLBACK: el inventario y los precios no cambiaron.
            messagebox.showerror("No se pudo registrar la compra", str(error), parent=self)
            self.recargar_combos()
            return

        self.carrito.clear()
        self._actualizar_carrito()
        self.var_proveedor.set("")
        self.var_producto.set("")           # el stock y los precios cambiaron: se vuelve a elegir
        self.var_busqueda.set("")
        self.var_precio.set("")
        self.producto_actual = None
        self._precio_final_manual = False
        self._poner_precio_final("")
        self.var_impuesto.set("")
        self.var_utilidad.set("")
        self.var_conservar_precio.set(False)
        self.entrada_final.configure(state="normal")
        self._mostrar_info_vacia()
        self.recargar_combos()
        ui_admin.actualizar_historial_abierto(self)
        messagebox.showinfo("Compra registrada", f"Compra N.º {id_compra:06d}\n"
                            f"Total: {_dinero(resumen['total'])}\nEl inventario fue actualizado.", parent=self)

    def cerrar(self):
        if self.hay_cambios_sin_guardar() and not messagebox.askyesno(
                "Salir", "Hay una compra sin confirmar. ¿Desea salir y perder el carrito?", parent=self):
            return
        self.destroy()
