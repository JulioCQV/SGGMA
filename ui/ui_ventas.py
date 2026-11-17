"""
ui/ui_ventas.py
Módulo de Ventas: selección de cliente, carrito temporal y confirmación.

- El precio de venta se toma SIEMPRE de la base de datos (no es editable).
- El método de pago (Efectivo, Tarjeta o SINPE) se elige antes de confirmar y se guarda en la venta.
- El carrito vive en memoria; no toca la BD ni el inventario hasta "Confirmar Venta",
  que llama a servicios.registrar_venta (transacción con COMMIT / ROLLBACK).
"""

import re
import unicodedata
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox

import servicios
from servicios import ErrorNegocio
from ui import ui_admin

CLIENTE_GENERAL = "Cliente General"
METODOS_PAGO = list(servicios.METODOS_PAGO)
METODO_PAGO_INICIAL = servicios.METODO_PAGO_POR_DEFECTO


def abrir_ventana(padre, conexion):
    return VentanaVentas(padre, conexion)


def _dinero(valor):
    return f"₡{valor:,.2f}"


def _normalizar_busqueda(texto):
    return "".join(c for c in unicodedata.normalize("NFD", texto.casefold())
                   if not unicodedata.combining(c))


def _filtrar_productos(productos, busqueda, carrito):
    palabras = _normalizar_busqueda(busqueda).split()
    resultados = []
    for producto in productos:
        disponible = producto["existencia"] - carrito.get(
            producto["id_producto"], {}).get("cantidad", 0)
        texto = _normalizar_busqueda(producto["nombre"] + " " + producto["categoria"])
        if (producto["estado"] == "activo" and disponible > 0
                and all(palabra in texto for palabra in palabras)):
            resultados.append((producto, disponible))
    return resultados


def _leer_monto(texto):
    texto = texto.strip()
    if not texto:
        raise ErrorNegocio("Ingrese el monto recibido en efectivo.")
    if not re.fullmatch(r"(?:[0-9]+(?:[.,][0-9]{0,2})?|[.,][0-9]{1,2})", texto):
        raise ErrorNegocio("Escriba un monto válido, con hasta dos decimales y sin separador de miles.")
    return texto.replace(",", ".")


class VentanaVentas(tk.Toplevel):
    def __init__(self, padre, conexion):
        super().__init__(padre)
        self.conexion = conexion
        self.carrito = {}
        self.mapa_clientes = {}
        self.mapa_productos = {}
        self.productos = []
        self.total_actual = 0.0
        self.ruta_ultimo_ticket = None
        self.id_ultima_venta = None

        self.title("GeMMa | Registrar venta")
        ancho = min(1080, self.winfo_screenwidth() - 60)
        alto = min(760, self.winfo_screenheight() - 100)
        self.geometry(f"{ancho}x{alto}")
        self.minsize(min(900, ancho), min(650, alto))
        self.protocol("WM_DELETE_WINDOW", self.cerrar)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        self.var_cliente = tk.StringVar(value=CLIENTE_GENERAL)
        self.var_metodo_pago = tk.StringVar(value=METODO_PAGO_INICIAL)
        self.var_busqueda = tk.StringVar()
        self.var_producto = tk.StringVar()
        self.var_cantidad = tk.StringVar(value="1")
        self.var_precio = tk.StringVar(value="Precio unitario: —")
        self.var_resultados = tk.StringVar()
        self.var_total = tk.StringVar(value=_dinero(0))
        self.var_recibido = tk.StringVar()
        self.var_vuelto = tk.StringVar(value="—")
        self.var_estado_cobro = tk.StringVar()
        self.var_ultima_venta = tk.StringVar()

        cabecera = ttk.Frame(self, padding=(16, 10, 16, 0))
        cabecera.grid(row=0, column=0, sticky="ew")
        ttk.Label(cabecera, text="Registrar venta", font=("Segoe UI", 18, "bold")).pack(side="left")
        ttk.Button(cabecera, text="Ver historial", style="Secundario.TButton",
                   command=self.abrir_historial).pack(side="right")
        self._crear_seleccion()
        self._crear_carrito()
        self._crear_cobro()
        self._crear_ultimo_ticket()
        self.var_busqueda.trace_add("write", lambda *_: self._filtrar_lista())
        self.var_recibido.trace_add("write", lambda *_: self._actualizar_cobro())
        self.var_metodo_pago.trace_add("write", lambda *_: self._actualizar_cobro())
        self.recargar_combos()
        self._actualizar_cobro()
        self._actualizar_ultima_venta()
        self.entrada_busqueda.focus_set()

    def _crear_seleccion(self):
        marco = ttk.LabelFrame(self, text="Productos y cliente", padding=10)
        marco.grid(row=1, column=0, sticky="ew", padx=16, pady=(8, 6))
        marco.columnconfigure(1, weight=1)
        marco.columnconfigure(2, weight=1)
        ttk.Label(marco, text="Cliente:").grid(row=0, column=0, sticky="w", pady=3)
        self.combo_cliente = ttk.Combobox(marco, textvariable=self.var_cliente, state="readonly",
                                          postcommand=self.recargar_combos)
        self.combo_cliente.grid(row=0, column=1, columnspan=3, sticky="ew", padx=(8, 0), pady=3)
        ttk.Label(marco, text="Buscar producto:").grid(row=1, column=0, sticky="w", pady=3)
        self.entrada_busqueda = ttk.Entry(marco, textvariable=self.var_busqueda)
        self.entrada_busqueda.grid(row=1, column=1, columnspan=2, sticky="ew", padx=8, pady=3)
        self.entrada_busqueda.bind("<Return>", self._ir_a_producto)
        ttk.Button(marco, text="Actualizar lista", command=self.recargar_combos).grid(
            row=1, column=3, sticky="ew", pady=3)
        ttk.Label(marco, text="Producto:").grid(row=2, column=0, sticky="w", pady=3)
        self.combo_producto = ttk.Combobox(marco, textvariable=self.var_producto, state="readonly",
                                           postcommand=self.recargar_combos)
        self.combo_producto.grid(row=2, column=1, columnspan=3, sticky="ew", padx=(8, 0), pady=3)
        self.combo_producto.bind("<<ComboboxSelected>>", lambda _e: self._mostrar_precio())
        ttk.Label(marco, text="Cantidad:").grid(row=3, column=0, sticky="w", pady=3)
        validacion = (self.register(lambda t: t == "" or bool(re.fullmatch(r"[0-9]{1,6}", t))), "%P")
        self.entrada_cantidad = ttk.Entry(marco, textvariable=self.var_cantidad, width=8,
                                          validate="key", validatecommand=validacion)
        self.entrada_cantidad.grid(row=3, column=1, sticky="w", padx=8, pady=3)
        self.entrada_cantidad.bind("<Return>", lambda _e: self.agregar_al_carrito())
        ttk.Label(marco, textvariable=self.var_precio).grid(row=3, column=2, sticky="w", padx=6)
        ttk.Button(marco, text="Agregar a la venta", command=self.agregar_al_carrito).grid(
            row=3, column=3, sticky="ew", pady=3)
        ttk.Label(marco, textvariable=self.var_resultados, foreground="#58685F").grid(
            row=4, column=0, columnspan=4, sticky="w", pady=(5, 0))

    def _crear_carrito(self):
        marco = ttk.LabelFrame(self, text="Productos de la venta", padding=8)
        marco.grid(row=2, column=0, sticky="nsew", padx=16, pady=6)
        marco.rowconfigure(0, weight=1)
        marco.columnconfigure(0, weight=1)
        columnas = ("producto", "precio", "cantidad", "subtotal")
        self.tabla = ttk.Treeview(marco, columns=columnas, show="headings", selectmode="browse", height=5)
        for columna, titulo, ancho, alineacion in (
                ("producto", "Producto", 330, "w"), ("precio", "Precio unitario", 135, "e"),
                ("cantidad", "Cantidad", 90, "center"), ("subtotal", "Subtotal", 135, "e")):
            self.tabla.heading(columna, text=titulo)
            self.tabla.column(columna, width=ancho, anchor=alineacion)
        self.tabla.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(marco, orient="vertical", command=self.tabla.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tabla.configure(yscrollcommand=scroll.set)
        botones = ttk.Frame(marco)
        botones.grid(row=1, column=0, columnspan=2, sticky="e", pady=(6, 0))
        ttk.Button(botones, text="Quitar seleccionado", style="Secundario.TButton",
                   command=self.quitar_seleccionado).pack(side="left", padx=4)
        ttk.Button(botones, text="Vaciar carrito", style="Secundario.TButton",
                   command=self.vaciar_carrito).pack(side="left", padx=4)

    def _crear_cobro(self):
        marco = ttk.LabelFrame(self, text="Cobro", padding=10)
        marco.grid(row=3, column=0, sticky="ew", padx=16, pady=6)
        marco.columnconfigure(1, weight=1)
        marco.columnconfigure(2, weight=1)
        ttk.Label(marco, text="Método de pago:").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Combobox(marco, textvariable=self.var_metodo_pago, values=METODOS_PAGO,
                     state="readonly", width=14).grid(row=0, column=1, sticky="w", padx=8, pady=3)
        ttk.Label(marco, text="Total a pagar", font=("Segoe UI", 11)).grid(
            row=0, column=2, sticky="e", padx=8)
        ttk.Label(marco, textvariable=self.var_total, font=("Segoe UI", 23, "bold"),
                  foreground="#205C49").grid(row=1, column=2, sticky="e", padx=8)
        ttk.Label(marco, text="Efectivo recibido:").grid(row=1, column=0, sticky="w", pady=3)
        validar_monto = (self.register(lambda t: len(t) <= 18 and bool(
            re.fullmatch(r"[0-9]*(?:[.,][0-9]{0,2})?", t))), "%P")
        self.entrada_recibido = ttk.Entry(marco, textvariable=self.var_recibido, width=18,
                                          validate="key", validatecommand=validar_monto)
        self.entrada_recibido.grid(row=1, column=1, sticky="w", padx=8, pady=3)
        self.entrada_recibido.bind("<Return>", lambda _e: self.confirmar_venta())
        ttk.Label(marco, text="Vuelto:").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Label(marco, textvariable=self.var_vuelto, font=("Segoe UI", 13, "bold")).grid(
            row=2, column=1, sticky="w", padx=8)
        self.boton_confirmar = ttk.Button(marco, text="Confirmar venta", command=self.confirmar_venta)
        self.boton_confirmar.grid(row=2, column=2, sticky="e", padx=8)
        self.etiqueta_cobro = ttk.Label(marco, textvariable=self.var_estado_cobro)
        self.etiqueta_cobro.grid(row=3, column=0, columnspan=3, sticky="w", pady=(4, 0))

    def _crear_ultimo_ticket(self):
        pie = ttk.Frame(self, padding=(16, 2, 16, 10))
        pie.grid(row=4, column=0, sticky="ew")
        pie.columnconfigure(0, weight=1)
        ttk.Label(pie, textvariable=self.var_ultima_venta, wraplength=650).grid(
            row=0, column=0, sticky="w", padx=(0, 10))
        self.boton_ticket = ttk.Button(pie, text="Ver último ticket", style="Secundario.TButton",
                                       state="disabled", command=self.ver_ultimo_ticket)
        self.boton_ticket.grid(row=0, column=1, sticky="e")

    def abrir_historial(self):
        ui_admin.abrir_historial(self, self.conexion, "venta")

    def actualizar_al_volver(self):
        self.recargar_combos()
        self._actualizar_ultima_venta()
        ui_admin.actualizar_historial_abierto(self)

    def hay_cambios_sin_guardar(self):
        return bool(self.carrito or self.var_recibido.get().strip())

    def actualizar_al_volver(self):
        self.recargar_combos()
        self._actualizar_ultima_venta()
        ui_admin.actualizar_historial_abierto(self)

    def hay_cambios_sin_guardar(self):
        return bool(self.carrito or self.var_recibido.get().strip())

    def _actualizar_ultima_venta(self):
        venta = servicios.obtener_ultima_venta(self.conexion)
        self.id_ultima_venta = venta["id_venta"] if venta is not None else None
        if venta is None:
            self.var_ultima_venta.set("No hay ventas registradas. Los comprobantes aparecerán aquí y en el historial.")
        else:
            estado = " · ANULADA" if venta["anulada"] else ""
            self.var_ultima_venta.set(f"Última venta registrada: N.º {venta['id_venta']:06d}{estado}.")
        self.boton_ticket.configure(state="normal" if venta is not None else "disabled")

    def recargar_combos(self):
        cliente_actual = self.var_cliente.get()
        cliente_id = self.mapa_clientes.get(cliente_actual)
        self.mapa_clientes = {CLIENTE_GENERAL: None}
        for c in servicios.listar_clientes(self.conexion):
            texto = c["nombre"] + (f" ({c['telefono']})" if c["telefono"] else "")
            if texto in self.mapa_clientes:
                texto += f" #{c['id_cliente']}"
            self.mapa_clientes[texto] = c["id_cliente"]
        self.combo_cliente["values"] = list(self.mapa_clientes)
        if cliente_actual == CLIENTE_GENERAL:
            self.var_cliente.set(CLIENTE_GENERAL)
        elif cliente_id is not None:
            self.var_cliente.set(next((texto for texto, identificador in self.mapa_clientes.items()
                                       if identificador == cliente_id), ""))
        else:
            self.var_cliente.set("")
        self.productos = servicios.listar_productos(self.conexion)
        self._filtrar_lista()

    def _filtrar_lista(self):
        seleccionado = self.mapa_productos.get(self.var_producto.get())
        resultados = _filtrar_productos(self.productos, self.var_busqueda.get(), self.carrito)
        self.mapa_productos = {
            f"{p['nombre']} [{p['categoria']}] · Disp.: {disponible} · #{p['id_producto']}": p["id_producto"]
            for p, disponible in resultados}
        self.combo_producto["values"] = list(self.mapa_productos)
        texto = next((t for t, identificador in self.mapa_productos.items()
                      if identificador == seleccionado), "")
        if not texto and len(resultados) == 1:
            texto = next(iter(self.mapa_productos))
        self.var_producto.set(texto)
        self._mostrar_precio()
        cantidad = len(resultados)
        self.var_resultados.set(
            f"{cantidad} producto(s) disponible(s). Busque por nombre o categoría."
            if cantidad else "No hay productos disponibles para esta búsqueda. Revise el nombre o actualice la lista.")

    def _ir_a_producto(self, _evento=None):
        if self.var_producto.get():
            self.entrada_cantidad.focus_set()
            self.entrada_cantidad.selection_range(0, "end")
        else:
            self.combo_producto.focus_set()
        return "break"

    def _mostrar_precio(self):
        identificador = self.mapa_productos.get(self.var_producto.get())
        producto = next((p for p in self.productos if p["id_producto"] == identificador), None)
        self.var_precio.set("Precio unitario: " + (_dinero(producto["precio_venta"]) if producto else "—"))

    def agregar_al_carrito(self):
        identificador = self.mapa_productos.get(self.var_producto.get())
        if identificador is None:
            messagebox.showwarning("Venta", "Seleccione un producto.", parent=self)
            return
        try:
            texto = self.var_cantidad.get()
            if not re.fullmatch(r"[0-9]{1,6}", texto) or int(texto) < 1:
                raise ErrorNegocio("La cantidad debe ser un entero mayor que cero.")
            cantidad = int(texto)
            producto = servicios.obtener_producto(self.conexion, identificador)
            if producto["estado"] != "activo":
                raise ErrorNegocio("El producto seleccionado está inactivo.")
            en_carrito = self.carrito.get(identificador, {}).get("cantidad", 0)
            if en_carrito + cantidad > producto["existencia"]:
                raise ErrorNegocio(f"Existencia insuficiente. Disponible para agregar: "
                                   f"{max(0, producto['existencia'] - en_carrito)}.")
        except ErrorNegocio as error:
            messagebox.showwarning("Venta", str(error), parent=self)
            self.recargar_combos()
            return
        self.carrito[identificador] = {"nombre": producto["nombre"],
                                       "precio": producto["precio_venta"],
                                       "cantidad": en_carrito + cantidad}
        self.var_cantidad.set("1")
        self._actualizar_carrito()
        self.recargar_combos()
        self.entrada_busqueda.focus_set()
        self.entrada_busqueda.selection_range(0, "end")

    def quitar_seleccionado(self):
        seleccion = self.tabla.selection()
        if not seleccion:
            messagebox.showinfo("Venta", "Seleccione una línea del carrito.", parent=self)
            return
        self.carrito.pop(int(seleccion[0]), None)
        self._actualizar_carrito()
        self.recargar_combos()

    def vaciar_carrito(self):
        if self.carrito and not messagebox.askyesno("Confirmar", "¿Desea vaciar el carrito?", parent=self):
            return
        self.carrito.clear()
        self.var_recibido.set("")
        self._actualizar_carrito()
        self.recargar_combos()

    def _actualizar_carrito(self):
        for fila in self.tabla.get_children():
            self.tabla.delete(fila)
        subtotales = []
        for identificador, linea in self.carrito.items():
            subtotal = round(linea["precio"] * linea["cantidad"], 2)
            subtotales.append(subtotal)
            self.tabla.insert("", "end", iid=str(identificador), values=(
                linea["nombre"], _dinero(linea["precio"]), linea["cantidad"], _dinero(subtotal)))
        self.total_actual = round(sum(subtotales), 2)
        self.var_total.set(_dinero(self.total_actual))
        self._actualizar_cobro()

    def _obtener_cobro(self):
        metodo = self.var_metodo_pago.get()
        recibido = _leer_monto(self.var_recibido.get()) if metodo == "Efectivo" else None
        return servicios.calcular_cobro(self.total_actual, metodo, recibido)

    def _actualizar_cobro(self):
        efectivo = self.var_metodo_pago.get() == "Efectivo"
        self.entrada_recibido.configure(state="normal" if efectivo else "disabled")
        valido = False
        color = "#58685F"
        if not self.carrito:
            mensaje = "Agregue productos para preparar el cobro."
            self.var_vuelto.set("—" if efectivo else "No aplica")
        else:
            try:
                cobro = self._obtener_cobro()
                self.var_vuelto.set(_dinero(cobro["vuelto"]) if efectivo else "No aplica")
                mensaje = "Efectivo suficiente. Puede confirmar la venta." if efectivo else "Puede confirmar el pago con " + self.var_metodo_pago.get() + "."
                color, valido = "#205C49", True
            except ErrorNegocio as error:
                self.var_vuelto.set("—")
                mensaje, color = str(error), "#A32932"
        self.var_estado_cobro.set(mensaje)
        self.etiqueta_cobro.configure(foreground=color)
        self.boton_confirmar.configure(state="normal" if valido else "disabled")

    def _datos_cambiaron(self):
        cambio = False
        for identificador, linea in self.carrito.items():
            producto = servicios.obtener_producto(self.conexion, identificador)
            if linea["precio"] != producto["precio_venta"] or linea["nombre"] != producto["nombre"]:
                linea["precio"], linea["nombre"] = producto["precio_venta"], producto["nombre"]
                cambio = True
        if cambio:
            self._actualizar_carrito()
        return cambio

    def confirmar_venta(self):
        if not self.carrito:
            messagebox.showwarning("Venta", "El carrito está vacío.", parent=self)
            return
        try:
            if self._datos_cambiaron():
                messagebox.showinfo("Datos actualizados", "Cambió el nombre o precio de algún producto.\n"
                                    "Revise el carrito y el cobro antes de confirmar otra vez.", parent=self)
                return
            cobro = self._obtener_cobro()
            if self.var_cliente.get() not in self.mapa_clientes:
                raise ErrorNegocio("Seleccione nuevamente el cliente.")
        except ErrorNegocio as error:
            messagebox.showwarning("Revisar venta", str(error), parent=self)
            return
        metodo = self.var_metodo_pago.get()
        resumen = f"Total: {_dinero(self.total_actual)}\nMétodo: {metodo}"
        if metodo == "Efectivo":
            resumen += f"\nRecibido: {_dinero(cobro['monto_recibido'])}\nVuelto: {_dinero(cobro['vuelto'])}"
        if not messagebox.askyesno("Confirmar venta", resumen + "\n\n¿Registrar la venta?", parent=self):
            return
        try:
            venta = servicios.registrar_venta(self.conexion,
                [(identificador, linea["cantidad"]) for identificador, linea in self.carrito.items()],
                self.mapa_clientes[self.var_cliente.get()], metodo_pago=metodo,
                monto_recibido=cobro["monto_recibido"], total_esperado=self.total_actual)
        except ErrorNegocio as error:
            messagebox.showerror("No se pudo registrar la venta", str(error), parent=self)
            self.recargar_combos()
            return

        # Una venta confirmada no se repite si después falla la generación del ticket.
        self.carrito.clear()
        self.var_cliente.set(CLIENTE_GENERAL)
        self.var_recibido.set("")
        self.var_metodo_pago.set(METODO_PAGO_INICIAL)
        self.var_busqueda.set("")
        self.var_cantidad.set("1")
        self._actualizar_carrito()
        self.recargar_combos()
        self.ruta_ultimo_ticket = None
        self._actualizar_ultima_venta()
        ui_admin.actualizar_historial_abierto(self)
        try:
            carpeta = str(Path(servicios.CARPETA_PROYECTO) / "tickets")
            self.ruta_ultimo_ticket = servicios.generar_ticket_venta(self.conexion, venta, carpeta)
            messagebox.showinfo("Venta registrada", resumen + "\n\nPuede consultar el comprobante en «Ver último ticket».", parent=self)
        except ErrorNegocio as error:
            messagebox.showwarning("Venta registrada", "El cobro quedó registrado.\n"
                                   f"No se pudo generar el ticket:\n{error}", parent=self)

    def ver_ultimo_ticket(self):
        # Se consulta la BD de nuevo: el archivo anterior puede haber quedado desactualizado.
        self._actualizar_ultima_venta()
        if self.id_ultima_venta is not None:
            ui_admin.mostrar_ticket(self, self.conexion, self.id_ultima_venta)

    def cerrar(self):
        if self.hay_cambios_sin_guardar() and not messagebox.askyesno("Salir",
                "Hay una venta sin confirmar. ¿Desea salir y perder el carrito?", parent=self):
            return
        self.destroy()
