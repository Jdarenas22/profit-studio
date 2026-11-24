"""Una aplicación normal en 127.0.0.1:8000; publicación opcional del mismo proceso."""

import argparse
import hashlib
import os
from pathlib import Path
import queue
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / ".runtime"
CLOUDFLARED_VERSION = "2026.11.2"
CLOUDFLARED_SHA256 = "555b46c85582461d189cac30d14ad05480d612613801be9f110b8ce08c5b2113"


class StartupError(Exception):
    pass


def connector():
    installed = shutil.which("cloudflared")
    if installed:
        return installed
    binary = ROOT / ".tools/cloudflared.exe"
    if sys.platform != "win32":
        raise StartupError(
            "Instala cloudflared con las instrucciones oficiales para tu sistema."
        )
    if not binary.exists():
        binary.parent.mkdir(exist_ok=True)
        url = f"https://github.com/cloudflare/cloudflared/releases/download/{CLOUDFLARED_VERSION}/cloudflared-windows-amd64.exe"
        partial = binary.with_suffix(".download")
        with urllib.request.urlopen(url, timeout=60) as response, partial.open(
            "wb"
        ) as out:
            shutil.copyfileobj(response, out)
        if hashlib.sha256(partial.read_bytes()).hexdigest() != CLOUDFLARED_SHA256:
            partial.unlink()
            raise StartupError(
                "La huella de cloudflared no coincide. No se ejecuta el archivo."
            )
        partial.replace(binary)
    if hashlib.sha256(binary.read_bytes()).hexdigest() != CLOUDFLARED_SHA256:
        raise StartupError("cloudflared local no coincide con la versión verificada.")
    return str(binary)


def start_tunnel():
    # Configuración propia vacía: no modifica ni lee credenciales de túneles existentes.
    config = RUNTIME / "tunnel.yml"
    config.write_text("no-autoupdate: true\n", encoding="utf-8")
    process = subprocess.Popen(
        [
            connector(),
            "tunnel",
            "--config",
            str(config),
            "--url",
            "http://127.0.0.1:8000",
            "--no-autoupdate",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    lines = queue.Queue()

    def read():
        with (RUNTIME / "tunnel.log").open("w", encoding="utf-8") as log:
            for line in process.stdout:
                log.write(line)
                log.flush()
                lines.put(line)

    threading.Thread(target=read, daemon=True).start()
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and process.poll() is None:
        try:
            line = lines.get(timeout=1)
        except queue.Empty:
            continue
        match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
        if match:
            return process, match.group(0)
    process.terminate()
    raise StartupError("No se obtuvo la URL del túnel; revisa .runtime/tunnel.log.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public", action="store_true")
    options = parser.parse_args()
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    import environ

    environ.Env.read_env(ROOT / ".env")
    os.environ.update(
        DJANGO_SETTINGS_MODULE="config.settings.development",
        DEBUG="false",
        AI_PLANS_WORKER="false",
        AI_PLANS_RUN_INLINE="false",
    )
    os.environ.setdefault("AI_PLANS_MODE", "live")
    os.environ.update(ALLOWED_HOSTS="127.0.0.1,localhost,[::1]", PUBLIC_BASE_URL="")
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", 8000))
        except OSError:
            raise StartupError(
                "El puerto 8000 ya está ocupado. No se inicia otra aplicación."
            ) from None
    RUNTIME.mkdir(exist_ok=True)
    tunnel = None
    try:
        public_url = ""
        if options.public:
            tunnel, public_url = start_tunnel()
            os.environ["PUBLIC_BASE_URL"] = public_url
            os.environ["ALLOWED_HOSTS"] += "," + urlsplit(public_url).hostname
        import django

        django.setup()
        from django.conf import settings
        from django.core.management import call_command
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor
        from config.security import secret_key_weaknesses
        from apps.plans.ai.policy import configuration_errors

        if secret_key_weaknesses(settings.SECRET_KEY):
            raise StartupError(
                "Configura una SECRET_KEY fuerte en .env antes de publicar."
            )
        if settings.AI_PLANS_MODE != "live" or configuration_errors(settings):
            raise StartupError(
                "Configura Gemini real y su nivel Free en .env. No se usa un generador de ejemplo."
            )
        executor = MigrationExecutor(connection)
        pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
        database = ROOT / "db.sqlite3"
        if pending and database.exists() and database.stat().st_size:
            folder = RUNTIME / "backups"
            folder.mkdir(exist_ok=True)
            backup = folder / (
                "before-migrate-" + time.strftime("%Y%m%d-%H%M%S") + ".sqlite3"
            )
            with sqlite3.connect(database) as source, sqlite3.connect(backup) as target:
                source.backup(target)
        call_command("migrate", interactive=False, verbosity=0)
        call_command("load_usda_catalog", apply=True, activate=True)
        call_command("collectstatic", interactive=False, verbosity=0)
        (RUNTIME / "public-url.txt").write_text(public_url, encoding="utf-8")
        (RUNTIME / "server.pid").write_text(str(os.getpid()), encoding="ascii")
        print("ProFit Studio: http://127.0.0.1:8000/", flush=True)
        if public_url:
            print("Internet: " + public_url, flush=True)
        print(
            "Gemini real; una sola base y aplicación. Ctrl+C detiene servidor y túnel.",
            flush=True,
        )
        from django.core.wsgi import get_wsgi_application
        from waitress import serve

        serve(
            get_wsgi_application(),
            host="127.0.0.1",
            port=8000,
            threads=4,
            trusted_proxy="127.0.0.1",
            trusted_proxy_headers={"x-forwarded-proto"},
            clear_untrusted_proxy_headers=True,
        )
    finally:
        if tunnel is not None:
            tunnel.terminate()
            try:
                tunnel.wait(timeout=5)
            except subprocess.TimeoutExpired:
                tunnel.kill()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as error:
        # No imprimir mensajes de proveedores o conexiones que puedan contener credenciales.
        if isinstance(error, StartupError):
            print(str(error), file=sys.stderr)
        else:
            print(
                "No se pudo iniciar ProFit Studio: " + type(error).__name__,
                file=sys.stderr,
            )
        sys.exit(1)
