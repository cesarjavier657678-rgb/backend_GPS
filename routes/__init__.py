from .api import plano_api
from .paginas import plano_paginas


def registrar_rutas(aplicacion):
    aplicacion.register_blueprint(plano_paginas)
    aplicacion.register_blueprint(plano_api)
