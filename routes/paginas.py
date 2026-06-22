from flask import Blueprint, current_app, render_template


plano_paginas = Blueprint("paginas", __name__)


@plano_paginas.get("/")
def inicio():
    return render_template(
        "index.html",
        clave_api_google=current_app.config["CLAVE_API_GOOGLE"],
        rendimiento_predeterminado=current_app.config["RENDIMIENTO_PREDETERMINADO_KM_L"],
    )
