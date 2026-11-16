"""
ui/formato.py
Formato de fechas para la interfaz. SQLite guarda las fechas como 'AAAA-MM-DD HH:MM:SS'
(y las de vencimiento como 'AAAA-MM-DD'); en pantalla se muestran como
'DD/MM/AAAA HH:MM' y 'DD/MM/AAAA'. Todas las ventanas usan estas funciones.
"""

import re
from datetime import datetime

from servicios import ErrorNegocio

_FORMATOS_BD = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")


def formatear_fecha_hora(texto):
    """'2026-11-12 14:35:09' -> '12/11/2026 14:35' (si no trae hora, solo la fecha)."""
    if not texto:
        return ""
    for formato in _FORMATOS_BD:
        try:
            fecha = datetime.strptime(texto, formato)
        except ValueError:
            continue
        return fecha.strftime("%d/%m/%Y %H:%M" if "%H" in formato else "%d/%m/%Y")
    return texto


def formatear_fecha(texto):
    """'2026-11-12' (o con hora) -> '12/11/2026'."""
    if not texto:
        return ""
    return formatear_fecha_hora(texto).split(" ")[0]


def a_fecha_iso(texto):
    """
    Lo que escribe el usuario ('DD/MM/AAAA') -> 'AAAA-MM-DD' para la base de datos.
    Vacío devuelve ''. También acepta una fecha que ya venga como 'AAAA-MM-DD'.
    """
    texto = (texto or "").strip()
    if not texto:
        return ""
    for formato in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(texto, formato).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise ErrorNegocio("La fecha debe tener el formato DD/MM/AAAA.")


def convertir_busqueda(texto):
    """Si el usuario busca por una fecha completa 'DD/MM/AAAA', la pasa al formato de la BD."""
    if re.fullmatch(r"\s*\d{1,2}/\d{1,2}/\d{4}\s*", texto or ""):
        try:
            return a_fecha_iso(texto)
        except ErrorNegocio:
            pass
    return texto
