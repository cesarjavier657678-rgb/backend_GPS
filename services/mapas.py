import requests

from services.motor_rutas import seleccionar_mejor_ruta
from services.casetas import encontrar_peajes
from services.zonas import zonas_intersectan_ruta


URL_GEOCODIFICACION = "https://maps.googleapis.com/maps/api/geocode/json"
URL_RUTAS = "https://routes.googleapis.com/directions/v2:computeRoutes"
CAMPOS_RUTAS = ",".join([
    "routes.distanceMeters",
    "routes.duration",
    "routes.polyline.encodedPolyline",
    "routes.travelAdvisory.tollInfo",
    "routes.legs.travelAdvisory.tollInfo",
    "routes.legs.steps",
])


class ErrorGoogleMaps(RuntimeError):
    pass


def _validar_clave(clave_api):
    clave = str(clave_api or "").strip()
    if not clave or clave.upper().startswith("COLOCA_AQUI"):
        raise ErrorGoogleMaps(
            "Configura CLAVE_API_GOOGLE dentro de configuracion.py antes de calcular rutas."
        )
    return clave


def _componente_direccion(componentes, tipos_aceptados):
    for componente in componentes:
        if any(elemento in componente.get("types", []) for elemento in tipos_aceptados):
            return componente.get("long_name", "")
    return ""


def geocodificar(direccion, clave_api, tiempo_espera=35):
    clave_api = _validar_clave(clave_api)
    respuesta = requests.get(
        URL_GEOCODIFICACION,
        params={
            "address": direccion,
            "region": "mx",
            "language": "es",
            "key": clave_api,
        },
        timeout=tiempo_espera,
    )
    respuesta.raise_for_status()
    contenido = respuesta.json()

    if contenido.get("status") != "OK" or not contenido.get("results"):
        detalle = contenido.get("error_message") or contenido.get("status") or "sin resultados"
        raise ErrorGoogleMaps(f"No se encontró la dirección: {detalle}.")

    resultado = contenido["results"][0]
    ubicacion = resultado.get("geometry", {}).get("location", {})
    componentes = resultado.get("address_components", [])
    ciudad = _componente_direccion(componentes, ["locality", "postal_town"])
    if not ciudad:
        ciudad = _componente_direccion(
            componentes,
            ["administrative_area_level_2", "sublocality"],
        )

    return {
        "lat": float(ubicacion["lat"]),
        "lng": float(ubicacion["lng"]),
        "address": resultado.get("formatted_address", direccion),
        "city": ciudad,
        "state": _componente_direccion(componentes, ["administrative_area_level_1"]),
        "country": _componente_direccion(componentes, ["country"]),
        "place_id": resultado.get("place_id", ""),
    }


def _duracion_segundos(valor):
    texto = str(valor or "0s").strip()
    try:
        return float(texto[:-1]) if texto.endswith("s") else 0.0
    except ValueError:
        return 0.0


def _valor_monetario(dinero):
    try:
        unidades = float(dinero.get("units", 0) or 0)
        nanos = float(dinero.get("nanos", 0) or 0)
    except (TypeError, ValueError):
        return 0.0
    return round(unidades + nanos / 1_000_000_000, 2)


def _combinar_info_peajes(ruta):
    aviso = ruta.get("travelAdvisory", {})
    peaje_ruta = aviso.get("tollInfo")
    if peaje_ruta and peaje_ruta.get("estimatedPrice"):
        return peaje_ruta, True

    totales = {}
    hay_peaje = "tollInfo" in aviso
    for tramo in ruta.get("legs", []):
        aviso_tramo = tramo.get("travelAdvisory", {})
        if "tollInfo" not in aviso_tramo:
            continue
        hay_peaje = True
        for precio in aviso_tramo.get("tollInfo", {}).get("estimatedPrice", []):
            moneda = precio.get("currencyCode", "MXN")
            totales[moneda] = totales.get(moneda, 0.0) + _valor_monetario(precio)

    if not totales:
        return {}, hay_peaje

    precios = []
    for moneda, total in totales.items():
        unidades = int(total)
        nanos = int(round((total - unidades) * 1_000_000_000))
        precios.append({
            "currencyCode": moneda,
            "units": str(unidades),
            "nanos": nanos,
        })
    return {"estimatedPrice": precios}, True


def _resumen_peajes(ruta):
    informacion_peaje, presente = _combinar_info_peajes(ruta)
    precios = informacion_peaje.get("estimatedPrice", [])
    if not precios:
        return {
            "has_tolls": presente,
            "price_available": False,
            "cost": 0.0,
            "currency": "MXN",
        }

    seleccionada = next(
        (precio for precio in precios if precio.get("currencyCode") == "MXN"),
        precios[0],
    )
    return {
        "has_tolls": True,
        "price_available": True,
        "cost": _valor_monetario(seleccionada),
        "currency": seleccionada.get("currencyCode", "MXN"),
    }


def _valores_vehiculo(tipo_vehiculo):
    solicitado = str(tipo_vehiculo or "GASOLINE").strip().upper()
    if solicitado not in {"GASOLINE", "TRUCK"}:
        raise ErrorGoogleMaps("El tipo de vehículo debe ser GASOLINE o TRUCK.")
    return solicitado, "DIESEL" if solicitado == "TRUCK" else "GASOLINE"


def calcular_rutas_google(origen, destino, tipo_vehiculo, clave_api, tiempo_espera=35):
    clave_api = _validar_clave(clave_api)
    tipo_solicitado, tipo_emision = _valores_vehiculo(tipo_vehiculo)

    cuerpo = {
        "origin": {
            "location": {
                "latLng": {"latitude": origen[0], "longitude": origen[1]}
            }
        },
        "destination": {
            "location": {
                "latLng": {"latitude": destino[0], "longitude": destino[1]}
            }
        },
        "travelMode": "DRIVE",
        "routingPreference": "TRAFFIC_AWARE",
        "computeAlternativeRoutes": True,
        "polylineQuality": "HIGH_QUALITY",
        "polylineEncoding": "ENCODED_POLYLINE",
        "languageCode": "es-MX",
        "regionCode": "MX",
        "units": "METRIC",
        "extraComputations": ["TOLLS"],
        "routeModifiers": {
            "avoidTolls": False,
            "avoidFerries": True,   # ← Evita transbordadores
            "vehicleInfo": {"emissionType": tipo_emision},
        },
    }

    respuesta = requests.post(
        URL_RUTAS,
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": clave_api,
            "X-Goog-FieldMask": CAMPOS_RUTAS,
        },
        json=cuerpo,
        timeout=tiempo_espera,
    )

    if not respuesta.ok:
        try:
            detalle = respuesta.json()
        except ValueError:
            detalle = respuesta.text
        raise ErrorGoogleMaps(
            f"El servicio de rutas no pudo calcular la ruta ({respuesta.status_code}): {detalle}"
        )

    rutas = []
    for indice, ruta in enumerate(respuesta.json().get("routes", [])):
        distancia_m = int(ruta.get("distanceMeters", 0) or 0)
        duracion_s = _duracion_segundos(ruta.get("duration", "0s"))
        peaje = _resumen_peajes(ruta)
        advertencias = []

        if tipo_solicitado == "TRUCK":
            advertencias.append(
                "No se dispone de una tarifa comercial específica para camión; "
                "se usa DIESEL como tipo de emisión."
            )
        if peaje["has_tolls"] and not peaje["price_available"]:
            advertencias.append(
                "Se detectaron peajes, pero no se recibió un precio estimado."
            )

        rutas.append({
            "index": indice,
            "polyline": ruta.get("polyline", {}).get("encodedPolyline", ""),
            "legs": ruta.get("legs", []),
            "distance_m": distancia_m,
            "distance_km": round(distancia_m / 1000, 2),
            "duration_s": round(duracion_s),
            "duration_min": round(duracion_s / 60),
            "toll_cost": peaje["cost"],
            "toll_currency": peaje["currency"],
            "has_tolls": peaje["has_tolls"],
            "toll_price_available": peaje["price_available"],
            "vehicle_type": tipo_solicitado,
            "emission_type": tipo_emision,
            "toll_warnings": advertencias,
        })

    if not rutas:
        raise ErrorGoogleMaps("El servicio de rutas no devolvió rutas disponibles.")
    return rutas


def calcular_viaje(
    origen,
    destino,
    tipo_vehiculo,
    rendimiento_km_l,
    precio_gasolina_mxn,
    clave_api,
    tiempo_espera,
    zonas,
    vehicle_real_type=None,
    toll_price_per_booth=None,
):
    lugar_origen = geocodificar(origen, clave_api, tiempo_espera)
    lugar_destino = geocodificar(destino, clave_api, tiempo_espera)
    punto_origen = (lugar_origen["lat"], lugar_origen["lng"])
    punto_destino = (lugar_destino["lat"], lugar_destino["lng"])

    rutas = calcular_rutas_google(
        punto_origen,
        punto_destino,
        tipo_vehiculo,
        clave_api,
        tiempo_espera,
    )
    analisis_ruta = seleccionar_mejor_ruta(rutas)
    seleccionada = rutas[analisis_ruta["route_index"]]
    puntos_ruta = [
        (punto["lat"], punto["lng"])
        for punto in analisis_ruta["coordinates"]
    ]

    peajes, fuente_peajes = encontrar_peajes(seleccionada, clave_api, tiempo_espera)
    zonas_intersectadas = zonas_intersectan_ruta(puntos_ruta, zonas)

    distancia_km = float(seleccionada.get("distance_km") or analisis_ruta["distance_km"])
    litros = distancia_km / rendimiento_km_l
    costo_combustible = litros * precio_gasolina_mxn

    # ===== Cálculo de peajes según tipo de vehículo =====
    # Por defecto, usar el costo que devuelve Google
    costo_peajes = float(seleccionada.get("toll_cost", 0) or 0)
    numero_casetas = len(peajes) if seleccionada.get("has_tolls", False) else 0

    # Si es tráiler y se envió un precio por caseta, recalcular
    if vehicle_real_type == "TRAILER" and toll_price_per_booth is not None:
        if seleccionada.get("has_tolls", False) and numero_casetas > 0:
            costo_peajes = round(toll_price_per_booth * numero_casetas, 2)
        else:
            costo_peajes = 0.0  # No hay casetas, costo cero

    # Si es motocicleta, aplicar 50% de descuento sobre el costo original (o el calculado)
    if vehicle_real_type == "MOTORCYCLE":
        costo_peajes = round(costo_peajes * 0.5, 2)

    return {
        "origin": {
            "query": origen,
            "address": lugar_origen["address"],
            "city": lugar_origen["city"],
            "state": lugar_origen["state"],
            "lat": lugar_origen["lat"],
            "lng": lugar_origen["lng"],
        },
        "destination": {
            "query": destino,
            "address": lugar_destino["address"],
            "city": lugar_destino["city"],
            "state": lugar_destino["state"],
            "lat": lugar_destino["lat"],
            "lng": lugar_destino["lng"],
        },
        "polyline": seleccionada["polyline"],
        "coordinates": analisis_ruta["coordinates"],
        "distance_km": round(distancia_km, 2),
        "google_distance_km": seleccionada["distance_km"],
        "manhattan_distance_km": analisis_ruta["distance_km"],
        "duration_min": seleccionada["duration_min"],
        "algorithm": analisis_ruta["algorithm"],
        "route_nodes": analisis_ruta["node_count"],
        "selected_google_route": analisis_ruta["route_index"],
        "alternatives_count": len(rutas),
        "vehicle_type": seleccionada["vehicle_type"],
        "emission_type": seleccionada["emission_type"],
        "efficiency_km_l": round(rendimiento_km_l, 2),
        "gas_price_mxn": round(precio_gasolina_mxn, 2),
        "estimated_liters": round(litros, 2),
        "fuel_cost_mxn": round(costo_combustible, 2),
        "has_tolls": seleccionada["has_tolls"],
        "toll_price_available": seleccionada["toll_price_available"],
        "toll_cost": round(costo_peajes, 2),
        "toll_currency": seleccionada["toll_currency"],
        "tolls": peajes,
        "toll_count": numero_casetas,
        "toll_source": fuente_peajes,
        "toll_warnings": seleccionada["toll_warnings"],
        "total_cost_mxn": round(costo_combustible + costo_peajes, 2),
        "has_red_zones": bool(zonas_intersectadas),
        "red_zones": zonas_intersectadas,
    }