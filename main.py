# Alias: expõe "app" para quem faz "from main import app"
# Se seu entrypoint real NÃO for app.py, troque abaixo para o arquivo certo.
from app import app as app

if __name__ == "__main__":
    # dev only; em produção usamos gunicorn
    app.run(host="0.0.0.0", port=8080, debug=False)
