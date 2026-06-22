import math
import re
import unicodedata

import requests

from services.motor_rutas import decodificar_polilinea
from services.zonas import distancia_a_ruta_m


URL_RUTAS = "https://routes.googleapis.com/directions/v2:computeRoutes"
URL_LUGARES = "https://places.googleapis.com/v1/places:searchText"

CAMPOS_LUGARES = ",".join([
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.location",
    "places.primaryType",
    "places.types",
])


def _limpiar_texto(valor):
    return " ".join(str(valor or "").replace("\xa0", " ").split())


def _texto_normalizado(valor):
    texto = unicodedata.normalize("NFD", _limpiar_texto(valor))
    return "".join(
        caracter for caracter in texto
        if unicodedata.category(caracter) != "Mn"
    ).lower()


def _ubicacion_de_paso(paso, clave):
    ubicacion = paso.get(clave, {}).get("latLng", {})
    try:
        return {
            "lat": float(ubicacion["latitude"]),
            "lng": float(ubicacion["longitude"]),
        }
    except (KeyError, TypeError, ValueError):
        return None


def _es_paso_peaje(paso):
    instruccion = paso.get("navigationInstruction", {}).get("instructions", "")
    texto = _texto_normalizado(instruccion)
    palabras = ("peaje", "caseta", "cuota", "plaza de cobro", "toll")
    return any(palabra in texto for palabra in palabras) or bool(
        re.search(r"\b\d{1,3}\s*d\b", texto, flags=re.IGNORECASE)
    )


def _candidatos_peaje_pasos(ruta):
    candidatos = []
    for tramo in ruta.get("legs", []):
        for paso in tramo.get("steps", []):
            if not _es_paso_peaje(paso):
                continue
            instruccion = _limpiar_texto(
                paso.get("navigationInstruction", {}).get("instructions", "")
            )
            ubicacion = _ubicacion_de_paso(paso, "startLocation") or _ubicacion_de_paso(
                paso, "endLocation"
            )
            candidatos.append({
                "name": instruccion or "Tramo de peaje",
                "address": "",
                "instruction": instruccion,
                "lat": ubicacion["lat"] if ubicacion else None,
                "lng": ubicacion["lng"] if ubicacion else None,
                "source": "Google Routes API",
                "price": None,
            })
    return candidatos


def _convertir_metros_locales(latitud, longitud, latitud_referencia):
    radio_tierra = 6_371_008.8
    coordenada_x = (
        math.radians(longitud)
        * radio_tierra
        * math.cos(math.radians(latitud_referencia))
    )
    coordenada_y = math.radians(latitud) * radio_tierra
    return coordenada_x, coordenada_y


def _distancia_punto_segmento_con_proporcion(punto, inicio, fin):
    latitud_referencia = (punto[0] + inicio[0] + fin[0]) / 3
    punto_x, punto_y = _convertir_metros_locales(
        punto[0], punto[1], latitud_referencia
    )
    inicio_x, inicio_y = _convertir_metros_locales(
        inicio[0], inicio[1], latitud_referencia
    )
    fin_x, fin_y = _convertir_metros_locales(
        fin[0], fin[1], latitud_referencia
    )

    diferencia_x = fin_x - inicio_x
    diferencia_y = fin_y - inicio_y
    longitud_cuadrada = diferencia_x ** 2 + diferencia_y ** 2
    if longitud_cuadrada == 0:
        return math.hypot(punto_x - inicio_x, punto_y - inicio_y), 0.0

    proporcion = (
        (punto_x - inicio_x) * diferencia_x
        + (punto_y - inicio_y) * diferencia_y
    ) / longitud_cuadrada
    proporcion = max(0.0, min(1.0, proporcion))

    proyeccion_x = inicio_x + proporcion * diferencia_x
    proyeccion_y = inicio_y + proporcion * diferencia_y
    distancia = math.hypot(
        punto_x - proyeccion_x,
        punto_y - proyeccion_y,
    )
    return distancia, proporcion


def _distancia_lugar_a_ruta(punto, puntos_ruta):
    """Devuelve distancia en metros y posición ordenada sobre la polilínea."""
    if len(puntos_ruta) < 2:
        return float("inf"), -1.0

    mejor_distancia = float("inf")
    mejor_indice = -1
    mejor_proporcion = 0.0

    for indice in range(len(puntos_ruta) - 1):
        distancia, proporcion = _distancia_punto_segmento_con_proporcion(
            punto,
            puntos_ruta[indice],
            puntos_ruta[indice + 1],
        )
        if distancia < mejor_distancia:
            mejor_distancia = distancia
            mejor_indice = indice
            mejor_proporcion = proporcion

    return mejor_distancia, mejor_indice + mejor_proporcion


def _interpolar_punto(punto_a, punto_b, proporcion):
    proporcion = max(0.0, min(1.0, proporcion))
    return (
        punto_a[0] + (punto_b[0] - punto_a[0]) * proporcion,
        punto_a[1] + (punto_b[1] - punto_a[1]) * proporcion,
    )


def _extremos_validacion_caseta(
    puntos_ruta,
    indice_orden,
    distancia_lado_metros=1_800,
):
    if len(puntos_ruta) < 2 or indice_orden < 0:
        return None

    indice_segmento = min(
        len(puntos_ruta) - 2,
        max(0, int(math.floor(indice_orden))),
    )
    proporcion = indice_orden - indice_segmento
    punto_cercano = _interpolar_punto(
        puntos_ruta[indice_segmento],
        puntos_ruta[indice_segmento + 1],
        proporcion,
    )

    indice_antes = indice_segmento
    acumulado = distancia_a_ruta_m(
        punto_cercano,
        [puntos_ruta[indice_antes]],
    )
    while indice_antes > 0 and acumulado < distancia_lado_metros:
        acumulado += distancia_a_ruta_m(
            puntos_ruta[indice_antes - 1],
            [puntos_ruta[indice_antes]],
        )
        indice_antes -= 1

    indice_despues = indice_segmento + 1
    acumulado = distancia_a_ruta_m(
        punto_cercano,
        [puntos_ruta[indice_despues]],
    )
    while (
        indice_despues < len(puntos_ruta) - 1
        and acumulado < distancia_lado_metros
    ):
        acumulado += distancia_a_ruta_m(
            puntos_ruta[indice_despues],
            [puntos_ruta[indice_despues + 1]],
        )
        indice_despues += 1

    punto_antes = puntos_ruta[indice_antes]
    punto_despues = puntos_ruta[indice_despues]
    if punto_antes == punto_despues:
        return None
    return punto_antes, punto_cercano, punto_despues


def _validar_peaje_en_segmento(
    puntos_ruta,
    indice_orden,
    clave_api,
    tiempo_espera=20,
):
    """Confirma que el tramo de la ruta elegida realmente reporta peaje."""
    extremos = _extremos_validacion_caseta(puntos_ruta, indice_orden)
    if extremos is None:
        return None

    punto_antes, punto_cercano, punto_despues = extremos
    cuerpo = {
        "origin": {
            "location": {
                "latLng": {
                    "latitude": punto_antes[0],
                    "longitude": punto_antes[1],
                }
            }
        },
        "destination": {
            "location": {
                "latLng": {
                    "latitude": punto_despues[0],
                    "longitude": punto_despues[1],
                }
            }
        },
        "intermediates": [{
            "location": {
                "latLng": {
                    "latitude": punto_cercano[0],
                    "longitude": punto_cercano[1],
                }
            },
            "via": True,
        }],
        "travelMode": "DRIVE",
        "routingPreference": "TRAFFIC_UNAWARE",
        "extraComputations": ["TOLLS"],
        "routeModifiers": {
            "avoidTolls": False,
            "vehicleInfo": {"emissionType": "GASOLINE"},
        },
        "languageCode": "es-MX",
        "units": "METRIC",
    }

    try:
        respuesta = requests.post(
            URL_RUTAS,
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": clave_api,
                "X-Goog-FieldMask": (
                    "routes.travelAdvisory.tollInfo,"
                    "routes.legs.travelAdvisory.tollInfo"
                ),
            },
            json=cuerpo,
            timeout=min(tiempo_espera, 20),
        )
    except requests.RequestException:
        return None

    if not respuesta.ok:
        return None

    for ruta in respuesta.json().get("routes", []):
        if "tollInfo" in ruta.get("travelAdvisory", {}):
            return True
        for tramo in ruta.get("legs", []):
            if "tollInfo" in tramo.get("travelAdvisory", {}):
                return True
    return False


def _parece_caseta(lugar):
    nombre_mostrado = lugar.get("displayName", {})
    nombre = (
        nombre_mostrado.get("text", "")
        if isinstance(nombre_mostrado, dict)
        else nombre_mostrado
    )
    direccion = lugar.get("formattedAddress", "")
    texto = _texto_normalizado(f"{nombre} {direccion}")
    tipos = {_texto_normalizado(tipo) for tipo in lugar.get("types", [])}
    tipo_principal = _texto_normalizado(lugar.get("primaryType", ""))
    palabras = (
        "caseta",
        "plaza de cobro",
        "peaje",
        "toll station",
        "toll booth",
    )
    return (
        tipo_principal == "toll_station"
        or "toll_station" in tipos
        or any(palabra in texto for palabra in palabras)
    )


def _buscar_lugares_peaje(
    puntos_ruta,
    polilinea_codificada,
    clave_api,
    tiempo_espera=35,
):
    """Busca casetas sobre la polilínea y descarta las carreteras paralelas."""
    if len(puntos_ruta) < 2 or not polilinea_codificada:
        return []

    respuesta = requests.post(
        URL_LUGARES,
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": clave_api,
            "X-Goog-FieldMask": CAMPOS_LUGARES,
        },
        json={
            "textQuery": "caseta de cobro",
            "languageCode": "es-MX",
            "regionCode": "MX",
            "pageSize": 20,
            "searchAlongRouteParameters": {
                "polyline": {"encodedPolyline": polilinea_codificada}
            },
        },
        timeout=tiempo_espera,
    )
    if not respuesta.ok:
        return []

    lugares = []
    for lugar in respuesta.json().get("places", []):
        if not _parece_caseta(lugar):
            continue

        ubicacion = lugar.get("location", {})
        try:
            punto = (
                float(ubicacion["latitude"]),
                float(ubicacion["longitude"]),
            )
        except (KeyError, TypeError, ValueError):
            continue

        distancia_ruta_m, indice_ruta = _distancia_lugar_a_ruta(
            punto,
            puntos_ruta,
        )

        # Un radio amplio incluye casetas de carreteras paralelas o salidas.
        # Solo se admiten lugares prácticamente encima de la polilínea.
        if distancia_ruta_m > 60:
            continue

        peaje_confirmado = _validar_peaje_en_segmento(
            puntos_ruta,
            indice_ruta,
            clave_api,
            tiempo_espera,
        )
        if peaje_confirmado is False:
            continue

        # Si la segunda consulta no estuvo disponible, el margen se reduce
        # todavía más para no mostrar una caseta simplemente cercana.
        if peaje_confirmado is None and distancia_ruta_m > 25:
            continue

        nombre_mostrado = lugar.get("displayName", {})
        nombre = (
            nombre_mostrado.get("text", "Caseta de cobro")
            if isinstance(nombre_mostrado, dict)
            else nombre_mostrado or "Caseta de cobro"
        )
        candidato = {
            "id": lugar.get("id", ""),
            "name": _limpiar_texto(nombre) or "Caseta de cobro",
            "address": _limpiar_texto(lugar.get("formattedAddress", "")),
            "instruction": "",
            "lat": punto[0],
            "lng": punto[1],
            "source": "Places API",
            "price": None,
            "distance_to_route_m": round(distancia_ruta_m, 2),
            "route_order": indice_ruta,
            "estimated_location": False,
        }
        if not _peaje_duplicado(candidato, lugares):
            lugares.append(candidato)

    lugares.sort(key=lambda elemento: elemento.get("route_order", 0))
    for lugar in lugares:
        lugar.pop("route_order", None)
    return lugares


def _peaje_duplicado(candidato, existentes):
    id_candidato = candidato.get("id")
    if id_candidato and any(elemento.get("id") == id_candidato for elemento in existentes):
        return True
    if candidato.get("lat") is None or candidato.get("lng") is None:
        return False
    punto = (candidato["lat"], candidato["lng"])
    return any(
        elemento.get("lat") is not None
        and elemento.get("lng") is not None
        and distancia_a_ruta_m(punto, [(elemento["lat"], elemento["lng"])]) < 750
        for elemento in existentes
    )


def _clave_tramo_peaje(instruccion, indice_tramo):
    texto = _texto_normalizado(instruccion)
    carretera = re.search(r"\b(\d{1,3})\s*d\b", texto, flags=re.IGNORECASE)
    if carretera:
        return f"{carretera.group(1)}D"
    autopista = re.search(
        r"(?:autopista|carretera)\s+([^,;]+)",
        texto,
        flags=re.IGNORECASE,
    )
    if autopista:
        nombre = _limpiar_texto(autopista.group(1))[:60]
        if nombre:
            return _texto_normalizado(nombre)
    return f"tramo-{indice_tramo + 1}"


def _casetas_estimadas(ruta):
    """Crea una sola referencia por tramo de cuota, siempre sobre la ruta."""
    if not ruta.get("has_tolls"):
        return []

    tramos = ruta.get("legs", [])
    hay_peaje_por_tramo = any(
        "tollInfo" in tramo.get("travelAdvisory", {})
        for tramo in tramos
    )
    grupos = {}
    orden_global = 0
    tramos_con_peaje = []

    for indice_tramo, tramo in enumerate(tramos):
        aviso = tramo.get("travelAdvisory", {})
        if "tollInfo" not in aviso and hay_peaje_por_tramo:
            continue

        pasos = tramo.get("steps", [])
        tramos_con_peaje.append((indice_tramo, pasos))
        for paso in pasos:
            orden_global += 1
            if not _es_paso_peaje(paso):
                continue
            ubicacion = _ubicacion_de_paso(paso, "startLocation")
            if ubicacion is None:
                continue
            instruccion = _limpiar_texto(
                paso.get("navigationInstruction", {}).get("instructions", "")
            )
            clave = _clave_tramo_peaje(instruccion, indice_tramo)
            grupos.setdefault(clave, []).append({
                **ubicacion,
                "instruction": instruccion,
                "order": orden_global,
                "key": clave,
            })

    seleccionadas = []
    for clave, candidatos in grupos.items():
        candidatos.sort(key=lambda candidato: candidato["order"])
        seleccionadas.append(candidatos[len(candidatos) // 2].copy())

    if not seleccionadas:
        for indice_tramo, pasos in tramos_con_peaje:
            if not pasos:
                continue
            paso = pasos[len(pasos) // 2]
            ubicacion = _ubicacion_de_paso(paso, "startLocation")
            if ubicacion is None:
                continue
            seleccionadas.append({
                **ubicacion,
                "instruction": _limpiar_texto(
                    paso.get("navigationInstruction", {}).get("instructions", "")
                ),
                "order": indice_tramo,
                "key": f"tramo-{indice_tramo + 1}",
            })

    seleccionadas.sort(key=lambda candidato: candidato["order"])
    resultado = []
    for indice, candidato in enumerate(seleccionadas, start=1):
        clave = candidato["key"]
        nombre = (
            f"Peaje estimado en {clave}"
            if not clave.startswith("tramo-")
            else f"Punto de peaje estimado {indice}"
        )
        resultado.append({
            "name": nombre,
            "address": "",
            "instruction": candidato["instruction"],
            "lat": candidato["lat"],
            "lng": candidato["lng"],
            "source": "Routes API",
            "price": None,
            "estimated_location": True,
        })
    return resultado


def encontrar_peajes(ruta, clave_api, tiempo_espera=35):
    if not ruta.get("has_tolls"):
        return [], "Sin peajes"

    polilinea = ruta.get("polyline", "")
    puntos_ruta = decodificar_polilinea(polilinea)
    try:
        lugares = _buscar_lugares_peaje(
            puntos_ruta,
            polilinea,
            clave_api,
            tiempo_espera,
        )
    except requests.RequestException:
        lugares = []

    if lugares:
        return lugares, "Casetas verificadas sobre la ruta"

    # El respaldo se forma con los pasos de la ruta elegida, no con lugares
    # cercanos. Por eso los marcadores permanecen encima del recorrido.
    estimadas = _casetas_estimadas(ruta)
    return estimadas, "Puntos de peaje de la ruta"
