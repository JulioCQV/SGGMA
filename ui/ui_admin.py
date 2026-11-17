"""
ui/ui_admin.py
Módulo Admin (CRUD 5): historial de ventas y compras con edición y anulación.

- Los historiales abiertos desde Compras/Ventas permiten consultar sin contraseña.
- Para editar o anular se requiere la contraseña maestra.
- El acceso es temporal: se pierde al cerrar esta ventana.
- Editar / Anular ajustan el inventario automáticamente (transaccional, en servicios.py):
    * anular venta  -> devuelve el stock      * anular compra -> resta el stock
    * editar        -> ajusta el stock según la diferencia de cantidades
  Anular NO borra: el registro queda marcado como ANULADA y se conserva para auditoría.
  Una transacción anulada no se puede editar ni anular otra vez.
  Si el ajuste dejaría stock negativo, servicios.py rechaza la operación.
"""

import math
import re
import sqlite3
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

import servicios
from servicios import ErrorNegocio
from ui import formato


def abrir_ventana(padre, conexion):
    """Función que llama ui_principal.py; devuelve el Toplevel creado."""
    return VentanaAdmin(padre, conexion)


def abrir_historial(padre, conexion, tipo):
    """Abre una sola ventana de consulta por módulo; nunca habilita edición sola."""
    existente = getattr(padre, "ventana_historial", None)
    if existente is not None and existente.winfo_exists():
        existente.pestana.actualizar()
        existente.deiconify()
        existente.lift()
        existente.focus_set()
        return existente
    ventana = VentanaHistorial(padre, conexion, tipo)
    padre.ventana_historial = ventana
    return ventana


def actualizar_historial_abierto(padre):
    ventana = getattr(padre, "ventana_historial", None)
    if ventana is not None and ventana.winfo_exists():
        ventana.pestana.actualizar()


def mostrar_ticket(padre, conexion, id_venta):
    """Regenera el comprobante desde la BD al consultarlo, incluso si fue anulado."""
    try:
        carpeta = str(Path(servicios.CARPETA_PROYECTO) / "tickets")
        ruta = servicios.generar_ticket_venta(conexion, id_venta, carpeta)
        contenido = Path(ruta).read_text(encoding="utf-8")
    except (ErrorNegocio, OSError, sqlite3.Error) as error:
        messagebox.showerror("Ticket", f"No se pudo generar el comprobante:\n{error}", parent=padre)
        return None
    ventana = tk.Toplevel(padre)
    ventana.title(f"GeMMa | Ticket de venta N.º {id_venta:06d}")
    ventana.geometry(f"{min(650, ventana.winfo_screenwidth()-60)}x{min(620, ventana.winfo_screenheight()-100)}")
    ventana.rowconfigure(0, weight=1)
    ventana.columnconfigure(0, weight=1)
    texto = tk.Text(ventana, font=("Courier New", 11), wrap="none", padx=12, pady=12,
                    background="white", foreground="#243D32")
    texto.grid(row=0, column=0, sticky="nsew")
    vertical = ttk.Scrollbar(ventana, command=texto.yview)
    vertical.grid(row=0, column=1, sticky="ns")
    horizontal = ttk.Scrollbar(ventana, orient="horizontal", command=texto.xview)
    horizontal.grid(row=1, column=0, sticky="ew")
    texto.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
    texto.insert("1.0", contenido)
    texto.configure(state="disabled")
    ttk.Button(ventana, text="Cerrar", command=ventana.destroy).grid(row=2, column=0, pady=8)
    return ventana


def _dinero(valor):
    return f"{valor:,.2f}"


# Diferencias entre ventas y compras, para reutilizar la misma pestaña.
CONFIG = {
    "venta": {
        "titulo": "venta", "titulo_pestana": "Historial de Ventas", "columna_entidad": "Cliente",
        "campo_id": "id_venta", "campo_entidad": "cliente", "campo_id_detalle": "id_detalle_venta",
        "listar": servicios.listar_ventas, "detalle": servicios.obtener_detalle_venta,
        "eliminar": servicios.eliminar_venta, "editar": servicios.editar_venta,
        "aviso_anular": "Se DEVOLVERÁ al inventario el stock de los productos vendidos.",
    },
    "compra": {
        "titulo": "compra", "titulo_pestana": "Historial de Compras", "columna_entidad": "Proveedor",
        "campo_id": "id_compra", "campo_entidad": "proveedor", "campo_id_detalle": "id_detalle_compra",
        "listar": servicios.listar_compras, "detalle": servicios.obtener_detalle_compra,
        "eliminar": servicios.eliminar_compra, "editar": servicios.editar_compra,
        "aviso_anular": "Se RESTARÁ del inventario el stock que aportó esta compra.",
    },
}


def _solo_digitos(texto_nuevo):
    return bool(re.fullmatch(r"[0-9]{0,9}", texto_nuevo))


def _solo_decimal(texto_nuevo):
    return len(texto_nuevo) <= 18 and bool(re.fullmatch(r"[0-9]*(?:[.,][0-9]{0,2})?", texto_nuevo))


def _leer_precio(texto, nombre="Precio unitario"):
    texto = texto.strip().replace(",", ".")
    if not re.fullmatch(r"(?:[0-9]+(?:\.[0-9]{0,2})?|\.[0-9]{1,2})", texto):
        raise ErrorNegocio(f"{nombre}: escriba un monto válido sin separador de miles.")
    numero = round(float(texto), 2)
    if not math.isfinite(numero):
        raise ErrorNegocio(f"{nombre}: el monto supera el máximo permitido.")
    return numero


# ======================================================================
# Ventana principal del módulo (contraseña + pestañas)
# ======================================================================
class VentanaAdmin(tk.Toplevel):
    def __init__(self, padre, conexion):
        super().__init__(padre)
        self.conexion = conexion
        self.title("Administración - Historial de transacciones")
        self.geometry("900x700")
        self.minsize(780, 600)

        self.var_clave = tk.StringVar()
        self.pestanas_historial = []
        self._mostrar_login()

    def actualizar_al_volver(self):
        for pestana in self.pestanas_historial:
            pestana.actualizar()

    # ------------------------------------------------------------------
    # Autenticación
    # ------------------------------------------------------------------
    def _mostrar_login(self):
        self.marco_login = ttk.Frame(self, padding=30)
        self.marco_login.pack(expand=True)

        ttk.Label(self.marco_login, text="Acceso de administrador",
                  font=("Segoe UI", 16, "bold")).pack(pady=(0, 6))
        ttk.Label(self.marco_login,
                  text="Ingrese la contraseña maestra para ver y\ncorregir el historial de transacciones.",
                  justify="center").pack(pady=(0, 14))

        self.entrada_clave = ttk.Entry(self.marco_login, textvariable=self.var_clave,
                                       show="*", width=28)   # show="*": la clave no se ve
        self.entrada_clave.pack(pady=4)
        self.entrada_clave.bind("<Return>", lambda _e: self.validar_clave())
        self.entrada_clave.focus_set()

        botones = ttk.Frame(self.marco_login)
        botones.pack(pady=(12, 0))
        ttk.Button(botones, text="Ingresar", command=self.validar_clave).pack(side="left", padx=4)
        ttk.Button(botones, text="Cancelar", command=self.destroy).pack(side="left", padx=4)

    def validar_clave(self):
        clave = self.var_clave.get()
        if not clave:
            messagebox.showwarning("Acceso", "Ingrese la contraseña.", parent=self)
            return
        if not servicios.verificar_clave_admin(clave):
            messagebox.showerror("Acceso denegado", "Contraseña incorrecta.", parent=self)
            self.var_clave.set("")
            self.entrada_clave.focus_set()
            return
        self.var_clave.set("")
        self.marco_login.destroy()
        self._mostrar_pestanas()

    # ------------------------------------------------------------------
    # Pestañas (solo tras autenticarse)
    # ------------------------------------------------------------------
    def _mostrar_pestanas(self):
        ttk.Label(self, text="Administración de transacciones. Los permisos se pierden al cerrar esta ventana.",
                  foreground="#a05a00").pack(anchor="w", padx=12, pady=(10, 0))
        cuaderno = ttk.Notebook(self)
        cuaderno.pack(fill="both", expand=True, padx=12, pady=12)
        for tipo in ("venta", "compra"):
            pestana = PestanaHistorial(cuaderno, self.conexion, tipo, modo_admin=True)
            self.pestanas_historial.append(pestana)
            cuaderno.add(pestana, text=CONFIG[tipo]["titulo_pestana"])


# ======================================================================
# Pestaña genérica de historial (se usa para ventas y para compras)
# ======================================================================
class VentanaHistorial(tk.Toplevel):
    def __init__(self, padre, conexion, tipo):
        super().__init__(padre)
        self.title("GeMMa | " + CONFIG[tipo]["titulo_pestana"])
        ancho = min(1000, self.winfo_screenwidth() - 60)
        alto = min(720, self.winfo_screenheight() - 100)
        self.geometry(f"{ancho}x{alto}")
        self.minsize(min(780, ancho), min(540, alto))
        self.pestana = PestanaHistorial(self, conexion, tipo)
        self.pestana.pack(fill="both", expand=True, padx=8, pady=8)

    def actualizar_al_volver(self):
        self.pestana.actualizar()


class PestanaHistorial(ttk.Frame):
    def __init__(self, padre, conexion, tipo, modo_admin=False):
        super().__init__(padre, padding=8)
        self.conexion = conexion
        self.tipo = tipo
        self.cfg = CONFIG[tipo]
        self.modo_admin = modo_admin
        self.mapa_entidades = {"Todos": None}
        self.filas_por_id = {}
        self.var_busqueda = tk.StringVar()
        self.var_desde = tk.StringVar()
        self.var_hasta = tk.StringVar()
        self.var_entidad = tk.StringVar(value="Todos")
        self.var_incluir_anuladas = tk.BooleanVar(value=True)
        self.var_resultados = tk.StringVar()
        self.var_cobro = tk.StringVar(value="Seleccione una operación para consultar su detalle.")
        self.var_modo = tk.StringVar()
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=3)
        self.rowconfigure(3, weight=2)
        self._crear_filtros()
        self._crear_tabla_principal()
        self._crear_detalle()
        self._crear_botones()
        self.actualizar()

    def _crear_filtros(self):
        barra = ttk.LabelFrame(self, text="Filtros del historial", padding=8)
        barra.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        for columna in (1, 3):
            barra.columnconfigure(columna, weight=1)
        ttk.Label(barra, text="Buscar (N.º, nombre o fecha):").grid(row=0, column=0, sticky="w", pady=3)
        entrada = ttk.Entry(barra, textvariable=self.var_busqueda)
        entrada.grid(row=0, column=1, columnspan=3, sticky="ew", padx=6, pady=3)
        entrada.bind("<Return>", lambda _e: self.actualizar())
        ttk.Button(barra, text="Buscar", command=self.actualizar).grid(row=0, column=4, padx=3)
        ttk.Button(barra, text="Limpiar", style="Secundario.TButton",
                   command=self.limpiar_filtros).grid(row=0, column=5, padx=3)
        for columna, etiqueta, variable in ((0, "Desde (DD/MM/AAAA):", self.var_desde),
                                               (2, "Hasta (DD/MM/AAAA):", self.var_hasta)):
            ttk.Label(barra, text=etiqueta).grid(row=1, column=columna, sticky="w", pady=3)
            fecha = ttk.Entry(barra, textvariable=variable, width=12)
            fecha.grid(row=1, column=columna+1, sticky="ew", padx=6, pady=3)
            fecha.bind("<Return>", lambda _e: self.actualizar())
        ttk.Button(barra, text="Actualizar", command=self.actualizar).grid(
            row=1, column=4, columnspan=2, sticky="ew", padx=3)
        ttk.Label(barra, text=self.cfg["columna_entidad"] + ":").grid(row=2, column=0, sticky="w", pady=3)
        self.combo_entidad = ttk.Combobox(barra, textvariable=self.var_entidad, state="readonly")
        self.combo_entidad.grid(row=2, column=1, columnspan=3, sticky="ew", padx=6, pady=3)
        self.combo_entidad.bind("<<ComboboxSelected>>", lambda _e: self.cargar_tabla())
        ttk.Checkbutton(barra, text="Incluir anuladas", variable=self.var_incluir_anuladas,
                        command=self.cargar_tabla).grid(row=2, column=4, columnspan=2, sticky="w", padx=3)
        ttk.Label(self, textvariable=self.var_resultados, foreground="#58685F").grid(
            row=1, column=0, sticky="w", pady=(0, 4))

    def _recargar_entidades(self):
        elegido = self.mapa_entidades.get(self.var_entidad.get())
        mapa = {"Todos": None}
        if self.tipo == "venta":
            mapa["Cliente General"] = "general"
        campo = "id_cliente" if self.tipo == "venta" else "id_proveedor"
        vistos = set()
        for fila in self.cfg["listar"](self.conexion):
            identificador = fila[campo]
            if identificador is None or identificador in vistos:
                continue
            vistos.add(identificador)
            texto = f"{fila[self.cfg['campo_entidad']]} (#{identificador})"
            mapa[texto] = identificador
        self.mapa_entidades = mapa
        self.combo_entidad["values"] = list(mapa)
        self.var_entidad.set(next((texto for texto, valor in mapa.items() if valor == elegido), "Todos"))

    def _opciones_filtros(self):
        opciones = {
            "fecha_inicio": formato.a_fecha_iso(self.var_desde.get()),
            "fecha_fin": formato.a_fecha_iso(self.var_hasta.get()),
            "incluir_anuladas": self.var_incluir_anuladas.get(),
        }
        if self.var_entidad.get() not in self.mapa_entidades:
            raise ErrorNegocio("Seleccione nuevamente el cliente o proveedor del filtro.")
        entidad = self.mapa_entidades[self.var_entidad.get()]
        if self.tipo == "venta":
            opciones["id_cliente"] = None if entidad == "general" else entidad
            opciones["solo_cliente_general"] = entidad == "general"
        else:
            opciones["id_proveedor"] = entidad
        return opciones

    def actualizar(self):
        try:
            self._recargar_entidades()
        except (ErrorNegocio, sqlite3.Error) as error:
            messagebox.showerror("Historial", f"No se pudo actualizar el historial:\n{error}", parent=self)
            return
        self.cargar_tabla()

    def limpiar_filtros(self):
        for variable in (self.var_busqueda, self.var_desde, self.var_hasta):
            variable.set("")
        self.var_entidad.set("Todos")
        self.var_incluir_anuladas.set(True)
        self.actualizar()

    def _crear_tabla_principal(self):
        marco = ttk.Frame(self)
        marco.grid(row=2, column=0, sticky="nsew")
        marco.rowconfigure(0, weight=1)
        marco.columnconfigure(0, weight=1)
        columnas = ("id", "fecha", "entidad", "total", "estado")
        self.tabla = ttk.Treeview(marco, columns=columnas, show="headings", selectmode="browse", height=5)
        for columna, titulo, ancho, alineacion in (
                ("id", "N.º", 60, "center"), ("fecha", "Fecha y hora", 150, "center"),
                ("entidad", self.cfg["columna_entidad"], 240, "w"),
                ("total", "Total (₡)", 115, "e"), ("estado", "Estado", 90, "center")):
            self.tabla.heading(columna, text=titulo)
            self.tabla.column(columna, width=ancho, anchor=alineacion)
        self.tabla.tag_configure("anulada", foreground="#a33", background="#eeeeee")
        self.tabla.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(marco, orient="vertical", command=self.tabla.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(marco, orient="horizontal", command=self.tabla.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.tabla.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.tabla.bind("<<TreeviewSelect>>", self.al_seleccionar_fila)

    def _crear_detalle(self):
        marco = ttk.LabelFrame(self, text="Detalle de la operación seleccionada", padding=6)
        marco.grid(row=3, column=0, sticky="nsew", pady=(8, 0))
        marco.rowconfigure(1, weight=1)
        marco.columnconfigure(0, weight=1)
        informacion = ttk.Label(marco, textvariable=self.var_cobro, wraplength=760, width=1)
        informacion.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 4))
        informacion.bind("<Configure>", lambda e: informacion.configure(wraplength=max(120, e.width)))
        columnas = ("producto", "cantidad", "precio", "subtotal")
        self.tabla_detalle = ttk.Treeview(marco, columns=columnas, show="headings", selectmode="none", height=3)
        for columna, titulo, ancho, alineacion in (
                ("producto", "Producto", 320, "w"), ("cantidad", "Cantidad", 80, "center"),
                ("precio", "Precio unit. (₡)", 120, "e"), ("subtotal", "Subtotal (₡)", 120, "e")):
            self.tabla_detalle.heading(columna, text=titulo)
            self.tabla_detalle.column(columna, width=ancho, anchor=alineacion)
        self.tabla_detalle.grid(row=1, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(marco, command=self.tabla_detalle.yview)
        vertical.grid(row=1, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(marco, orient="horizontal", command=self.tabla_detalle.xview)
        horizontal.grid(row=2, column=0, sticky="ew")
        self.tabla_detalle.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)

    def _crear_botones(self):
        botones = ttk.Frame(self)
        botones.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        botones.columnconfigure(0, weight=1)
        ttk.Label(botones, textvariable=self.var_modo, foreground="#58685F").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 4))
        acceso = ttk.Frame(botones)
        acceso.grid(row=1, column=0, sticky="w")
        self.boton_modo = ttk.Button(acceso, command=self.cambiar_modo)
        self.boton_modo.pack(side="left", padx=3)
        if self.tipo == "venta":
            ttk.Button(acceso, text="Ver ticket", style="Secundario.TButton",
                       command=self.ver_ticket).pack(side="left", padx=3)
        cambios = ttk.Frame(botones)
        cambios.grid(row=1, column=1, sticky="e")
        self.boton_editar = ttk.Button(cambios, text="Editar", command=self.editar)
        self.boton_editar.pack(side="left", padx=3)
        self.boton_anular = ttk.Button(cambios, text="Anular", command=self.anular)
        self.boton_anular.pack(side="left", padx=3)
        self._actualizar_permiso()

    def _actualizar_permiso(self):
        estado = "normal" if self.modo_admin else "disabled"
        self.boton_editar.configure(state=estado)
        self.boton_anular.configure(state=estado)
        self.boton_modo.configure(text="Bloquear edición" if self.modo_admin else "Habilitar edición")
        self.var_modo.set("Edición habilitada hasta bloquearla o cerrar la ventana." if self.modo_admin else
                          "Modo consulta. Para editar o anular, habilite la edición con la contraseña.")

    def cambiar_modo(self):
        if self.modo_admin:
            self.modo_admin = False
        else:
            clave = simpledialog.askstring("Habilitar edición", "Contraseña maestra:", show="*", parent=self)
            if clave is None:
                return
            if not clave or not servicios.verificar_clave_admin(clave):
                messagebox.showerror("Acceso denegado", "Contraseña incorrecta.", parent=self)
                return
            self.modo_admin = True
        self._actualizar_permiso()

    def _exigir_modo_admin(self):
        if not self.modo_admin:
            messagebox.showwarning("Modo consulta", "Habilite la edición con la contraseña para continuar.", parent=self)
            return False
        return True

    def cargar_tabla(self):
        try:
            filas = self.cfg["listar"](self.conexion,
                formato.convertir_busqueda(self.var_busqueda.get()), **self._opciones_filtros())
        except (ErrorNegocio, sqlite3.Error) as error:
            messagebox.showwarning("Revisar filtros", str(error), parent=self)
            return
        seleccion = self.tabla.selection()
        seleccionado = seleccion[0] if seleccion else None
        for fila in self.tabla.get_children():
            self.tabla.delete(fila)
        self._limpiar_detalle()
        self.filas_por_id = {}
        total_vigente = 0.0
        for t in filas:
            identificador = str(t[self.cfg["campo_id"]])
            self.filas_por_id[identificador] = t
            anulada = bool(t["anulada"])
            if not anulada:
                total_vigente += t["total"]
            self.tabla.insert("", "end", iid=identificador, tags=("anulada",) if anulada else (), values=(
                t[self.cfg["campo_id"]], formato.formatear_fecha_hora(t["fecha_hora"]),
                t[self.cfg["campo_entidad"]], _dinero(t["total"]), "ANULADA" if anulada else "Vigente"))
        if filas:
            self.var_resultados.set(f"{len(filas)} operación(es). Total vigente mostrado: ₡{total_vigente:,.2f}.")
        else:
            self.var_resultados.set("No se encontraron operaciones con esos filtros.")
        if seleccionado in self.filas_por_id:
            self.tabla.selection_set(seleccionado)
            self.al_seleccionar_fila()

    def _limpiar_detalle(self):
        for fila in self.tabla_detalle.get_children():
            self.tabla_detalle.delete(fila)
        self.var_cobro.set("Seleccione una operación para consultar su detalle.")

    def _id_seleccionado(self):
        seleccion = self.tabla.selection()
        if not seleccion:
            messagebox.showinfo("Historial", f"Seleccione una {self.cfg['titulo']} de la tabla.", parent=self)
            return None
        if self.tabla.set(seleccion[0], "estado") == "ANULADA":
            messagebox.showinfo("Historial", "La operación ya está anulada y se conserva solo para consulta.", parent=self)
            return None
        return int(seleccion[0])

    def al_seleccionar_fila(self, _evento=None):
        seleccion = self.tabla.selection()
        if not seleccion:
            return
        self._limpiar_detalle()
        try:
            detalles = self.cfg["detalle"](self.conexion, int(seleccion[0]))
        except (ErrorNegocio, sqlite3.Error) as error:
            messagebox.showerror("Detalle", str(error), parent=self)
            return
        for d in detalles:
            self.tabla_detalle.insert("", "end", values=(d["producto"], d["cantidad"],
                                      _dinero(d["precio_unitario"]), _dinero(d["subtotal"])))
        fila = self.filas_por_id.get(seleccion[0])
        if fila is not None:
            texto = ("ANULADA" if fila["anulada"] else "Vigente") + f" · Total: ₡{fila['total']:,.2f}"
            if self.tipo == "venta":
                texto += " · Pago: " + fila["metodo_pago"]
                if fila["monto_recibido"] is not None:
                    texto += f" · Recibido: ₡{fila['monto_recibido']:,.2f} · Vuelto: ₡{fila['vuelto']:,.2f}"
            self.var_cobro.set(texto)

    def anular(self):
        if not self._exigir_modo_admin():
            return
        identificador = self._id_seleccionado()
        if identificador is None:
            return
        if not messagebox.askyesno("Anular operación",
                f"¿Desea anular la {self.cfg['titulo']} N.º {identificador}?\n\n"
                f"{self.cfg['aviso_anular']}\nEl registro se conserva como ANULADA. Esta acción no se puede deshacer.",
                icon="warning", parent=self):
            return
        try:
            self.cfg["eliminar"](self.conexion, identificador)
        except ErrorNegocio as error:
            messagebox.showerror("No se pudo anular", str(error), parent=self)
            return
        self.cargar_tabla()
        messagebox.showinfo("Operación anulada", "La operación se anuló y el inventario se ajustó.", parent=self)

    def ver_ticket(self):
        seleccion = self.tabla.selection()
        if not seleccion:
            messagebox.showinfo("Ticket", "Seleccione una venta de la tabla.", parent=self)
            return
        mostrar_ticket(self, self.conexion, int(seleccion[0]))

    def editar(self):
        if not self._exigir_modo_admin():
            return
        identificador = self._id_seleccionado()
        if identificador is not None:
            DialogoEdicion(self, self.conexion, self.cfg, identificador, al_guardar=self.cargar_tabla)


# ======================================================================
# Diálogo para editar cantidades y precios de una transacción
# ======================================================================
class DialogoEdicion(tk.Toplevel):
    def __init__(self, padre, conexion, cfg, id_transaccion, al_guardar):
        super().__init__(padre)
        self.conexion = conexion
        self.cfg = cfg
        self.id_transaccion = id_transaccion
        self.al_guardar = al_guardar
        # Copia editable de las líneas; no se toca la BD hasta pulsar "Guardar cambios".
        self.lineas = [
            {"id_detalle": d[cfg["campo_id_detalle"]], "producto": d["producto"],
             "cantidad": d["cantidad"], "precio": d["precio_unitario"]}
            for d in cfg["detalle"](conexion, id_transaccion)
        ]
        self.indice_seleccionado = None
        self.cobro_original = None
        if cfg["campo_id"] == "id_venta":
            self.cobro_original = conexion.execute(
                "SELECT metodo_pago, monto_recibido FROM ventas WHERE id_venta = ?",
                (id_transaccion,),
            ).fetchone()
        self.es_efectivo = (self.cobro_original is not None
                            and self.cobro_original["metodo_pago"] == "Efectivo")

        self.title(f"Editar {cfg['titulo']} N.º {id_transaccion}")
        self.geometry("760x610" if self.es_efectivo else "760x530")
        self.minsize(700, 550 if self.es_efectivo else 470)
        self.transient(padre)
        self.grab_set()   # ventana modal

        self.var_producto = tk.StringVar()
        self.var_cantidad = tk.StringVar()
        self.var_precio = tk.StringVar()
        self.var_total = tk.StringVar()
        recibido = self.cobro_original["monto_recibido"] if self.es_efectivo else None
        self.var_recibido = tk.StringVar(value=f"{recibido:.2f}" if recibido is not None else "")

        self._crear_widgets()
        self._actualizar_tabla()

    def _crear_widgets(self):
        ttk.Label(self, text="Modifique cantidades y precios. Los cambios escritos se aplican al guardar.\n"
                             "La fecha y el N.º se conservan.", foreground="gray").pack(
            anchor="w", padx=12, pady=(10, 4))

        marco = ttk.Frame(self)
        marco.pack(fill="both", expand=True, padx=12)
        marco.rowconfigure(0, weight=1)
        marco.columnconfigure(0, weight=1)
        columnas = ("producto", "cantidad", "precio", "subtotal")
        encabezados = (("producto", "Producto", 250, "w"), ("cantidad", "Cantidad", 80, "center"),
                       ("precio", "Precio unit.", 100, "e"), ("subtotal", "Subtotal", 110, "e"))
        self.tabla = ttk.Treeview(marco, columns=columnas, show="headings", selectmode="browse", height=7)
        for columna, titulo, ancho, alineacion in encabezados:
            self.tabla.heading(columna, text=titulo)
            self.tabla.column(columna, width=ancho, anchor=alineacion)
        self.tabla.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(marco, orient="vertical", command=self.tabla.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tabla.configure(yscrollcommand=scroll.set)
        self.tabla.bind("<<TreeviewSelect>>", self.al_seleccionar_linea)

        form = ttk.LabelFrame(self, text="Línea seleccionada", padding=10)
        form.pack(fill="x", padx=12, pady=8)
        form.columnconfigure(1, weight=1)
        ttk.Label(form, text="Producto:").grid(row=0, column=0, sticky="w")
        ttk.Label(form, textvariable=self.var_producto, font=("Segoe UI", 10, "bold")).grid(
            row=0, column=1, columnspan=4, sticky="w", padx=8)
        ttk.Label(form, text="Cantidad:").grid(row=1, column=0, sticky="w", pady=6)
        ttk.Entry(form, textvariable=self.var_cantidad, width=10, validate="key",
                  validatecommand=(self.register(_solo_digitos), "%P")).grid(row=1, column=1, sticky="w", padx=8)
        ttk.Label(form, text="Precio unitario:").grid(row=1, column=2, sticky="e")
        ttk.Entry(form, textvariable=self.var_precio, width=14, validate="key",
                  validatecommand=(self.register(_solo_decimal), "%P")).grid(row=1, column=3, sticky="w", padx=8)
        ttk.Button(form, text="Aplicar a la línea", command=self.aplicar_linea).grid(row=1, column=4, padx=4)

        if self.es_efectivo:
            cobro = ttk.LabelFrame(self, text="Corrección del cobro en efectivo", padding=10)
            cobro.pack(fill="x", padx=12, pady=(0, 8))
            ttk.Label(cobro, text="Efectivo recibido (₡):").grid(row=0, column=0, sticky="w")
            ttk.Entry(cobro, textvariable=self.var_recibido, width=18, validate="key",
                      validatecommand=(self.register(_solo_decimal), "%P")).grid(row=0, column=1, padx=8)
            ttk.Label(cobro, text="El vuelto se recalcula al guardar. Montos sin separador de miles.").grid(
                row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))

        pie = ttk.Frame(self, padding=(12, 0, 12, 12))
        pie.pack(fill="x")
        ttk.Label(pie, text="Nuevo total:", font=("Segoe UI", 13, "bold")).pack(side="left")
        ttk.Label(pie, textvariable=self.var_total, font=("Segoe UI", 13, "bold")).pack(side="left", padx=8)
        ttk.Button(pie, text="Cancelar", command=self.destroy).pack(side="right", padx=4)
        ttk.Button(pie, text="Guardar cambios", command=self.guardar).pack(side="right", padx=4)

    # ------------------------------------------------------------------
    def _actualizar_tabla(self):
        for fila in self.tabla.get_children():
            self.tabla.delete(fila)
        total = 0.0
        for posicion, linea in enumerate(self.lineas):
            subtotal = round(linea["cantidad"] * linea["precio"], 2)
            total += subtotal
            self.tabla.insert("", "end", iid=str(posicion), values=(
                linea["producto"], linea["cantidad"], _dinero(linea["precio"]), _dinero(subtotal)))
        self.var_total.set(_dinero(round(total, 2)))
        if self.indice_seleccionado is not None:
            self.tabla.selection_set(str(self.indice_seleccionado))

    def al_seleccionar_linea(self, _evento=None):
        seleccion = self.tabla.selection()
        if not seleccion:
            return
        nuevo_indice = int(seleccion[0])
        if nuevo_indice == self.indice_seleccionado:
            return
        if not self._aplicar_linea_pendiente():
            self.tabla.selection_set(str(self.indice_seleccionado))
            return
        self.indice_seleccionado = nuevo_indice
        linea = self.lineas[self.indice_seleccionado]
        self.var_producto.set(linea["producto"])
        self.var_cantidad.set(str(linea["cantidad"]))
        self.var_precio.set(f"{linea['precio']:.2f}")
        self._actualizar_tabla()

    def _aplicar_linea_pendiente(self):
        """Valida antes de cambiar de fila o guardar; la BD todavía no cambia."""
        if self.indice_seleccionado is None:
            return True
        try:
            texto = self.var_cantidad.get().strip()
            if not re.fullmatch(r"[0-9]{1,9}", texto) or int(texto) < 1:
                raise ErrorNegocio("La cantidad debe ser un entero mayor que cero (máximo 9 dígitos).")
            cantidad = int(texto)
            precio = _leer_precio(self.var_precio.get())
            if not math.isfinite(cantidad * precio):
                raise ErrorNegocio("El subtotal supera el máximo permitido.")
        except ErrorNegocio as error:
            messagebox.showwarning("Editar", str(error), parent=self)
            return False
        self.lineas[self.indice_seleccionado]["cantidad"] = cantidad
        self.lineas[self.indice_seleccionado]["precio"] = precio
        return True

    def aplicar_linea(self):
        if self.indice_seleccionado is None:
            messagebox.showinfo("Editar", "Seleccione una línea de la tabla.", parent=self)
            return
        if self._aplicar_linea_pendiente():
            self._actualizar_tabla()

    def guardar(self):
        if not self._aplicar_linea_pendiente():
            return
        self._actualizar_tabla()
        opciones = {}
        if self.es_efectivo:
            try:
                texto = self.var_recibido.get().strip()
                if texto:
                    opciones["monto_recibido"] = _leer_precio(texto, "Efectivo recibido")
                elif self.cobro_original["monto_recibido"] is not None:
                    raise ErrorNegocio("Ingrese el efectivo recibido. No se puede borrar el cobro registrado.")
                total = round(sum(round(l["cantidad"] * l["precio"], 2) for l in self.lineas), 2)
                servicios.calcular_cobro(total, "Efectivo", opciones.get("monto_recibido"))
            except ErrorNegocio as error:
                messagebox.showwarning("Revisar cobro", str(error), parent=self)
                return
        if not messagebox.askyesno(
                "Confirmar modificación",
                "Se guardarán los cambios y el inventario se ajustará según la diferencia "
                "de cantidades.\n¿Desea continuar?", parent=self):
            return
        lineas = [(l["id_detalle"], l["cantidad"], l["precio"]) for l in self.lineas]
        try:
            nuevo_total = self.cfg["editar"](self.conexion, self.id_transaccion, lineas, **opciones)
        except ErrorNegocio as error:
            # Hubo ROLLBACK: ni la transacción ni el inventario cambiaron.
            messagebox.showerror("No se pudo modificar", str(error), parent=self)
            return
        messagebox.showinfo("Modificada",
                            f"La {self.cfg['titulo']} se actualizó. Nuevo total: {_dinero(nuevo_total)}",
                            parent=self)
        self.destroy()
        self.al_guardar()
