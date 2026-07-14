import pyotp
import os

totp_secret = os.getenv("TOTP_SECRET")
if not totp_secret:
    raise RuntimeError("Environment variable TOTP_SECRET is required.")

totp = pyotp.TOTP(totp_secret)
print(totp.now())


from pathlib import Path

# ─── Inicialização de paths ───
# Garante que o cwd (diretório de trabalho) seja sempre a raiz do projeto,
# i.e. a pasta dash_way_group onde estão app.py, /scripts, /dados, /uploads, etc.
PROJECT_ROOT = Path(__file__).resolve().parent
os.chdir(PROJECT_ROOT)
