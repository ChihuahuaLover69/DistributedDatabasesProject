from flask import Flask
from flask_cors import CORS

from routes.clientes import bp_clientes
from routes.direcciones import bp_direcciones
from routes.tablas import bp_tablas

app = Flask(__name__)
CORS(app)

app.register_blueprint(bp_clientes)
app.register_blueprint(bp_direcciones)
app.register_blueprint(bp_tablas)

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
