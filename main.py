"""Punto de entrada del SGGMA. Ejecutar con: python main.py."""

import sys
import tkinter as tk
from tkinter import messagebox

from database import inicializar_base_datos
from ui.ui_principal import VentanaPrincipal


def main():
    raiz = None
    conexion = None
    etapa = "interfaz"
    try:
        # Una sola raíz permite mostrar también los errores de inicialización.
        raiz = tk.Tk()
        raiz.withdraw()
        etapa = "base de datos"
        conexion = inicializar_base_datos()
        etapa = "interfaz"
        VentanaPrincipal(raiz, conexion)
        raiz.deiconify()
        raiz.mainloop()
    except Exception as error:
        mensaje = f"No se pudo iniciar o ejecutar el sistema ({etapa}):\n{error}"
        if raiz is not None:
            try:
                messagebox.showerror("Error del sistema", mensaje, parent=raiz)
            except tk.TclError:
                print(mensaje, file=sys.stderr)
        else:
            print(mensaje, file=sys.stderr)
    finally:
        # Se liberan los recursos también si falla la creación de alguna ventana.
        try:
            if conexion is not None:
                conexion.close()
        finally:
            if raiz is not None:
                try:
                    if raiz.winfo_exists():
                        raiz.destroy()
                except tk.TclError:
                    pass  # la ventana ya fue destruida al salir


if __name__ == "__main__":
    main()
