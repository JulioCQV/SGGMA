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

import tkinter as tk
from tkinter import ttk, messagebox

import servicios
from servicios import ErrorNegocio

MONEDA = "₡"


def abrir_ventana(padre, conexion):
    """Función que llama ui_principal.py; devuelve el Toplevel creado."""
    return VentanaCompras(padre, conexion)


def _dinero(valor):
    return f"{valor:,.2f}"


def _solo_digitos(texto_nuevo):
    return texto_nuevo == "" or (texto_nuevo.isdigit() and len(texto_nuevo) <= 6)


def _solo_decimal(texto_nuevo):
    """Permite dígitos y un separador decimal (punto o coma)."""
    if texto_nuevo == "":
        return True
    if len(texto_nuevo) > 12:
        return False
    texto = texto_nuevo.replace(",", ".")
    return (texto.count(".") <= 1 and texto.replace(".", "").isdigit()) or texto == "."


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
    except ValueError:
        raise ErrorNegocio(f"'{nombre_campo}' debe ser un número válido.")
    if numero < 0:
        raise ErrorNegocio(f"'{nombre_campo}' no puede ser negativo.")
    if maximo is not None and numero > maximo:
        raise ErrorNegocio(f"'{nombre_campo}' no puede ser mayor a {maximo:g}.")
    return numero


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
        self._precio_final_manual = False   # True si el usuario escribió el precio final
        self._autocompletando = False       # True mientras el programa escribe el precio final

        self.title("Registrar compra")
        self.geometry("980x700")
        self.minsize(860, 620)
        self.protocol("WM_DELETE_WINDOW", self.cerrar)

        self.var_proveedor = tk.StringVar()
        self.var_producto = tk.StringVar()
        self.var_cantidad = tk.StringVar(value="1")
        self.var_precio = tk.StringVar()                 # costo unitario real pagado
        self.var_impuesto = tk.StringVar()               # % impuesto editable
        self.var_utilidad = tk.StringVar()               # % utilidad editable
        self.var_precio_final = tk.StringVar()           # nuevo precio de venta final
        self.var_info = tk.StringVar()                   # "Precio Actual: ₡X | Sugerido: ₡Y"
        self.var_total = tk.StringVar(value=_dinero(0))
        self._mostrar_info_vacia()

        self._crear_seleccion()
        self._crear_carrito()
        self._crear_pie()
        self.recargar_combos()

        # Recalcula el precio sugerido en tiempo real (después de crear todos los widgets).
        for variable in (self.var_precio, self.var_impuesto, self.var_utilidad):
            variable.trace_add("write", lambda *_: self._recalcular_sugerido())
        self.var_precio_final.trace_add("write", lambda *_: self._al_cambiar_precio_final())
        self.combo_proveedor.focus_set()

    # ------------------------------------------------------------------
    # Construcción de la interfaz
    # ------------------------------------------------------------------
    def _crear_seleccion(self):
        marco = ttk.LabelFrame(self, text="Datos de la compra", padding=12)
        marco.pack(fill="x", padx=12, pady=(12, 6))
        for columna in (1, 3):
            marco.columnconfigure(columna, weight=1, uniform="campos")

        validar_entero = (self.register(_solo_digitos), "%P")
        validar_decimal = (self.register(_solo_decimal), "%P")

        # Fila 0-1: proveedor y producto
        ttk.Label(marco, text="Proveedor (*):").grid(row=0, column=0, sticky="w", pady=4)
        self.combo_proveedor = ttk.Combobox(marco, textvariable=self.var_proveedor, state="readonly",
                                            postcommand=self.recargar_combos)
        self.combo_proveedor.grid(row=0, column=1, columnspan=4, sticky="ew", padx=(8, 0), pady=4)

        ttk.Label(marco, text="Producto (*):").grid(row=1, column=0, sticky="w", pady=4)
        self.combo_producto = ttk.Combobox(marco, textvariable=self.var_producto, state="readonly",
                                           postcommand=self.recargar_combos)
        self.combo_producto.grid(row=1, column=1, columnspan=4, sticky="ew", padx=(8, 0), pady=4)
        self.combo_producto.bind("<<ComboboxSelected>>", lambda _e: self._al_seleccionar_producto())

        # Fila 2: cantidad y costo
        ttk.Label(marco, text="Cantidad (*):").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(marco, textvariable=self.var_cantidad, width=10, validate="key",
                  validatecommand=validar_entero).grid(row=2, column=1, sticky="w", padx=(8, 16), pady=4)

        ttk.Label(marco, text="Costo unitario (*):").grid(row=2, column=2, sticky="w", pady=4)
        ttk.Entry(marco, textvariable=self.var_precio, validate="key",
                  validatecommand=validar_decimal).grid(row=2, column=3, sticky="ew", padx=(8, 0), pady=4)

        # Fila 3: porcentajes editables
        ttk.Label(marco, text="% Impuesto:").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Entry(marco, textvariable=self.var_impuesto, width=10, validate="key",
                  validatecommand=validar_decimal).grid(row=3, column=1, sticky="w", padx=(8, 16), pady=4)

        ttk.Label(marco, text="% Utilidad:").grid(row=3, column=2, sticky="w", pady=4)
        ttk.Entry(marco, textvariable=self.var_utilidad, validate="key",
                  validatecommand=validar_decimal).grid(row=3, column=3, sticky="ew", padx=(8, 0), pady=4)

        # Fila 4: etiqueta informativa única
        estilo = ttk.Style(self)
        estilo.configure("Info.TLabel", font=("Segoe UI", 11, "bold"), foreground="#0a5c0a")
        ttk.Label(marco, textvariable=self.var_info, style="Info.TLabel").grid(
            row=4, column=0, columnspan=5, sticky="w", pady=(8, 4))

        # Fila 5: precio final y botón agregar
        ttk.Label(marco, text="Nuevo Precio de Venta Final:").grid(row=5, column=0, columnspan=2, sticky="w", pady=4)
        marco_final = ttk.Frame(marco)
        marco_final.grid(row=5, column=2, columnspan=2, sticky="ew", pady=4)
        marco_final.columnconfigure(0, weight=1)
        entrada_final = ttk.Entry(marco_final, textvariable=self.var_precio_final, validate="key",
                                  validatecommand=validar_decimal)
        entrada_final.grid(row=0, column=0, sticky="ew", padx=(8, 0))
        entrada_final.bind("<Return>", lambda _e: self.agregar_al_carrito())
        ttk.Button(marco_final, text="Actual", width=7, command=self.mantener_precio_actual).grid(
            row=0, column=1, padx=(6, 0))
        ttk.Button(marco, text="Agregar a la Compra", command=self.agregar_al_carrito).grid(
            row=5, column=4, sticky="e", padx=(12, 0), pady=4)

    def _crear_carrito(self):
        marco = ttk.LabelFrame(self, text="Carrito de compra", padding=8)
        marco.pack(fill="both", expand=True, padx=12, pady=6)
        marco.rowconfigure(0, weight=1)
        marco.columnconfigure(0, weight=1)

        columnas = ("producto", "precio", "cantidad", "subtotal", "pv_final", "imp", "util")
        encabezados = (("producto", "Producto", 230, "w"), ("precio", "Costo unit.", 90, "e"),
                       ("cantidad", "Cantidad", 70, "center"), ("subtotal", "Subtotal", 100, "e"),
                       ("pv_final", "P. Venta final", 100, "e"), ("imp", "% Imp.", 65, "center"),
                       ("util", "% Util.", 65, "center"))
        self.tabla = ttk.Treeview(marco, columns=columnas, show="headings", selectmode="browse", height=6)
        for columna, titulo, ancho, alineacion in encabezados:
            self.tabla.heading(columna, text=titulo)
            self.tabla.column(columna, width=ancho, anchor=alineacion)
        self.tabla.grid(row=0, column=0, sticky="nsew")

        scroll = ttk.Scrollbar(marco, orient="vertical", command=self.tabla.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tabla.configure(yscrollcommand=scroll.set)

        botones = ttk.Frame(marco)
        botones.grid(row=1, column=0, columnspan=2, sticky="e", pady=(8, 0))
        ttk.Button(botones, text="Quitar seleccionado", command=self.quitar_seleccionado).pack(side="left", padx=4)
        ttk.Button(botones, text="Vaciar carrito", command=self.vaciar_carrito).pack(side="left", padx=4)

    def _crear_pie(self):
        pie = ttk.Frame(self, padding=(12, 6, 12, 12))
        pie.pack(fill="x")
        estilo = ttk.Style(self)
        estilo.configure("Total.TLabel", font=("Segoe UI", 18, "bold"))
        ttk.Label(pie, text="Total de la compra:", style="Total.TLabel").pack(side="left")
        ttk.Label(pie, textvariable=self.var_total, style="Total.TLabel").pack(side="left", padx=10)
        ttk.Button(pie, text="Confirmar Compra", command=self.confirmar_compra).pack(side="right")

    # ------------------------------------------------------------------
    # Listas desplegables
    # ------------------------------------------------------------------
    def recargar_combos(self):
        """Proveedores y productos ACTIVOS. Se refresca al desplegar cada lista."""
        seleccion = self.var_proveedor.get()
        self.mapa_proveedores = {}
        for p in servicios.listar_proveedores(self.conexion):
            texto = p["nombre"]
            if texto in self.mapa_proveedores:
                texto = f"{texto} #{p['id_proveedor']}"
            self.mapa_proveedores[texto] = p["id_proveedor"]
        self.combo_proveedor["values"] = list(self.mapa_proveedores)
        if seleccion not in self.mapa_proveedores:
            self.var_proveedor.set("")

        seleccion = self.var_producto.get()
        self.mapa_productos = {}
        for p in servicios.listar_productos(self.conexion):
            texto = f"{p['nombre']} [{p['categoria']}] - Stock: {p['existencia']}"
            self.mapa_productos[texto] = p["id_producto"]
        self.combo_producto["values"] = list(self.mapa_productos)
        if seleccion not in self.mapa_productos:
            self.var_producto.set("")
            self.producto_actual = None
            self._precio_final_manual = False
            self._mostrar_info_vacia()

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
        texto_sugerido = f"{MONEDA}{_dinero(sugerido)}" if sugerido is not None else f"{MONEDA}-"
        self.var_info.set(f"Precio Actual: {MONEDA}{_dinero(self.producto_actual['precio_venta'])}"
                          f"  |  Sugerido: {texto_sugerido}")

    def _al_seleccionar_producto(self):
        id_producto = self.mapa_productos.get(self.var_producto.get())
        if id_producto is None:
            self.producto_actual = None
            self._mostrar_info_vacia()
            return
        self.producto_actual = servicios.obtener_producto(self.conexion, id_producto)
        self._cargar_datos_producto()

    def _cargar_datos_producto(self):
        """Autocompleta los % editables con los del producto y refresca la etiqueta."""
        p = self.producto_actual
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
        if sugerido is not None and not self._precio_final_manual:
            self._poner_precio_final(f"{sugerido:.2f}")

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
        self._precio_final_manual = True
        self._poner_precio_final(f"{self.producto_actual['precio_venta']:.2f}")

    # ------------------------------------------------------------------
    # Carrito
    # ------------------------------------------------------------------
    def agregar_al_carrito(self):
        id_producto = self.mapa_productos.get(self.var_producto.get())
        if id_producto is None:
            messagebox.showwarning("Compra", "Seleccione un producto.", parent=self)
            return
        try:
            cantidad = int(self.var_cantidad.get())
        except ValueError:
            cantidad = 0
        if cantidad < 1:
            messagebox.showwarning("Compra", "La cantidad debe ser un entero mayor o igual a 1.", parent=self)
            return
        try:
            costo = _leer_decimal(self.var_precio.get(), "Costo unitario", obligatorio=True)
            impuesto = _leer_decimal(self.var_impuesto.get(), "% Impuesto", maximo=100)
            utilidad = _leer_decimal(self.var_utilidad.get(), "% Utilidad")
            precio_final = _leer_decimal(self.var_precio_final.get(), "Nuevo Precio de Venta Final")
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

        # Mismo producto y mismo costo: se suman cantidades. Distinto costo: línea aparte.
        for linea in self.carrito:
            if linea["id_producto"] == id_producto and linea["precio"] == costo:
                linea["cantidad"] += cantidad
                linea["nuevo_precio_venta"] = precio_final
                linea["nuevo_porcentaje_impuesto"] = impuesto
                linea["nuevo_porcentaje_utilidad"] = utilidad
                linea["cambia"] = cambia
                break
        else:
            self.carrito.append({
                "id_producto": id_producto, "nombre": producto["nombre"],
                "precio": costo, "cantidad": cantidad,
                "nuevo_precio_venta": precio_final,
                "nuevo_porcentaje_impuesto": impuesto,
                "nuevo_porcentaje_utilidad": utilidad,
                "cambia": cambia,
            })

        # Preparar el formulario para la siguiente línea.
        self.var_cantidad.set("1")
        self.var_precio.set("")
        self.producto_actual = producto
        self._cargar_datos_producto()
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
                _dinero(pv) if pv is not None else "-",
                f"{imp:g}" if imp is not None else "-",
                f"{util:g}" if util is not None else "-"))
        self.var_total.set(_dinero(round(total, 2)))

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

        productos_con_cambio = {l["id_producto"] for l in self.carrito if l["cambia"]}
        aviso = (f"\nSe actualizará el precio de {len(productos_con_cambio)} producto(s)."
                 if productos_con_cambio else "")
        if not messagebox.askyesno("Confirmar compra",
                                   f"¿Registrar la compra por {MONEDA}{self.var_total.get()}?{aviso}",
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

        messagebox.showinfo("Compra", "Compra registrada.", parent=self)
        self.carrito.clear()
        self._actualizar_carrito()
        self.var_proveedor.set("")
        self.var_producto.set("")           # el stock y los precios cambiaron: se vuelve a elegir
        self.var_precio.set("")
        self.producto_actual = None
        self._precio_final_manual = False
        self._poner_precio_final("")
        self.var_impuesto.set("")
        self.var_utilidad.set("")
        self._mostrar_info_vacia()
        self.recargar_combos()

    def cerrar(self):
        if self.carrito and not messagebox.askyesno(
                "Salir", "Hay una compra sin confirmar. ¿Desea salir y perder el carrito?", parent=self):
            return
        self.destroy()