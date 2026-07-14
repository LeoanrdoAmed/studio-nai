# wsgi.py
# Tenta importar a aplicaÃ§Ã£o:
# - Flask: de app.py ? app
# - Dash:  de app.py ? app.server
def _load_app():
    # Evita problemas de encoding em prints/logs
    try:
        from app import app as candidate
    except Exception:
        try:
            from main import app as candidate
        except Exception as e:
            raise RuntimeError("NÃ£o achei 'app' em app.py nem main.py") from e
    # Se for Dash, o Flask estÃ¡ em .server
    return getattr(candidate, "server", candidate)

app = _load_app()
