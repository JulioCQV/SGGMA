"""
ui/ui_cierre_caja.py
Cierre de Caja: totales del día en pantalla minimalista (fuentes grandes).
Los datos vienen de servicios.obtener_cierre_caja_diario; las anuladas no cuentan.
"""

import tkinter as tk
from tkinter import ttk, messagebox

import servicios
from servicios import ErrorNegocio
from ui import formato

MONEDA = "₡"


def abrir_ventana(padre, conexion):
    """Función que llama ui_principal.py; devuelve el Toplevel creado."""
    return VentanaCierreCaja(padre, conexion)


def _dinero(valor):
    return f"{MONEDA}{valor:,.2f}"


def _solo_fecha(texto_nuevo):
    return len(texto_nuevo) <= 10 and all(c.isdigit() or c == "/" for c in texto_nuevo)


class VentanaCierreCaja(tk.Toplevel):
    def __init__(self, padre, conexion):
        super().__init__(padre)
        self.conexion = conexion
        self.title("Cierre de Caja")
        self.geometry("620x560")
        self.minsize(540, 500)

        self.var_fecha = tk.StringVar()
        self.var_total_vendido = tk.StringVar()
        self.var_detalle_ventas = tk.StringVar()
        self.var_total_comprado = tk.StringVar()
        self.var_detalle_compras = tk.StringVar()
        self.var_metodos = {metodo: tk.StringVar() for metodo in servicios.METODOS_PAGO}

        self._crear_widgets()
        self.actualizar()        # sin fecha = hoy

    # ------------------------------------------------------------------
    def _crear_widgets(self):
        estilo = ttk.Style(self)
        estilo.configure("CajaTitulo.TLabel", font=("Segoe UI", 20, "bold"))
        estilo.configure("CajaEtiqueta.TLabel", font=("Segoe UI", 12), foreground="#666")
        estilo.configure("CajaTotal.TLabel", font=("Segoe UI", 32, "bold"))
        estilo.configure("CajaDetalle.TLabel", font=("Segoe UI", 11), foreground="#888")
        estilo.configure("CajaMetodo.TLabel", font=("Segoe UI", 15))
        estilo.configure("CajaMonto.TLabel", font=("Segoe UI", 18, "bold"))

        marco = ttk.Frame(self, padding=(28, 22))
        marco.pack(fill="both", expand=True)

        # Encabezado: título y fecha
        cabecera = ttk.Frame(marco)
        cabecera.pack(fill="x")
        ttk.Label(cabecera, text="Cierre de Caja", style="CajaTitulo.TLabel").pack(side="left")
        ttk.Button(cabecera, text="Hoy", width=6, command=self.ir_a_hoy).pack(side="right")
        ttk.Button(cabecera, text="Ver", width=6, command=self.actualizar).pack(side="right", padx=6)
        entrada = ttk.Entry(cabecera, textvariable=self.var_fecha, width=12, justify="center",
                            validate="key", validatecommand=(self.register(_solo_fecha), "%P"))
        entrada.pack(side="right")
        entrada.bind("<Return>", lambda _e: self.actualizar())

        # Totales del día
        totales = ttk.Frame(marco)
        totales.pack(fill="x", pady=(30, 10))
        totales.columnconfigure(0, weight=1, uniform="totales")
        totales.columnconfigure(1, weight=1, uniform="totales")
        self._crear_total(totales, 0, "Vendido", self.var_total_vendido, self.var_detalle_ventas)
        self._crear_total(totales, 1, "Comprado", self.var_total_comprado, self.var_detalle_compras)

        ttk.Separator(marco, orient="horizontal").pack(fill="x", pady=22)

        # Ventas por método de pago
        metodos = ttk.Frame(marco)
        metodos.pack(fill="x")
        metodos.columnconfigure(1, weight=1)
        for fila, metodo in enumerate(servicios.METODOS_PAGO):
            ttk.Label(metodos, text=metodo, style="CajaMetodo.TLabel").grid(
                row=fila, column=0, sticky="w", pady=8)
            ttk.Label(metodos, textvariable=self.var_metodos[metodo], style="CajaMonto.TLabel").grid(
                row=fila, column=1, sticky="e", pady=8)

    def _crear_total(self, padre, columna, titulo, var_monto, var_detalle):
        bloque = ttk.Frame(padre)
        bloque.grid(row=0, column=columna, sticky="w")
        ttk.Label(bloque, text=titulo, style="CajaEtiqueta.TLabel").pack(anchor="w")
        ttk.Label(bloque, textvariable=var_monto, style="CajaTotal.TLabel").pack(anchor="w")
        ttk.Label(bloque, textvariable=var_detalle, style="CajaDetalle.TLabel").pack(anchor="w")

    # ------------------------------------------------------------------
    def ir_a_hoy(self):
        self.var_fecha.set("")
        self.actualizar()

    def actualizar(self):
        """Consulta el cierre de la fecha indicada (vacía = hoy) y refresca la pantalla."""
        try:
            fecha_bd = formato.a_fecha_iso(self.var_fecha.get())      # DD/MM/AAAA -> AAAA-MM-DD
            cierre = servicios.obtener_cierre_caja_diario(self.conexion, fecha_bd or None)
        except ErrorNegocio as error:
            messagebox.showwarning("Cierre de caja", str(error), parent=self)
            return

        self.var_fecha.set(formato.formatear_fecha(cierre["fecha"]))
        self.var_total_vendido.set(_dinero(cierre["total_vendido"]))
        self.var_detalle_ventas.set(f"{cierre['cantidad_ventas']} ventas")
        self.var_total_comprado.set(_dinero(cierre["total_comprado"]))
        self.var_detalle_compras.set(f"{cierre['cantidad_compras']} compras")
        for metodo, variable in self.var_metodos.items():
            variable.set(_dinero(cierre["ventas_por_metodo"][metodo]))