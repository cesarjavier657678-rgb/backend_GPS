from flask import Flask
from flask_cors import CORS 

from configuracion import Configuracion
from routes import registrar_rutas


def crear_aplicacion(configuracion_prueba=None):

    aplicacion = Flask(
        __name__,
        template_folder="templates",   # Opcional, ya no se usa
        static_folder="static",        # Opcional, ya no se usa
    )
    aplicacion.config.from_object(Configuracion)

    if configuracion_prueba:
        aplicacion.config.update(configuracion_prueba)

  
    CORS(aplicacion, resources={r"/api/*": {"origins": "*"}})


    registrar_rutas(aplicacion)

    return aplicacion



aplicacion = crear_aplicacion()


if __name__ == "__main__":
    aplicacion.run(
        host=aplicacion.config["SERVIDOR"],
        port=aplicacion.config["PUERTO"],
        debug=aplicacion.config["DEPURACION"],
    )