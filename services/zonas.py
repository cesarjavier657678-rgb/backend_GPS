import json
import math
import os
import tempfile
import threading
import uuid
from pathlib import Path


_BLOQUEO_ARCHIVO = threading.RLock()
RADIO_TIERRA_M = 6_371_000


class ErrorValidacionZona(ValueError):
    pass


class ErrorZonaNoEncontrada(LookupError):
    pass


def _limpiar_texto(valor, longitud_maxima=220):
    return " ".join(str(valor or "").replace("\xa0", " ").split())[:longitud_maxima]


def _normalizar_riesgos(valor):
    if isinstance(valor, str):
        valor = [elemento.strip() for elemento in valor.split(",")]
    if not isinstance(valor, list):
        return []
    return [
        _limpiar_texto(elemento, 80)
        for elemento in valor
        if _limpiar_texto(elemento, 80)
    ][:12]


def _numero(valor, nombre_campo):
    try:
        return float(valor)
    except (TypeError, ValueError) as exc:
        raise ErrorValidacionZona(f"{nombre_campo} debe ser un número válido.") from exc


def validar_zona(datos, parcial=False):
    if not isinstance(datos, dict):
        raise ErrorValidacionZona("Los datos de la zona no son válidos.")

    resultado = {}
    obligatorios = {"name", "lat", "lng", "radius_m"}
    alias_campos = {
        "name": ("name", "nombre"),
        "municipality": ("municipality", "municipio"),
        "state": ("state", "estado"),
        "risks": ("risks", "riesgos"),
        "description": ("description", "contexto"),
        "lat": ("lat", "latitude", "latitud"),
        "lng": ("lng", "longitude", "longitud"),
        "radius_m": ("radius_m", "radio_metros", "radio"),
    }

    normalizados = {}
    for destino_campo, nombres in alias_campos.items():
        for nombre in nombres:
            if nombre in datos:
                normalizados[destino_campo] = datos[nombre]
                break

    if not parcial:
        faltantes = [campo for campo in obligatorios if campo not in normalizados]
        if faltantes:
            raise ErrorValidacionZona(
                "Faltan datos obligatorios: " + ", ".join(sorted(faltantes)) + "."
            )

    for campo in ("name", "municipality", "state", "description"):
        if campo in normalizados:
            limite = 800 if campo == "description" else 180
            resultado[campo] = _limpiar_texto(normalizados[campo], limite)

    if "name" in resultado and not resultado["name"]:
        raise ErrorValidacionZona("El nombre de la zona es obligatorio.")

    if "risks" in normalizados:
        resultado["risks"] = _normalizar_riesgos(normalizados["risks"])

    if "lat" in normalizados:
        resultado["lat"] = _numero(normalizados["lat"], "La latitud")
        if not -90 <= resultado["lat"] <= 90:
            raise ErrorValidacionZona("La latitud debe estar entre -90 y 90.")

    if "lng" in normalizados:
        resultado["lng"] = _numero(normalizados["lng"], "La longitud")
        if not -180 <= resultado["lng"] <= 180:
            raise ErrorValidacionZona("La longitud debe estar entre -180 y 180.")

    if "radius_m" in normalizados:
        resultado["radius_m"] = round(_numero(normalizados["radius_m"], "El radio"), 2)
        if not 50 <= resultado["radius_m"] <= 50_000:
            raise ErrorValidacionZona("El radio debe estar entre 50 y 50000 metros.")

    return resultado


def _a_publico(zona):
    return {
        "id": str(zona.get("id", "")),
        "name": zona.get("name", ""),
        "municipality": zona.get("municipality", ""),
        "state": zona.get("state", ""),
        "risks": zona.get("risks", []),
        "description": zona.get("description", ""),
        "lat": float(zona.get("lat", 0)),
        "lng": float(zona.get("lng", 0)),
        "radius_m": float(zona.get("radius_m", 500)),
    }


def _normalizar_antigua(zona):
    if not isinstance(zona, dict):
        return None
    try:
        limpia = validar_zona(zona)
    except ErrorValidacionZona:
        return None
    limpia["id"] = str(zona.get("id") or uuid.uuid4())
    return limpia


def listar_zonas(ruta_archivo):
    camino = Path(ruta_archivo)
    with _BLOQUEO_ARCHIVO:
        if not camino.exists():
            camino.parent.mkdir(parents=True, exist_ok=True)
            camino.write_text("[]\n", encoding="utf-8")
            return []
        try:
            contenido_crudo = json.loads(camino.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise ErrorValidacionZona("No fue posible leer el archivo de zonas.") from exc

    if not isinstance(contenido_crudo, list):
        raise ErrorValidacionZona("El archivo de zonas debe contener una lista JSON.")

    zonas = []
    for elemento in contenido_crudo:
        zona = _normalizar_antigua(elemento)
        if zona:
            zonas.append(_a_publico(zona))
    return zonas


def _guardar_zonas(ruta_archivo, zonas):
    camino = Path(ruta_archivo)
    camino.parent.mkdir(parents=True, exist_ok=True)
    contenido_texto = json.dumps(zonas, ensure_ascii=False, indent=2) + "\n"

    with _BLOQUEO_ARCHIVO:
        descriptor, nombre_temporal = tempfile.mkstemp(
            prefix=f".{camino.name}.", suffix=".tmp", dir=camino.parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as archivo_temporal:
                archivo_temporal.write(contenido_texto)
                archivo_temporal.flush()
                os.fsync(archivo_temporal.fileno())
            os.replace(nombre_temporal, camino)
        finally:
            if os.path.exists(nombre_temporal):
                os.unlink(nombre_temporal)


def crear_zona(ruta_archivo, datos):
    zona = validar_zona(datos)
    zona.update({"id": str(uuid.uuid4())})
    zonas = listar_zonas(ruta_archivo)
    zonas.append(_a_publico(zona))
    _guardar_zonas(ruta_archivo, zonas)
    return _a_publico(zona)


def actualizar_zona(ruta_archivo, id_zona, cambios):
    cambios_limpios = validar_zona(cambios, parcial=True)
    if not cambios_limpios:
        raise ErrorValidacionZona("No se recibieron cambios válidos.")

    zonas = listar_zonas(ruta_archivo)
    for indice, zona in enumerate(zonas):
        if str(zona["id"]) == str(id_zona):
            actualizada = {**zona, **cambios_limpios, "id": str(id_zona)}
            actualizada = _a_publico(validar_zona(actualizada) | {"id": str(id_zona)})
            zonas[indice] = actualizada
            _guardar_zonas(ruta_archivo, zonas)
            return actualizada
    raise ErrorZonaNoEncontrada("No se encontró la zona solicitada.")


def eliminar_zona(ruta_archivo, id_zona):
    zonas = listar_zonas(ruta_archivo)
    restantes = [zona for zona in zonas if str(zona["id"]) != str(id_zona)]
    if len(restantes) == len(zonas):
        raise ErrorZonaNoEncontrada("No se encontró la zona solicitada.")
    _guardar_zonas(ruta_archivo, restantes)


def distancia_haversine_m(punto_a, punto_b):
    latitud1, longitud1 = map(math.radians, punto_a)
    latitud2, longitud2 = map(math.radians, punto_b)
    dlat = latitud2 - latitud1
    dlng = longitud2 - longitud1
    valor = (
        math.sin(dlat / 2) ** 2
        + math.cos(latitud1) * math.cos(latitud2) * math.sin(dlng / 2) ** 2
    )
    return 2 * RADIO_TIERRA_M * math.asin(min(1, math.sqrt(valor)))


def _a_xy(punto, latitud_referencia):
    latitud, longitud = punto
    x = math.radians(longitud) * RADIO_TIERRA_M * math.cos(math.radians(latitud_referencia))
    y = math.radians(latitud) * RADIO_TIERRA_M
    return x, y


def distancia_punto_segmento_m(punto, inicio, fin):
    latitud_referencia = (punto[0] + inicio[0] + fin[0]) / 3
    px, py = _a_xy(punto, latitud_referencia)
    ax, ay = _a_xy(inicio, latitud_referencia)
    bx, by = _a_xy(fin, latitud_referencia)
    dx, dy = bx - ax, by - ay
    longitud_cuadrada = dx * dx + dy * dy
    if longitud_cuadrada == 0:
        return math.hypot(px - ax, py - ay)
    factor = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / longitud_cuadrada))
    x_cercana = ax + factor * dx
    y_cercana = ay + factor * dy
    return math.hypot(px - x_cercana, py - y_cercana)


def distancia_a_ruta_m(punto, puntos_ruta):
    if not puntos_ruta:
        return float("inf")
    if len(puntos_ruta) == 1:
        return distancia_haversine_m(punto, puntos_ruta[0])
    return min(
        distancia_punto_segmento_m(punto, puntos_ruta[indice], puntos_ruta[indice + 1])
        for indice in range(len(puntos_ruta) - 1)
    )


def zonas_intersectan_ruta(puntos_ruta, zonas):
    coincidencias = []
    for zona in zonas:
        try:
            centro = (float(zona["lat"]), float(zona["lng"]))
            radio = float(zona["radius_m"])
        except (KeyError, TypeError, ValueError):
            continue
        distancia = distancia_a_ruta_m(centro, puntos_ruta)
        if distancia <= radio:
            elemento = dict(zona)
            elemento["distance_to_route_m"] = round(distancia, 2)
            coincidencias.append(elemento)
    return coincidencias
