import heapq
import math


RADIO_TIERRA_KM = 6371.0088


def decodificar_polilinea(codificada):
    """Decodifica una polyline de Google en pares (latitud, longitud)."""
    if not codificada:
        return []

    coordenadas = []
    indice = latitud = longitud = 0
    longitud_codificada = len(codificada)

    while indice < longitud_codificada:
        for eje in ("lat", "lng"):
            desplazamiento = resultado = 0
            while True:
                if indice >= longitud_codificada:
                    return coordenadas
                valor = ord(codificada[indice]) - 63
                indice += 1
                resultado |= (valor & 0x1F) << desplazamiento
                desplazamiento += 5
                if valor < 0x20:
                    break
            diferencia = ~(resultado >> 1) if resultado & 1 else resultado >> 1
            if eje == "lat":
                latitud += diferencia
            else:
                longitud += diferencia
        coordenadas.append((latitud / 1e5, longitud / 1e5))

    return coordenadas


def distancia_manhattan_km(punto_a, punto_b):
    """Distancia Manhattan aproximada sobre la superficie terrestre."""
    latitud1, longitud1 = punto_a
    latitud2, longitud2 = punto_b
    latitud_media = math.radians((latitud1 + latitud2) / 2)
    latitud_km = abs(latitud2 - latitud1) * 111.32
    longitud_km = abs(longitud2 - longitud1) * 111.32 * math.cos(latitud_media)
    return latitud_km + longitud_km


def _nodo(punto):
    return round(float(punto[0]), 6), round(float(punto[1]), 6)


def construir_grafo_cadena(puntos):
    grafo = {}
    normalizados = [_nodo(punto) for punto in puntos]
    for inicio, fin in zip(normalizados, normalizados[1:]):
        peso = distancia_manhattan_km(inicio, fin)
        grafo.setdefault(inicio, []).append((fin, peso))
        grafo.setdefault(fin, []).append((inicio, peso))
    if normalizados:
        grafo.setdefault(normalizados[0], [])
    return grafo, normalizados


def aplicar_dijkstra(grafo, origen, destino):
    distancias = {origen: 0.0}
    anteriores = {}
    cola = [(0.0, origen)]

    while cola:
        distancia_actual, actual = heapq.heappop(cola)
        if actual == destino:
            break
        if distancia_actual > distancias.get(actual, float("inf")):
            continue

        for vecino, peso in grafo.get(actual, []):
            candidato = distancia_actual + peso
            if candidato < distancias.get(vecino, float("inf")):
                distancias[vecino] = candidato
                anteriores[vecino] = actual
                heapq.heappush(cola, (candidato, vecino))

    if destino not in distancias:
        return [], float("inf")

    camino = [destino]
    while camino[-1] != origen:
        camino.append(anteriores[camino[-1]])
    camino.reverse()
    return camino, distancias[destino]


def analizar_ruta(ruta):
    puntos = decodificar_polilinea(ruta.get("polyline", ""))
    if len(puntos) < 2:
        return None

    grafo, nodos = construir_grafo_cadena(puntos)
    camino, distancia = aplicar_dijkstra(grafo, nodos[0], nodos[-1])
    return {
        "points": camino,
        "manhattan_distance_km": distancia,
        "node_count": len(camino),
    }


def seleccionar_mejor_ruta(rutas):
    """Evalúa alternativas de Google y elige la de menor peso Manhattan.

    Dijkstra se ejecuta sobre la secuencia de puntos de cada alternativa.
    Google propone las alternativas y este motor selecciona una de ellas.
    """
    candidatos = []
    for indice, ruta in enumerate(rutas):
        analisis = analizar_ruta(ruta)
        if analisis:
            candidatos.append((analisis["manhattan_distance_km"], indice, analisis))

    if not candidatos:
        raise ValueError("Ninguna ruta contiene una polyline válida.")

    _, indice, analisis = min(candidatos, key=lambda elemento: elemento[0])
    return {
        "route_index": indice,
        "coordinates": [
            {"lat": punto[0], "lng": punto[1]}
            for punto in analisis["points"]
        ],
        "distance_km": round(analisis["manhattan_distance_km"], 2),
        "distance_m": round(analisis["manhattan_distance_km"] * 1000, 2),
        "node_count": analisis["node_count"],
        "algorithm": "Alternativas de Google + distancia Manhattan + Dijkstra",
    }
