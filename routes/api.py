import requests
from flask import Blueprint, current_app, jsonify, request

from services.mapas import ErrorGoogleMaps, calcular_viaje
from services.zonas import (
    ErrorZonaNoEncontrada,
    ErrorValidacionZona,
    crear_zona,
    eliminar_zona,
    listar_zonas,
    actualizar_zona,
)


# Crear el blueprint con prefijo /api
plano_api = Blueprint("api", __name__, url_prefix="/api")


def respuesta_error(mensaje, estado_http=400):
    """Helper para respuestas de error consistentes."""
    return jsonify({"ok": False, "error": mensaje}), estado_http


@plano_api.get("/health")
def estado_servidor():
    """Endpoint para verificar que el servidor está activo."""
    return jsonify({"ok": True, "service": "gps"})


@plano_api.post("/route")
def calcular_ruta_api():
    """
    Calcula una ruta entre origen y destino.
    Espera un JSON con:
      - origin: str
      - destination: str
      - vehicle_type: str (GASOLINE o TRUCK)
      - efficiency_km_l: float
      - (opcional) vehicle_real_type: str (SUV, PICKUP, MOTORCYCLE, TRAILER)
      - (opcional) toll_price_per_booth: float (para tráiler)
      - (opcional) fuel_price_mxn: float (precio de combustible)
    """
    datos = request.get_json(silent=True) or {}
    origen = str(datos.get("origin", "")).strip()
    destino = str(datos.get("destination", "")).strip()

    # Tipo de vehículo para Google (solo GASOLINE o TRUCK)
    tipo_vehiculo = str(
        datos.get("vehicle_type", current_app.config.get("TIPO_VEHICULO_PREDETERMINADO", "GASOLINE"))
    ).strip().upper()

    # Tipo real (para ajustes de peaje como moto o tráiler)
    tipo_real = datos.get("vehicle_real_type", tipo_vehiculo)

    # Precio por caseta para tráiler (opcional)
    toll_price_per_booth = datos.get("toll_price_per_booth")
    if toll_price_per_booth is not None:
        try:
            toll_price_per_booth = float(toll_price_per_booth)
        except (TypeError, ValueError):
            return respuesta_error("El precio por caseta debe ser un número válido.", 400)

    # Precio del combustible (puede ser diésel)
    precio_combustible = datos.get("fuel_price_mxn")
    if precio_combustible is None:
        precio_combustible = current_app.config.get("PRECIO_GASOLINA_MXN", 23.70)
    else:
        try:
            precio_combustible = float(precio_combustible)
        except (TypeError, ValueError):
            return respuesta_error("El precio del combustible debe ser un número válido.", 400)

    # Rendimiento
    try:
        rendimiento = float(
            datos.get(
                "efficiency_km_l",
                current_app.config.get("RENDIMIENTO_PREDETERMINADO_KM_L", 14.0),
            )
        )
    except (TypeError, ValueError):
        return respuesta_error("El rendimiento debe ser un número válido.")

    # Validaciones básicas
    if not origen or not destino:
        return respuesta_error("Debes indicar un origen y un destino.")
    if tipo_vehiculo not in {"GASOLINE", "TRUCK"}:
        return respuesta_error("El tipo de vehículo debe ser GASOLINE o TRUCK.")
    if rendimiento <= 0 or rendimiento > 100:
        return respuesta_error("El rendimiento debe estar entre 0 y 100 km/l.")

    try:
        resultado = calcular_viaje(
            origen=origen,
            destino=destino,
            tipo_vehiculo=tipo_vehiculo,
            vehicle_real_type=tipo_real,
            rendimiento_km_l=rendimiento,
            precio_gasolina_mxn=precio_combustible,
            clave_api=current_app.config.get("CLAVE_API_GOOGLE"),
            tiempo_espera=current_app.config.get("TIEMPO_ESPERA_GOOGLE_SEGUNDOS", 35),
            zonas=listar_zonas(current_app.config.get("ARCHIVO_ZONAS", "data/zonas_rojas.json")),
            toll_price_per_booth=toll_price_per_booth,
        )
        return jsonify({"ok": True, "data": resultado})
    except ErrorGoogleMaps as error:
        return respuesta_error(str(error), 400)
    except requests.RequestException:
        current_app.logger.exception("No fue posible contactar Google Maps")
        return respuesta_error("No fue posible conectar con Google Maps.", 502)
    except Exception:
        current_app.logger.exception("Error inesperado calculando la ruta")
        return respuesta_error("Ocurrió un error inesperado al calcular la ruta.", 500)


@plano_api.get("/zones")
def consultar_zonas():
    """Obtiene todas las zonas rojas."""
    try:
        zonas = listar_zonas(current_app.config.get("ARCHIVO_ZONAS", "data/zonas_rojas.json"))
        return jsonify({"ok": True, "data": zonas})
    except Exception as e:
        current_app.logger.exception("Error al consultar zonas")
        return respuesta_error(str(e), 500)


@plano_api.post("/zones")
def crear_zona_api():
    """Crea una nueva zona roja."""
    try:
        zona = crear_zona(
            current_app.config.get("ARCHIVO_ZONAS", "data/zonas_rojas.json"),
            request.get_json(silent=True) or {},
        )
        return jsonify({"ok": True, "data": zona}), 201
    except ErrorValidacionZona as error:
        return respuesta_error(str(error), 400)
    except Exception as e:
        current_app.logger.exception("Error al crear zona")
        return respuesta_error(str(e), 500)


@plano_api.put("/zones/<id_zona>")
def actualizar_zona_api(id_zona):
    """Actualiza una zona roja existente."""
    try:
        zona = actualizar_zona(
            current_app.config.get("ARCHIVO_ZONAS", "data/zonas_rojas.json"),
            id_zona,
            request.get_json(silent=True) or {},
        )
        return jsonify({"ok": True, "data": zona})
    except ErrorValidacionZona as error:
        return respuesta_error(str(error), 400)
    except ErrorZonaNoEncontrada as error:
        return respuesta_error(str(error), 404)
    except Exception as e:
        current_app.logger.exception("Error al actualizar zona")
        return respuesta_error(str(e), 500)


@plano_api.delete("/zones/<id_zona>")
def eliminar_zona_api(id_zona):
    """Elimina una zona roja."""
    try:
        eliminar_zona(current_app.config.get("ARCHIVO_ZONAS", "data/zonas_rojas.json"), id_zona)
        return jsonify({"ok": True, "message": "Zona eliminada correctamente."})
    except ErrorZonaNoEncontrada as error:
        return respuesta_error(str(error), 404)
    except Exception as e:
        current_app.logger.exception("Error al eliminar zona")
        return respuesta_error(str(e), 500)