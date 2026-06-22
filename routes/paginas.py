from flask import Blueprint, jsonify

plano_paginas = Blueprint("paginas", __name__)


@plano_paginas.get("/")
def inicio():
    return jsonify({
        "status": "ok",
        "message": "GPS Backend API funcionando correctamente",
        "endpoints": {
            "health": "/api/health",
            "route": "/api/route",
            "zones": "/api/zones"
        }
    })