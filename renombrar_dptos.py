"""
Renombra los GeoJSON exportados de OroraTech WFS al patron consistente:
minusculas, sin tildes, sin espacios, solo el nombre del departamento.

Ejemplo:
    wfs-area-export-Bogota_2025-01-01-2025-12-31.geojson  ->  bogota.geojson
    wfs-area-export-Choco_2025-01-01-2025-12-31.geojson   ->  choco.geojson

USO:
    1. Coloca este script donde quieras y ajusta CARPETA abajo si hace falta.
    2. Ejecuta primero en modo simulacion (por defecto):  python renombrar_departamentos.py
    3. Revisa la lista. Si todo se ve bien, cambia SIMULAR = False y vuelve a ejecutar.
"""

import re
import unicodedata
from pathlib import Path

# ----------------------------------------------------------------------
# CONFIGURACION
# ----------------------------------------------------------------------
# Carpeta donde estan los .geojson de un anio.
# En tu PC seria algo como: r"C:\Users\MSI\Downloads\incendios-colombia-wfs\data\2025"
# El prefijo r"..." evita problemas con las barras invertidas de Windows.
CARPETA = Path(r"C:\Users\MSI\Downloads\incendios-colombia-wfs\data\2025")

# True  = solo muestra que haria, sin tocar nada (recomendado la primera vez)
# False = aplica los cambios de verdad
SIMULAR = True
#SIMULAR = False
# ----------------------------------------------------------------------


def quitar_tildes(texto: str) -> str:
    """Convierte 'Bogota'/'Choco'/'Caqueta' quitando acentos y enie."""
    # Normaliza y elimina los signos diacriticos (tildes)
    nfkd = unicodedata.normalize("NFKD", texto)
    sin_tildes = "".join(c for c in nfkd if not unicodedata.combining(c))
    # La enie se trata aparte porque a veces no se descompone como se espera
    return sin_tildes.replace("ñ", "n").replace("Ñ", "N")


def nombre_limpio(nombre_archivo: str) -> str:
    """
    Extrae el departamento del nombre original y devuelve el nombre final.
    De 'wfs-area-export-Bogota_2025-01-01-2025-12-31.geojson' saca 'bogota.geojson'.
    """
    # 1. Quita el prefijo de exportacion si existe
    base = nombre_archivo
    base = re.sub(r"^wfs-area-export-", "", base)

    # 2. Quita la extension para trabajar solo el nombre
    base = re.sub(r"\.geojson$", "", base, flags=re.IGNORECASE)

    # 3. El departamento es todo lo que va antes del primer "_" (que inicia las fechas)
    departamento = base.split("_")[0]

    # 4. Limpieza: sin tildes -> minusculas -> espacios por guiones
    departamento = quitar_tildes(departamento).lower().strip()
    departamento = re.sub(r"\s+", "-", departamento)
    # Por seguridad, deja solo letras, numeros y guiones
    departamento = re.sub(r"[^a-z0-9\-]", "", departamento)

    return f"{departamento}.geojson"


def main():
    if not CARPETA.exists():
        print(f"[ERROR] No existe la carpeta: {CARPETA}")
        print("Ajusta la variable CARPETA al inicio del script.")
        return

    archivos = sorted(CARPETA.glob("*.geojson"))
    if not archivos:
        print(f"[AVISO] No se encontraron archivos .geojson en: {CARPETA}")
        return

    modo = "SIMULACION (no se cambia nada)" if SIMULAR else "APLICANDO CAMBIOS"
    print(f"Carpeta : {CARPETA}")
    print(f"Modo    : {modo}")
    print(f"Archivos: {len(archivos)}")
    print("-" * 70)

    destinos = {}  # para detectar colisiones de nombres
    cambios = 0
    sin_cambio = 0

    for origen in archivos:
        nuevo = nombre_limpio(origen.name)
        destino = origen.with_name(nuevo)

        # Detecta si dos archivos terminarian con el mismo nombre
        if nuevo in destinos:
            print(f"[!] COLISION: '{origen.name}' y '{destinos[nuevo]}' "
                  f"apuntan ambos a '{nuevo}'. Se omite el segundo.")
            continue
        destinos[nuevo] = origen.name

        if origen.name == nuevo:
            print(f"[=] Ya correcto : {nuevo}")
            sin_cambio += 1
            continue

        print(f"[>] {origen.name}")
        print(f"        ->  {nuevo}")
        cambios += 1

        if not SIMULAR:
            if destino.exists():
                print(f"        [!] Ya existe '{nuevo}', se omite para no sobrescribir.")
                continue
            origen.rename(destino)

    print("-" * 70)
    print(f"A renombrar: {cambios}   |   Ya correctos: {sin_cambio}")
    if SIMULAR and cambios:
        print("\nRevisa la lista de arriba. Si esta bien, cambia SIMULAR = False y vuelve a ejecutar.")
    elif not SIMULAR and cambios:
        print("\nListo. Archivos renombrados.")


if __name__ == "__main__":
    main()