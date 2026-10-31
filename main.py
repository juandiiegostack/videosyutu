#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Descargador de YouTube en 1080p.

Aplicación de escritorio construida con customtkinter y yt-dlp. Permite
descargar videos individuales o listas completas, elegir calidad y extraer
el audio como MP3.

La aplicación intenta mantenerse actualizada por sí sola:
- Actualiza yt-dlp al iniciar (en segundo plano).
- Si una descarga falla, actualiza yt-dlp y reintenta una vez.
- Guarda la configuración en config.json y los detalles en log.txt.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import json
import logging
import logging.handlers
import os
import platform
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox

    import customtkinter as ctk
except ImportError as exc:
    # Normalmente el archivo .bat detecta esto antes de abrir la interfaz.
    print(
        "No se pudieron cargar las dependencias de la interfaz.\n"
        "Ejecuta: pip install -r requirements.txt\n"
        f"Detalle: {exc}"
    )
    raise SystemExit(1) from exc


# ---------------------------------------------------------------------------
# Constantes generales
# ---------------------------------------------------------------------------

NOMBRE_APP = "Descargador de YouTube"
VERSION_APP = "1.0.0"
PLANTILLA_SALIDA = "%(title)s.%(ext)s"

# Cada etiqueta visible se asocia con un identificador que se guarda en
# config.json y con el selector de formatos de yt-dlp.
CALIDADES: dict[str, dict[str, str]] = {
    "1080p (MP4)": {
        "id": "1080p",
        "formato": "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
    },
    "720p (MP4)": {
        "id": "720p",
        "formato": "bestvideo[height<=720]+bestaudio/best[height<=720]",
    },
    "480p (MP4)": {
        "id": "480p",
        "formato": "bestvideo[height<=480]+bestaudio/best[height<=480]",
    },
    "Solo audio (MP3)": {
        "id": "audio",
        "formato": "bestaudio/best",
    },
}

ETIQUETA_POR_ID = {datos["id"]: etiqueta for etiqueta, datos in CALIDADES.items()}
CALIDAD_POR_DEFECTO = "1080p"

# Se usa para que el subprocess de pip no abra una consola negra en Windows.
FLAGS_SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ---------------------------------------------------------------------------
# Ubicación de los archivos de la aplicación
# ---------------------------------------------------------------------------

def _directorio_escribible(ruta: Path) -> bool:
    """Comprueba de forma real (creando un archivo temporal) si se puede escribir."""
    try:
        ruta.mkdir(parents=True, exist_ok=True)
        prueba = ruta / f".prueba_escritura_{os.getpid()}_{threading.get_ident()}.tmp"
        prueba.write_text("ok", encoding="utf-8")
        prueba.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def _elegir_directorio_datos() -> Path:
    """
    Prefiere la carpeta del programa. Si está protegida (por ejemplo,
    Archivos de programa), usa una carpeta de datos del usuario.
    """
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        base = Path(__file__).resolve().parent

    appdata = os.environ.get("APPDATA")
    if appdata:
        respaldo_usuario = Path(appdata) / "DescargadorYouTube"
    else:
        respaldo_usuario = Path.home() / ".descargador_youtube"

    candidatos = [
        base,
        respaldo_usuario,
        Path(tempfile.gettempdir()) / "DescargadorYouTube",
    ]
    for candidato in candidatos:
        if _directorio_escribible(candidato):
            return candidato
    # En el peor caso, se intenta igualmente con la carpeta del programa.
    return base


DIRECTORIO_DATOS = _elegir_directorio_datos()
RUTA_CONFIG = DIRECTORIO_DATOS / "config.json"
RUTA_LOG = DIRECTORIO_DATOS / "log.txt"


# ---------------------------------------------------------------------------
# Registro de errores
# ---------------------------------------------------------------------------

def configurar_log(ruta: Path) -> logging.Logger:
    """Configura un log rotativo para que no crezca indefinidamente."""
    logger = logging.getLogger("descargador_youtube")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    if logger.handlers:
        return logger

    formato = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(threadName)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    try:
        manejador = logging.handlers.RotatingFileHandler(
            ruta,
            maxBytes=2_000_000,
            backupCount=2,
            encoding="utf-8",
        )
        manejador.setFormatter(formato)
        logger.addHandler(manejador)
    except OSError:
        # La interfaz seguirá funcionando aunque no se pueda escribir el log.
        manejador_consola = logging.StreamHandler()
        manejador_consola.setFormatter(formato)
        logger.addHandler(manejador_consola)

    return logger


LOG = configurar_log(RUTA_LOG)


# ---------------------------------------------------------------------------
# Utilidades de configuración, versiones y FFmpeg
# ---------------------------------------------------------------------------

def carpeta_descargas_por_defecto() -> Path:
    """Devuelve una carpeta de Descargas razonable según el sistema."""
    candidatos = [
        Path.home() / "Downloads",
        Path.home() / "Descargas",
        Path.home() / "Descàrregues",
        Path.home(),
    ]
    for candidato in candidatos:
        if candidato.exists():
            return candidato / "YouTube"
    return Path.home() / "YouTube"


def configuracion_por_defecto() -> dict[str, str]:
    return {
        "carpeta_destino": str(carpeta_descargas_por_defecto()),
        "calidad": CALIDAD_POR_DEFECTO,
    }


def cargar_configuracion() -> dict[str, str]:
    """Carga config.json; si está dañado, vuelve a valores seguros."""
    config = configuracion_por_defecto()
    if not RUTA_CONFIG.exists():
        return config

    try:
        datos = json.loads(RUTA_CONFIG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        LOG.warning("No se pudo leer config.json: %s", exc)
        return config

    if not isinstance(datos, dict):
        LOG.warning("config.json no contiene un objeto válido; se usarán valores por defecto.")
        return config

    calidad = str(datos.get("calidad", config["calidad"]))
    if calidad not in ETIQUETA_POR_ID:
        LOG.warning("Calidad desconocida en config.json: %s", calidad)
        calidad = CALIDAD_POR_DEFECTO

    carpeta = str(datos.get("carpeta_destino", config["carpeta_destino"])).strip()
    if carpeta:
        ruta = Path(carpeta).expanduser()
        if not ruta.is_absolute():
            ruta = (DIRECTORIO_DATOS / ruta).resolve()
        carpeta = str(ruta)
    else:
        carpeta = config["carpeta_destino"]

    return {"carpeta_destino": carpeta, "calidad": calidad}


def guardar_configuracion(config: dict[str, str]) -> None:
    """Escribe config.json de forma atómica para evitar archivos a medias."""
    temporal = RUTA_CONFIG.with_suffix(".json.tmp")
    try:
        texto = json.dumps(config, ensure_ascii=False, indent=2)
        temporal.write_text(texto + "\n", encoding="utf-8")
        os.replace(temporal, RUTA_CONFIG)
    except OSError as exc:
        LOG.error("No se pudo guardar config.json: %s", exc)
        try:
            temporal.unlink(missing_ok=True)
        except OSError:
            pass


def version_ytdlp_instalada() -> str:
    """Lee la versión del paquete sin depender de una importación ya cargada."""
    try:
        importlib.invalidate_caches()
        return importlib.metadata.version("yt-dlp")
    except (importlib.metadata.PackageNotFoundError, ValueError):
        pass
    except Exception as exc:  # pragma: no cover - defensivo
        LOG.debug("Error leyendo la versión de yt-dlp: %s", exc)

    try:
        import yt_dlp  # noqa: PLC0415 (importación intencionalmente tardía)

        return str(getattr(getattr(yt_dlp, "version", None), "__version__", "desconocida"))
    except Exception:
        return "no instalada"


def buscar_ffmpeg() -> Path | None:
    """
    Busca FFmpeg en PATH (requisito principal) y también junto al programa.
    La segunda opción permite colocar una carpeta ffmpeg/bin portable.
    """
    encontrado = shutil.which("ffmpeg")
    if encontrado:
        return Path(encontrado)

    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        base = Path(__file__).resolve().parent

    nombre = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    candidatos = [
        base / nombre,
        base / "ffmpeg" / "bin" / nombre,
        base / "bin" / nombre,
    ]
    for candidato in candidatos:
        if candidato.is_file():
            return candidato
    return None


def instrucciones_ffmpeg() -> str:
    """Texto de ayuda adaptado al sistema operativo."""
    sistema = platform.system()
    if sistema == "Windows":
        return (
            "FFmpeg no está instalado o no está en el PATH.\n\n"
            "Opción 1 (recomendada): abre PowerShell como administrador y ejecuta:\n"
            "    winget install --id Gyan.FFmpeg -e\n\n"
            "Opción 2: descarga FFmpeg desde https://www.gyan.dev/ffmpeg/builds/\n"
            "Descomprímelo en C:\\ffmpeg, añade C:\\ffmpeg\\bin al PATH de Windows\n"
            "y cierra y vuelve a abrir esta aplicación.\n\n"
            "Comprueba la instalación en una terminal con:\n"
            "    ffmpeg -version"
        )
    if sistema == "Darwin":
        return (
            "FFmpeg no está instalado.\n\n"
            "Instálalo con Homebrew abriendo Terminal y ejecutando:\n"
            "    brew install ffmpeg\n\n"
            "Después vuelve a abrir esta aplicación y comprueba con:\n"
            "    ffmpeg -version"
        )
    return (
        "FFmpeg no está instalado.\n\n"
        "En Debian/Ubuntu/Mint:\n"
        "    sudo apt update && sudo apt install ffmpeg\n\n"
        "En Fedora:\n"
        "    sudo dnf install ffmpeg\n\n"
        "En Arch Linux:\n"
        "    sudo pacman -S ffmpeg\n\n"
        "Comprueba la instalación con:\n"
        "    ffmpeg -version"
    )


# ---------------------------------------------------------------------------
# Validación de enlaces
# ---------------------------------------------------------------------------

def _es_host_youtube(host: str) -> bool:
    host = host.lower().rstrip(".")
    return (
        host == "youtu.be"
        or host == "www.youtu.be"
        or host == "youtube.com"
        or host.endswith(".youtube.com")
        or host == "youtube-nocookie.com"
        or host.endswith(".youtube-nocookie.com")
    )


def _datos_del_enlace(texto: str) -> tuple[Any, str, list[str]]:
    """Normaliza el texto y devuelve (urlparse, host, partes de la ruta)."""
    limpio = texto.strip().strip("\"'").replace("\r", "").replace("\n", "")
    if not limpio:
        raise ValueError("Pega primero el enlace de un video o de una lista de YouTube.")

    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", limpio):
        limpio = "https://" + limpio

    parsed = urlparse(limpio)
    if parsed.scheme.lower() not in ("http", "https"):
        raise ValueError("El enlace debe comenzar por http:// o https://.")
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host or not _es_host_youtube(host):
        raise ValueError("El enlace no pertenece a YouTube.")
    if parsed.username or parsed.password:
        raise ValueError("El enlace contiene credenciales y no es seguro.")

    partes = [parte for parte in parsed.path.split("/") if parte]
    return parsed, host, partes


def normalizar_url_youtube(texto: str) -> str:
    """Valida el enlace y lanza ValueError con un mensaje comprensible."""
    parsed, host, partes = _datos_del_enlace(texto)

    query = parse_qs(parsed.query)
    video = (query.get("v") or [""])[0].strip()
    lista = (query.get("list") or [""])[0].strip()

    if not video and host.endswith("youtu.be") and partes:
        video = partes[0]

    if not video and partes and partes[0].lower() in {"shorts", "live", "embed", "v"}:
        if len(partes) > 1:
            video = partes[1]

    es_pagina_lista = bool(partes and partes[0].lower() == "playlist")

    if not video and not lista and not es_pagina_lista:
        raise ValueError(
            "El enlace no apunta a un video ni a una lista de YouTube.\n\n"
            "Ejemplos válidos:\n"
            "https://www.youtube.com/watch?v=VIDEO\n"
            "https://youtu.be/VIDEO\n"
            "https://www.youtube.com/playlist?list=LISTA"
        )

    if es_pagina_lista and not lista:
        raise ValueError(
            "El enlace de la lista no contiene el identificador 'list'. "
            "Copia la dirección completa desde el navegador."
        )

    return parsed.geturl()


def es_lista_de_reproduccion(url: str) -> bool:
    """Detecta listas sin hacer una petición de red."""
    try:
        parsed, _host, partes = _datos_del_enlace(url)
    except ValueError:
        return False
    query = parse_qs(parsed.query)
    lista = (query.get("list") or [""])[0].strip()
    return bool(lista) or bool(partes and partes[0].lower() == "playlist")


def enlace_tiene_video_individual(url: str) -> bool:
    """Indica si el enlace incluye también un video concreto."""
    try:
        parsed, host, partes = _datos_del_enlace(url)
    except ValueError:
        return False
    query = parse_qs(parsed.query)
    if (query.get("v") or [""])[0].strip():
        return True
    if host.endswith("youtu.be") and partes:
        return True
    if partes and partes[0].lower() in {"shorts", "live", "embed", "v"} and len(partes) > 1:
        return True
    return False


# ---------------------------------------------------------------------------
# Formato de datos y traducción de errores
# ---------------------------------------------------------------------------

def formatear_bytes_velocidad(valor: Any) -> str:
    if not isinstance(valor, (int, float)) or valor <= 0:
        return "Calculando..."
    unidades = ["B/s", "KB/s", "MB/s", "GB/s"]
    numero = float(valor)
    for unidad in unidades:
        if numero < 1024 or unidad == unidades[-1]:
            if unidad == "B/s":
                return f"{numero:.0f} {unidad}"
            return f"{numero:.1f} {unidad}"
        numero /= 1024
    return "Calculando..."


def formatear_tiempo(segundos: Any) -> str:
    if not isinstance(segundos, (int, float)) or segundos < 0:
        return "Calculando..."
    total = int(segundos)
    horas, resto = divmod(total, 3600)
    minutos, segundos_restantes = divmod(resto, 60)
    if horas:
        return f"{horas:02d}:{minutos:02d}:{segundos_restantes:02d}"
    return f"{minutos:02d}:{segundos_restantes:02d}"


def _normalizar_para_buscar(texto: str) -> str:
    """Quita acentos y pasa a minúsculas para comparar mensajes de error."""
    return (
        unicodedata.normalize("NFKD", texto or "")
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
    )


def _detalle_excepcion(error: BaseException) -> str:
    """Reúne el mensaje y sus causas, evitando bucles."""
    partes: list[str] = []
    vistos: set[int] = set()
    actual: BaseException | None = error
    while actual is not None and id(actual) not in vistos:
        vistos.add(id(actual))
        texto = str(actual).strip()
        if texto and texto not in partes:
            partes.append(texto)
        actual = actual.__cause__ or actual.__context__
    return " | ".join(partes) if partes else error.__class__.__name__


def traducir_error(error: BaseException) -> str:
    """Convierte errores técnicos de yt-dlp/Python en mensajes en español."""
    detalle = _detalle_excepcion(error)
    texto = _normalizar_para_buscar(detalle)

    if isinstance(error, OSError):
        errno = getattr(error, "errno", None)
        winerror = getattr(error, "winerror", None)
        if errno == 28 or winerror == 112:
            texto = "no space left on device"

    if any(p in texto for p in ("no space left", "not enough space", "disk full", "winerror 112")):
        return (
            "No hay espacio suficiente en el disco o la carpeta de destino.\n\n"
            "Libera espacio, elige otra carpeta y vuelve a intentarlo."
        )

    if any(p in texto for p in ("private video", "video is private", "es privado")):
        return (
            "Este video es privado. Solo el propietario y las personas autorizadas "
            "pueden verlo, así que no es posible descargarlo."
        )

    if any(p in texto for p in ("confirm your age", "age-restricted", "age restricted",
                                "inappropriate for some users", "may be inappropriate")):
        return (
            "YouTube pide verificar la edad para ver este video.\n\n"
            "Inicia sesión en YouTube desde el navegador y usa una versión de yt-dlp "
            "con cookies, o elige otro video. Esta aplicación no solicita tu cuenta."
        )

    if any(p in texto for p in ("confirm you're not a bot", "confirm you are not a bot",
                                "sign in to confirm you're not a bot")):
        return (
            "YouTube pidió verificar que no eres un robot.\n\n"
            "Suele ocurrir de forma temporal. Espera unos minutos, actualiza yt-dlp "
            "y vuelve a intentarlo. Si continúa, prueba con otra red o conexión."
        )

    if any(p in texto for p in ("members-only", "join this channel", "channel members",
                                "members only")):
        return (
            "Este video es exclusivo para miembros del canal.\n\n"
            "Se necesita una cuenta con membresía activa y cookies de sesión para descargarlo."
        )

    if any(p in texto for p in ("sign in to confirm", "please sign in", "login required",
                                "registered users", "use --cookies", "authentication")):
        return (
            "YouTube solicita iniciar sesión para acceder a este contenido.\n\n"
            "Puede ser un video restringido, una cuenta requerida o una verificación "
            "temporal de YouTube. Esta aplicación no pide ni guarda contraseñas."
        )

    if any(p in texto for p in ("not available in your country", "not available in your location",
                                "geo restricted", "geo-restricted", "blocked it in your country",
                                "the uploader has not made this video available")):
        return (
            "Este video no está disponible en tu país por una restricción geográfica.\n\n"
            "No se puede evitar desde esta aplicación de forma fiable."
        )

    if any(p in texto for p in ("copyright", "blocked on copyright grounds")):
        return (
            "Este video fue bloqueado por derechos de autor y YouTube no permite descargarlo."
        )

    if any(p in texto for p in ("live event will begin", "premieres in", "this live event")):
        return (
            "El video es una transmisión en directo o un estreno que todavía no está disponible.\n\n"
            "Espera a que termine y vuelve a intentarlo."
        )

    if any(p in texto for p in ("video unavailable", "this video is unavailable",
                                "this video is not available", "video is not available",
                                "video is no longer available", "has been removed",
                                "account associated with this video", "video no disponible",
                                "removed by the uploader", "http error 404")):
        return (
            "El video no está disponible. Puede haber sido eliminado, ser privado "
            "o tener una restricción del canal."
        )

    if any(p in texto for p in ("http error 403", "403 forbidden", "forbidden")):
        return (
            "YouTube rechazó la descarga (HTTP 403).\n\n"
            "Suele ocurrir cuando cambia YouTube o la versión de yt-dlp está desactualizada. "
            "El programa intentó actualizarla; si persiste, actualiza manualmente con:\n"
            "    pip install -U yt-dlp"
        )

    if any(p in texto for p in ("http error 429", "too many requests")):
        return (
            "YouTube limitó temporalmente las peticiones (HTTP 429).\n\n"
            "Espera unos minutos, evita descargar muchas listas a la vez y vuelve a intentarlo."
        )

    if any(p in texto for p in ("getaddrinfo failed", "name or service not known",
                                "temporary failure in name resolution", "network is unreachable",
                                "no route to host", "urlopen error", "connection refused",
                                "connection reset", "connection aborted", "timed out",
                                "timeout", "ssl", "certificate verify failed",
                                "proxy", "unable to download webpage")):
        return (
            "No hay conexión con YouTube o la red no responde.\n\n"
            "Comprueba tu conexión a internet, el firewall o el proxy y vuelve a intentarlo."
        )

    if any(p in texto for p in ("requested format is not available", "no video formats found",
                                "requested format not available", "no formats found")):
        return (
            "YouTube no ofrece un formato disponible para la calidad elegida.\n\n"
            "Prueba con 720p, 480p o solo audio. También puede ayudar actualizar yt-dlp."
        )

    if any(p in texto for p in ("ffmpeg not found", "ffmpeg is not installed",
                                "ffprobe and ffmpeg not found", "postprocessing: ffmpeg")):
        return (
            "FFmpeg no está instalado o no se encuentra en el PATH.\n\n"
            + instrucciones_ffmpeg()
        )

    if any(p in texto for p in ("unsupported url", "is not a valid url", "invalid url",
                                "unable to extract", "no suitable info extractor")):
        return (
            "No se pudo interpretar el enlace. Comprueba que sea una dirección válida "
            "de un video o de una lista de YouTube."
        )

    recorte = detalle
    if len(recorte) > 350:
        recorte = recorte[:347] + "..."
    return (
        "No se pudo completar la descarga.\n\n"
        f"Detalle técnico: {recorte}\n\n"
        "Consulta log.txt para obtener más información."
    )


def _parece_error_de_red(texto: str) -> bool:
    normalizado = _normalizar_para_buscar(texto)
    return any(
        p in normalizado
        for p in (
            "getaddrinfo", "name or service", "temporary failure", "connection",
            "timed out", "timeout", "network is unreachable", "no route to host",
            "urlopen error", "proxy", "ssl", "certificate",
        )
    )


# ---------------------------------------------------------------------------
# Integración con yt-dlp
# ---------------------------------------------------------------------------

class CancelacionSolicitada(Exception):
    """Se lanza desde un hook de yt-dlp para detener la descarga."""


class ErrorFinalDescarga(Exception):
    """Error ya traducido que debe mostrarse tal cual en la interfaz."""


class AdaptadorLogYtDlp:
    """Envía los mensajes internos de yt-dlp al log de la aplicación."""

    def __init__(self, logger: logging.Logger) -> None:
        self.logger = logger

    def debug(self, mensaje: str) -> None:
        if mensaje.startswith("[debug] "):
            mensaje = mensaje[8:]
        self.logger.debug("yt-dlp: %s", mensaje)

    def info(self, mensaje: str) -> None:
        self.logger.info("yt-dlp: %s", mensaje)

    def warning(self, mensaje: str) -> None:
        self.logger.warning("yt-dlp: %s", mensaje)

    def error(self, mensaje: str) -> None:
        self.logger.error("yt-dlp: %s", mensaje)

    def write(self, mensaje: str) -> None:
        # Algunas versiones de yt-dlp usan write() para mensajes informativos.
        if mensaje.strip():
            self.debug(mensaje)


# ---------------------------------------------------------------------------
# Interfaz gráfica
# ---------------------------------------------------------------------------

class DescargadorApp(ctk.CTk):
    """Ventana principal y coordinación de los hilos de trabajo."""

    def __init__(self) -> None:
        super().__init__()

        self.title(f"{NOMBRE_APP} {VERSION_APP}")
        self.geometry("840x650")
        self.minsize(760, 630)
        self.configure(fg_color=("gray92", "gray10"))

        # Estado compartido entre la interfaz y los hilos.
        self._eventos: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._cancelar = threading.Event()
        self._hilo_descarga: threading.Thread | None = None
        self._descargando = False
        self._cerrando = False
        self._after_eventos: str | None = None
        self._bloqueo_pip = threading.Lock()
        self._actualizacion_inicial_terminada = threading.Event()
        self._ultimo_aviso_progreso = 0.0
        self._ffmpeg = buscar_ffmpeg()
        self._version_ytdlp = version_ytdlp_instalada()

        config = cargar_configuracion()
        self._calidad_inicial = config["calidad"]
        self._carpeta_inicial = config["carpeta_destino"]

        self._crear_fuentes()
        self._construir_interfaz()
        self._actualizar_estado_ffmpeg()

        self.protocol("WM_DELETE_WINDOW", self._al_cerrar)
        self._after_eventos = self.after(100, self._procesar_eventos)

        if not self._ffmpeg:
            self.after(700, self._avisar_ffmpeg_al_inicio)
        self.after(250, self._iniciar_actualizacion_inicial)

        LOG.info(
            "Aplicación iniciada. Versión yt-dlp: %s | FFmpeg: %s | Datos: %s",
            self._version_ytdlp,
            self._ffmpeg or "no encontrado",
            DIRECTORIO_DATOS,
        )

        # Un error no controlado en un callback de Tk se registra y se muestra
        # de forma amable en lugar de cerrar la aplicación.
        self.report_callback_exception = self._error_callback_tk

    # ------------------------------------------------------------------
    # Construcción de la interfaz
    # ------------------------------------------------------------------

    def _crear_fuentes(self) -> None:
        self.fuente_titulo = ctk.CTkFont(size=28, weight="bold")
        self.fuente_subtitulo = ctk.CTkFont(size=13)
        self.fuente_seccion = ctk.CTkFont(size=13, weight="bold")
        self.fuente_estado = ctk.CTkFont(size=14, weight="bold")
        self.fuente_porcentaje = ctk.CTkFont(size=22, weight="bold")
        self.fuente_pequena = ctk.CTkFont(size=11)
        self.fuente_media = ctk.CTkFont(size=12)

    def _construir_interfaz(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Cabecera -----------------------------------------------------
        cabecera = ctk.CTkFrame(self, fg_color="transparent")
        cabecera.grid(row=0, column=0, sticky="ew", padx=30, pady=(24, 12))
        cabecera.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            cabecera,
            text=NOMBRE_APP,
            font=self.fuente_titulo,
            anchor="w",
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(
            cabecera,
            text="Descarga videos en hasta 1080p o extrae el audio en MP3.",
            font=self.fuente_subtitulo,
            text_color=("gray35", "gray70"),
            anchor="w",
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))

        contenido = ctk.CTkFrame(self, fg_color="transparent")
        contenido.grid(row=1, column=0, sticky="nsew", padx=30)
        contenido.grid_columnconfigure(0, weight=1)
        contenido.grid_rowconfigure(2, weight=1)

        # Panel de datos ----------------------------------------------
        panel = ctk.CTkFrame(
            contenido,
            corner_radius=16,
            border_width=1,
            border_color=("gray78", "gray28"),
        )
        panel.grid(row=0, column=0, sticky="ew")
        panel.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            panel,
            text="Enlace del video o de la lista",
            font=self.fuente_seccion,
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=20, pady=(18, 7))

        fila_enlace = ctk.CTkFrame(panel, fg_color="transparent")
        fila_enlace.grid(row=1, column=0, sticky="ew", padx=20)
        fila_enlace.grid_columnconfigure(0, weight=1)

        self.entrada_enlace = ctk.CTkEntry(
            fila_enlace,
            height=40,
            placeholder_text="https://www.youtube.com/watch?v=...",
        )
        self.entrada_enlace.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.entrada_enlace.bind("<Return>", lambda _evento: self._iniciar_descarga())

        self.boton_pegar = ctk.CTkButton(
            fila_enlace,
            text="Pegar",
            width=92,
            height=40,
            command=self._pegar_enlace,
        )
        self.boton_pegar.grid(row=0, column=1)

        # Calidad y carpeta ------------------------------------------
        ajustes = ctk.CTkFrame(panel, fg_color="transparent")
        ajustes.grid(row=2, column=0, sticky="ew", padx=20, pady=(16, 0))
        ajustes.grid_columnconfigure(0, weight=0, minsize=210)
        ajustes.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            ajustes,
            text="Calidad",
            font=self.fuente_seccion,
            anchor="w",
        ).grid(row=0, column=0, sticky="w", pady=(0, 7))

        self.calidad_var = tk.StringVar(
            value=ETIQUETA_POR_ID.get(self._calidad_inicial, "1080p (MP4)")
        )
        self.selector_calidad = ctk.CTkOptionMenu(
            ajustes,
            variable=self.calidad_var,
            values=list(CALIDADES.keys()),
            width=190,
            height=38,
            dynamic_resizing=False,
            command=self._al_cambiar_calidad,
        )
        self.selector_calidad.grid(row=1, column=0, sticky="w", padx=(0, 18))

        ctk.CTkLabel(
            ajustes,
            text="Carpeta de destino",
            font=self.fuente_seccion,
            anchor="w",
        ).grid(row=0, column=1, sticky="w", pady=(0, 7))

        fila_carpeta = ctk.CTkFrame(ajustes, fg_color="transparent")
        fila_carpeta.grid(row=1, column=1, sticky="ew")
        fila_carpeta.grid_columnconfigure(0, weight=1)

        self.carpeta_var = tk.StringVar(value=self._carpeta_inicial)
        self.entrada_carpeta = ctk.CTkEntry(
            fila_carpeta,
            height=38,
            textvariable=self.carpeta_var,
            state="readonly",
        )
        self.entrada_carpeta.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        self.boton_examinar = ctk.CTkButton(
            fila_carpeta,
            text="Examinar...",
            width=104,
            height=38,
            fg_color=("gray75", "gray28"),
            hover_color=("gray65", "gray35"),
            text_color=("gray10", "gray95"),
            command=self._elegir_carpeta,
        )
        self.boton_examinar.grid(row=0, column=1)

        # Acciones ----------------------------------------------------
        acciones = ctk.CTkFrame(panel, fg_color="transparent")
        acciones.grid(row=3, column=0, sticky="ew", padx=20, pady=(18, 20))
        acciones.grid_columnconfigure(2, weight=1)

        self.boton_descargar = ctk.CTkButton(
            acciones,
            text="Descargar",
            width=188,
            height=44,
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._iniciar_descarga,
        )
        self.boton_descargar.grid(row=0, column=0, padx=(0, 9))

        self.boton_cancelar = ctk.CTkButton(
            acciones,
            text="Cancelar",
            width=120,
            height=44,
            fg_color=("#C0392B", "#922B21"),
            hover_color=("#A93226", "#7B241C"),
            text_color="white",
            state="disabled",
            command=self._cancelar_descarga,
        )
        self.boton_cancelar.grid(row=0, column=1)

        self.boton_abrir_carpeta = ctk.CTkButton(
            acciones,
            text="Abrir carpeta",
            width=132,
            height=44,
            fg_color="transparent",
            hover_color=("gray82", "gray25"),
            border_width=1,
            border_color=("gray65", "gray40"),
            text_color=("gray20", "gray85"),
            command=self._abrir_carpeta_destino,
        )
        self.boton_abrir_carpeta.grid(row=0, column=2, sticky="e")

        # Progreso ----------------------------------------------------
        tarjeta_progreso = ctk.CTkFrame(
            contenido,
            corner_radius=16,
            border_width=1,
            border_color=("gray78", "gray28"),
        )
        tarjeta_progreso.grid(row=1, column=0, sticky="ew", pady=(16, 0))
        tarjeta_progreso.grid_columnconfigure(0, weight=1)

        self.estado_label = ctk.CTkLabel(
            tarjeta_progreso,
            text="Listo para descargar.",
            font=self.fuente_estado,
            anchor="w",
            justify="left",
            wraplength=620,
        )
        self.estado_label.grid(row=0, column=0, sticky="w", padx=(20, 8), pady=(16, 7))

        self.porcentaje_label = ctk.CTkLabel(
            tarjeta_progreso,
            text="0 %",
            font=self.fuente_porcentaje,
            anchor="e",
            width=78,
        )
        self.porcentaje_label.grid(row=0, column=1, sticky="e", padx=(8, 20), pady=(16, 7))

        self.barra_progreso = ctk.CTkProgressBar(
            tarjeta_progreso,
            height=15,
            corner_radius=7,
        )
        self.barra_progreso.grid(
            row=1,
            column=0,
            columnspan=2,
            sticky="ew",
            padx=20,
        )
        self.barra_progreso.set(0)

        metricas = ctk.CTkFrame(tarjeta_progreso, fg_color="transparent")
        metricas.grid(row=2, column=0, columnspan=2, sticky="ew", padx=20, pady=(11, 16))
        metricas.grid_columnconfigure(0, weight=0)
        metricas.grid_columnconfigure(1, weight=0)
        metricas.grid_columnconfigure(2, weight=1)

        self.velocidad_label = ctk.CTkLabel(
            metricas,
            text="Velocidad: --",
            font=self.fuente_media,
            text_color=("gray35", "gray72"),
            anchor="w",
        )
        self.velocidad_label.grid(row=0, column=0, sticky="w", padx=(0, 26))

        self.tiempo_label = ctk.CTkLabel(
            metricas,
            text="Tiempo restante: --",
            font=self.fuente_media,
            text_color=("gray35", "gray72"),
            anchor="w",
        )
        self.tiempo_label.grid(row=0, column=1, sticky="w", padx=(0, 26))

        self.elemento_label = ctk.CTkLabel(
            metricas,
            text="",
            font=self.fuente_media,
            text_color=("gray35", "gray72"),
            anchor="e",
        )
        self.elemento_label.grid(row=0, column=2, sticky="e")

        # Pie de página ----------------------------------------------
        pie = ctk.CTkFrame(self, fg_color="transparent")
        pie.grid(row=2, column=0, sticky="ew", padx=30, pady=(10, 18))
        pie.grid_columnconfigure(0, weight=1)
        pie.grid_columnconfigure(1, weight=0)

        estado_herramientas = ctk.CTkFrame(pie, fg_color="transparent")
        estado_herramientas.grid(row=0, column=0, sticky="w")

        self.ffmpeg_label = ctk.CTkLabel(
            estado_herramientas,
            text="FFmpeg: comprobando...",
            font=self.fuente_pequena,
            anchor="w",
        )
        self.ffmpeg_label.grid(row=0, column=0, sticky="w")

        self.boton_ayuda_ffmpeg = ctk.CTkButton(
            estado_herramientas,
            text="Ayuda",
            width=58,
            height=24,
            font=self.fuente_pequena,
            fg_color="transparent",
            hover_color=("gray82", "gray25"),
            border_width=1,
            border_color=("gray70", "gray35"),
            text_color=("gray25", "gray80"),
            command=self._mostrar_ayuda_ffmpeg,
        )
        self.boton_ayuda_ffmpeg.grid(row=0, column=1, padx=(8, 4))

        self.boton_recomprobar_ffmpeg = ctk.CTkButton(
            estado_herramientas,
            text="Comprobar de nuevo",
            width=119,
            height=24,
            font=self.fuente_pequena,
            fg_color="transparent",
            hover_color=("gray82", "gray25"),
            text_color=("gray35", "gray70"),
            command=self._recomprobar_ffmpeg,
        )
        self.boton_recomprobar_ffmpeg.grid(row=0, column=2)

        herramientas_derecha = ctk.CTkFrame(pie, fg_color="transparent")
        herramientas_derecha.grid(row=0, column=1, sticky="e")

        self.version_label = ctk.CTkLabel(
            herramientas_derecha,
            text=f"yt-dlp v{self._version_ytdlp}",
            font=self.fuente_pequena,
            text_color=("gray35", "gray70"),
        )
        self.version_label.grid(row=0, column=0, padx=(0, 8))

        self.boton_log = ctk.CTkButton(
            herramientas_derecha,
            text="Abrir log",
            width=72,
            height=24,
            font=self.fuente_pequena,
            fg_color="transparent",
            hover_color=("gray82", "gray25"),
            border_width=1,
            border_color=("gray70", "gray35"),
            text_color=("gray25", "gray80"),
            command=self._abrir_log,
        )
        self.boton_log.grid(row=0, column=1)

        self.actualizacion_label = ctk.CTkLabel(
            pie,
            text="Comprobando actualizaciones de yt-dlp...",
            font=self.fuente_pequena,
            text_color=("gray45", "gray60"),
            anchor="w",
            justify="left",
            wraplength=770,
        )
        self.actualizacion_label.grid(
            row=1,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(5, 0),
        )

    # ------------------------------------------------------------------
    # Configuración y estado visual
    # ------------------------------------------------------------------

    def _guardar_preferencias(self) -> None:
        calidad = ETIQUETA_POR_ID.get(self.calidad_var.get(), CALIDAD_POR_DEFECTO)
        guardar_configuracion(
            {
                "carpeta_destino": self.carpeta_var.get(),
                "calidad": calidad,
            }
        )

    def _al_cambiar_calidad(self, _valor: str | None = None) -> None:
        self._guardar_preferencias()

    def _elegir_carpeta(self) -> None:
        inicial = self.carpeta_var.get()
        if not Path(inicial).is_dir():
            inicial = str(Path.home())
        seleccionada = filedialog.askdirectory(
            title="Elige la carpeta de destino",
            initialdir=inicial,
            mustexist=False,
        )
        if seleccionada:
            self.carpeta_var.set(str(Path(seleccionada)))
            self._guardar_preferencias()

    def _actualizar_estado_ffmpeg(self, mostrar_resultado: bool = False) -> None:
        self._ffmpeg = buscar_ffmpeg()
        if self._ffmpeg:
            self.ffmpeg_label.configure(
                text="FFmpeg: instalado",
                text_color=("#1E8449", "#58D68D"),
            )
            if mostrar_resultado:
                messagebox.showinfo(
                    "FFmpeg encontrado",
                    f"FFmpeg está disponible en:\n{self._ffmpeg}",
                )
        else:
            self.ffmpeg_label.configure(
                text="FFmpeg: NO encontrado",
                text_color=("#C0392B", "#EC7063"),
            )
            if mostrar_resultado:
                messagebox.showwarning(
                    "FFmpeg no encontrado",
                    "Todavía no se encontró FFmpeg.\n\n" + instrucciones_ffmpeg(),
                )

        if not self._descargando:
            self.boton_descargar.configure(state="normal" if self._ffmpeg else "disabled")

    def _recomprobar_ffmpeg(self) -> None:
        self._actualizar_estado_ffmpeg(mostrar_resultado=True)

    def _mostrar_ayuda_ffmpeg(self) -> None:
        messagebox.showinfo("Instalar FFmpeg", instrucciones_ffmpeg())

    def _avisar_ffmpeg_al_inicio(self) -> None:
        if self._cerrando or self._ffmpeg:
            return
        messagebox.showwarning(
            "FFmpeg no encontrado",
            "Sin FFmpeg no se pueden unir el video y el audio ni crear archivos MP3.\n\n"
            + instrucciones_ffmpeg()
            + "\n\nCuando termines, pulsa 'Comprobar de nuevo' en la parte inferior.",
        )

    def _al_cambiar_estado_ocupado(self, ocupado: bool) -> None:
        self._descargando = ocupado
        estado = "disabled" if ocupado else "normal"
        self.entrada_enlace.configure(state=estado)
        self.boton_pegar.configure(state=estado)
        self.selector_calidad.configure(state=estado)
        self.boton_examinar.configure(state=estado)
        self.boton_cancelar.configure(state="normal" if ocupado else "disabled")

        puede_descargar = (not ocupado) and self._ffmpeg is not None
        self.boton_descargar.configure(state="normal" if puede_descargar else "disabled")

    def _reiniciar_progreso(self) -> None:
        self.barra_progreso.set(0)
        self.porcentaje_label.configure(text="0 %")
        self.velocidad_label.configure(text="Velocidad: --")
        self.tiempo_label.configure(text="Tiempo restante: --")
        self.elemento_label.configure(text="")

    def _aplicar_progreso(self, datos: dict[str, Any]) -> None:
        if "estado" in datos:
            self.estado_label.configure(text=str(datos["estado"]))
        if "elemento" in datos:
            self.elemento_label.configure(text=str(datos["elemento"]))
        if "porcentaje" in datos and datos["porcentaje"] is not None:
            porcentaje = max(0.0, min(1.0, float(datos["porcentaje"])))
            self.barra_progreso.set(porcentaje)
            self.porcentaje_label.configure(text=f"{porcentaje * 100:.0f} %")
        if "velocidad" in datos:
            self.velocidad_label.configure(text=f"Velocidad: {datos['velocidad']}")
        if "eta" in datos:
            self.tiempo_label.configure(text=f"Tiempo restante: {datos['eta']}")

    def _actualizar_version_ui(self, version: str) -> None:
        self._version_ytdlp = version
        self.version_label.configure(text=f"yt-dlp v{version}")

    # ------------------------------------------------------------------
    # Comunicación segura entre hilos y eventos
    # ------------------------------------------------------------------

    def _emitir(self, tipo: str, datos: Any = None) -> None:
        self._eventos.put((tipo, datos))

    def _procesar_eventos(self) -> None:
        if self._cerrando:
            return

        try:
            while True:
                tipo, datos = self._eventos.get_nowait()
                try:
                    if tipo == "progreso":
                        self._aplicar_progreso(datos)
                    elif tipo == "estado":
                        self.estado_label.configure(text=str(datos))
                    elif tipo == "reiniciar_progreso":
                        self._reiniciar_progreso()
                    elif tipo == "version":
                        self._actualizar_version_ui(str(datos))
                    elif tipo == "actualizacion":
                        self.actualizacion_label.configure(text=str(datos))
                    elif tipo == "finalizado":
                        self._manejar_finalizacion(datos)
                except Exception:
                    LOG.exception("Error procesando evento de interfaz: %s", tipo)
        except queue.Empty:
            pass
        finally:
            if not self._cerrando:
                self._after_eventos = self.after(100, self._procesar_eventos)

    # ------------------------------------------------------------------
    # Actualización de yt-dlp
    # ------------------------------------------------------------------

    def _iniciar_actualizacion_inicial(self) -> None:
        if self._cerrando:
            self._actualizacion_inicial_terminada.set()
            return

        if getattr(sys, "frozen", False):
            self.actualizacion_label.configure(
                text=(
                    "Modo .exe: yt-dlp está incluido y no puede actualizarse solo. "
                    "Usa ejecutar.bat para mantenerlo actualizado."
                )
            )
            self._actualizacion_inicial_terminada.set()
            return

        hilo = threading.Thread(
            target=self._tarea_actualizacion_inicial,
            name="actualizar-ytdlp",
            daemon=True,
        )
        hilo.start()

    def _tarea_actualizacion_inicial(self) -> None:
        try:
            self._actualizar_ytdlp(origen="inicio")
        except Exception:
            LOG.exception("Error inesperado durante la actualización inicial")
            self._emitir(
                "actualizacion",
                "No se pudo comprobar la actualización. Se usará la versión instalada.",
            )
        finally:
            self._actualizacion_inicial_terminada.set()

    def _actualizar_ytdlp(self, origen: str = "manual") -> tuple[bool, str]:
        """
        Ejecuta 'pip install -U yt-dlp' sin bloquear la interfaz.
        Devuelve (éxito, mensaje). Si falla, la aplicación sigue funcionando.
        """
        if getattr(sys, "frozen", False):
            mensaje = "En modo .exe no se puede actualizar yt-dlp automáticamente."
            LOG.info(mensaje)
            return False, mensaje

        with self._bloqueo_pip:
            antes = version_ytdlp_instalada()
            if origen == "inicio":
                self._emitir("actualizacion", "Actualizando yt-dlp en segundo plano...")
            else:
                self._emitir("actualizacion", "Actualizando yt-dlp antes de reintentar...")

            comando = [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--upgrade",
                "yt-dlp",
                "--disable-pip-version-check",
                "--timeout",
                "15",
                "--retries",
                "1",
            ]
            entorno = os.environ.copy()
            entorno["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
            entorno["PYTHONIOENCODING"] = "utf-8"

            try:
                proceso = subprocess.run(
                    comando,
                    capture_output=True,
                    stdin=subprocess.DEVNULL,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=120,
                    env=entorno,
                    creationflags=FLAGS_SIN_VENTANA,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                mensaje = (
                    "La actualización de yt-dlp tardó demasiado (posible falta de internet). "
                    f"Se seguirá usando la versión {antes}."
                )
                LOG.warning(mensaje)
                self._emitir("actualizacion", mensaje)
                return False, mensaje
            except (OSError, ValueError) as exc:
                mensaje = (
                    "No se pudo ejecutar pip para actualizar yt-dlp. "
                    f"Se seguirá usando la versión {antes}."
                )
                LOG.error("%s Detalle: %s", mensaje, exc)
                self._emitir("actualizacion", mensaje)
                return False, mensaje

            salida = ((proceso.stdout or "") + "\n" + (proceso.stderr or "")).strip()
            if salida:
                LOG.debug("Salida de pip (últimos 4000 caracteres):\n%s", salida[-4000:])

            if proceso.returncode == 0:
                version = version_ytdlp_instalada()
                self._emitir("version", version)
                if version != antes:
                    mensaje = f"yt-dlp actualizado a v{version}."
                else:
                    mensaje = f"yt-dlp ya está actualizado (v{version})."
                LOG.info(mensaje)
                self._emitir("actualizacion", mensaje)
                return True, mensaje

            if _parece_error_de_red(salida):
                mensaje = (
                    "Sin conexión o pip no pudo contactar con el servidor. "
                    f"Se seguirá usando la versión {antes}."
                )
            else:
                mensaje = (
                    "No se pudo actualizar yt-dlp. "
                    f"Se seguirá usando la versión {antes}. Consulta log.txt."
                )
            LOG.warning("%s Código de pip: %s", mensaje, proceso.returncode)
            self._emitir("actualizacion", mensaje)
            return False, mensaje

    # ------------------------------------------------------------------
    # Acciones de la ventana
    # ------------------------------------------------------------------

    def _pegar_enlace(self) -> None:
        try:
            texto = self.clipboard_get()
        except tk.TclError:
            messagebox.showwarning(
                "Portapapeles vacío",
                "No hay ningún texto en el portapapeles. Copia el enlace de YouTube primero.",
            )
            return
        if not texto or not texto.strip():
            messagebox.showwarning("Portapapeles vacío", "El portapapeles no contiene un enlace.")
            return
        self.entrada_enlace.delete(0, "end")
        self.entrada_enlace.insert(0, texto.strip())
        self.entrada_enlace.focus_set()

    def _obtener_calidad_id(self) -> str:
        etiqueta = self.calidad_var.get()
        return CALIDADES.get(etiqueta, CALIDADES["1080p (MP4)"])["id"]

    def _preguntar_tipo_lista(self, url: str) -> bool | None:
        """
        Devuelve True para descargar la lista completa, False para un video
        individual y None si el usuario cancela.
        """
        if not es_lista_de_reproduccion(url):
            return False

        if enlace_tiene_video_individual(url):
            respuesta = messagebox.askyesnocancel(
                "Lista de reproducción detectada",
                "Este enlace también forma parte de una lista.\n\n"
                "Sí: descargar TODA la lista.\n"
                "No: descargar solo este video.\n"
                "Cancelar: volver sin descargar.",
                icon="question",
            )
            if respuesta is None:
                return None
            return bool(respuesta)

        respuesta = messagebox.askyesno(
            "Lista de reproducción detectada",
            "El enlace apunta a una lista de reproducción completa.\n\n"
            "¿Quieres descargar todos sus videos?\n\n"
            "La descarga puede tardar bastante si la lista es grande.",
            icon="question",
        )
        return True if respuesta else None

    def _obtener_y_preparar_carpeta(self) -> Path:
        texto = self.carpeta_var.get().strip()
        if not texto:
            raise ValueError("Elige una carpeta de destino antes de descargar.")

        ruta = Path(texto).expanduser()
        if not ruta.is_absolute():
            ruta = (Path.home() / ruta).resolve()

        try:
            ruta.mkdir(parents=True, exist_ok=True)
            if not ruta.is_dir():
                raise OSError("La ruta de destino no es una carpeta.")
            # Comprobación real de escritura sin dejar archivos basura.
            prueba = ruta / f".prueba_descarga_{os.getpid()}.tmp"
            prueba.write_text("ok", encoding="utf-8")
            prueba.unlink(missing_ok=True)
        except OSError as exc:
            LOG.exception("No se puede escribir en la carpeta de destino: %s", ruta)
            if getattr(exc, "errno", None) == 28 or getattr(exc, "winerror", None) == 112:
                raise ValueError(
                    "No hay espacio suficiente en el disco o la carpeta no admite escritura."
                ) from exc
            raise ValueError(
                f"No se puede escribir en la carpeta de destino:\n{ruta}\n\n"
                "Elige otra carpeta o revisa los permisos."
            ) from exc

        return ruta

    def _iniciar_descarga(self) -> None:
        if self._descargando:
            return
        if not self._ffmpeg:
            messagebox.showerror(
                "FFmpeg no encontrado",
                "Para descargar y unir video con audio hace falta FFmpeg.\n\n"
                + instrucciones_ffmpeg(),
            )
            return

        url_texto = self.entrada_enlace.get().strip()
        try:
            url = normalizar_url_youtube(url_texto)
        except ValueError as exc:
            messagebox.showerror("Enlace no válido", str(exc))
            self.entrada_enlace.focus_set()
            return

        modo_lista = self._preguntar_tipo_lista(url)
        if modo_lista is None:
            return

        try:
            carpeta = self._obtener_y_preparar_carpeta()
        except ValueError as exc:
            messagebox.showerror("Carpeta no válida", str(exc))
            return

        calidad = self._obtener_calidad_id()
        self.carpeta_var.set(str(carpeta))
        self._guardar_preferencias()

        self._cancelar.clear()
        self._reiniciar_progreso()
        self.estado_label.configure(text="Preparando la descarga...")
        self._al_cambiar_estado_ocupado(True)

        self._hilo_descarga = threading.Thread(
            target=self._trabajo_descarga,
            args=(url, calidad, carpeta, modo_lista),
            name="descarga-youtube",
            daemon=True,
        )
        self._hilo_descarga.start()

    def _cancelar_descarga(self) -> None:
        if not self._descargando:
            return
        self._cancelar.set()
        self.estado_label.configure(text="Cancelando... espera a que se detenga la descarga.")
        self.boton_cancelar.configure(state="disabled")

    def _abrir_carpeta_destino(self) -> None:
        ruta = Path(self.carpeta_var.get()).expanduser()
        if not ruta.is_dir():
            messagebox.showwarning(
                "Carpeta no encontrada",
                f"La carpeta todavía no existe:\n{ruta}\n\nSe creará al descargar.",
            )
            return
        self._abrir_ruta(ruta)

    def _abrir_log(self) -> None:
        if not RUTA_LOG.exists():
            messagebox.showinfo(
                "Registro no encontrado",
                f"Todavía no se ha creado el archivo de registro:\n{RUTA_LOG}",
            )
            return
        self._abrir_ruta(RUTA_LOG)

    def _abrir_ruta(self, ruta: Path) -> None:
        try:
            if os.name == "nt":
                os.startfile(str(ruta))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(ruta)])
            else:
                subprocess.Popen(["xdg-open", str(ruta)])
        except Exception as exc:
            LOG.exception("No se pudo abrir la ruta %s", ruta)
            messagebox.showerror("No se pudo abrir", f"No se pudo abrir:\n{ruta}\n\n{exc}")

    def _error_callback_tk(self, tipo: type[BaseException], valor: BaseException, tb: Any) -> None:
        LOG.error(
            "Error no controlado en la interfaz",
            exc_info=(tipo, valor, tb),
        )
        if not self._cerrando:
            try:
                messagebox.showerror(
                    "Error inesperado",
                    "Ocurrió un error inesperado en la interfaz.\n\n"
                    "La aplicación seguirá abierta. Revisa log.txt para más detalles.",
                )
            except tk.TclError:
                pass

    def _al_cerrar(self) -> None:
        if self._descargando:
            confirmar = messagebox.askyesno(
                "Salir durante la descarga",
                "Hay una descarga en curso. Si sales, se cancelará.\n\n¿Quieres salir igualmente?",
            )
            if not confirmar:
                return
            self._cancelar.set()

        self._guardar_preferencias()
        self._cerrando = True
        if self._after_eventos is not None:
            try:
                self.after_cancel(self._after_eventos)
            except tk.TclError:
                pass
        LOG.info("Aplicación cerrada.")
        self.destroy()

    # ------------------------------------------------------------------
    # Hilo de descarga
    # ------------------------------------------------------------------

    def _esperar_actualizacion_inicial(self) -> None:
        """Evita importar yt-dlp mientras pip está reemplazando sus archivos."""
        if self._actualizacion_inicial_terminada.is_set():
            return

        self._emitir(
            "estado",
            "Esperando a que termine la comprobación de yt-dlp...",
        )
        while not self._actualizacion_inicial_terminada.wait(0.2):
            if self._cancelar.is_set():
                raise CancelacionSolicitada()

    def _importar_yt_dlp_fresco(self) -> Any:
        """
        Importa yt-dlp de nuevo para usar una actualización recién instalada
        sin tener que cerrar y volver a abrir el programa.
        """
        importlib.invalidate_caches()
        for nombre in list(sys.modules):
            if nombre == "yt_dlp" or nombre.startswith("yt_dlp."):
                del sys.modules[nombre]

        try:
            import yt_dlp  # noqa: PLC0415

            version = str(getattr(getattr(yt_dlp, "version", None), "__version__", "?"))
            LOG.info("yt-dlp cargado para la descarga. Versión activa: %s", version)
            return yt_dlp
        except ImportError as exc:
            raise ErrorFinalDescarga(
                "yt-dlp no está instalado.\n\n"
                "Instala las dependencias con:\n"
                "    pip install -r requirements.txt"
            ) from exc

    def _trabajo_descarga(
        self,
        url: str,
        calidad: str,
        carpeta: Path,
        modo_lista: bool,
    ) -> None:
        """Flujo completo: descarga, actualización y reintento si falla."""
        try:
            self._esperar_actualizacion_inicial()
            if self._cancelar.is_set():
                raise CancelacionSolicitada()

            self._emitir("estado", "Iniciando descarga...")
            try:
                self._descargar_una_vez(url, calidad, carpeta, modo_lista)
            except CancelacionSolicitada:
                raise
            except Exception:
                LOG.exception("Primer intento de descarga fallido: %s", url)
                if self._cancelar.is_set():
                    raise CancelacionSolicitada()

                self._emitir(
                    "estado",
                    "La descarga falló. Actualizando yt-dlp para reintentar...",
                )
                actualizado, nota = self._actualizar_ytdlp(origen="reintento")

                if self._cancelar.is_set():
                    raise CancelacionSolicitada()

                # Se reintenta siempre una vez, aunque la actualización no haya
                # podido completarse (por ejemplo, por falta de internet).
                self._emitir("reiniciar_progreso")
                if actualizado:
                    self._emitir(
                        "estado",
                        "Reintentando con la versión actualizada de yt-dlp...",
                    )
                else:
                    self._emitir(
                        "estado",
                        "No se pudo actualizar yt-dlp. Reintentando una vez...",
                    )
                try:
                    self._descargar_una_vez(url, calidad, carpeta, modo_lista)
                except CancelacionSolicitada:
                    raise
                except Exception as segundo_error:
                    LOG.exception("Segundo intento de descarga fallido: %s", url)
                    mensaje = traducir_error(segundo_error)
                    if actualizado:
                        mensaje += (
                            "\n\nSe actualizó yt-dlp y se reintentó una vez, "
                            "pero el error continúa. Revisa log.txt."
                        )
                    else:
                        mensaje += (
                            "\n\nNo se pudo actualizar yt-dlp ("
                            + nota
                            + "). Se reintentó una vez, pero el error continúa."
                        )
                    raise ErrorFinalDescarga(mensaje) from segundo_error

            if self._cancelar.is_set():
                raise CancelacionSolicitada()

            self._emitir(
                "progreso",
                {
                    "porcentaje": 1.0,
                    "velocidad": "Completado",
                    "eta": "--",
                    "estado": "Descarga completada correctamente.",
                    "elemento": "",
                },
            )
            self._emitir(
                "finalizado",
                {
                    "estado": "ok",
                    "mensaje": "La descarga terminó correctamente.",
                    "carpeta": str(carpeta),
                },
            )

        except CancelacionSolicitada:
            LOG.info("Descarga cancelada por el usuario: %s", url)
            self._emitir(
                "finalizado",
                {
                    "estado": "cancelado",
                    "mensaje": "La descarga fue cancelada.",
                    "carpeta": str(carpeta),
                },
            )
        except ErrorFinalDescarga as exc:
            LOG.error("Descarga finalizada con error: %s", exc)
            self._emitir(
                "finalizado",
                {
                    "estado": "error",
                    "mensaje": str(exc),
                    "carpeta": str(carpeta),
                },
            )
        except Exception as exc:
            LOG.exception("Error inesperado en el hilo de descarga: %s", url)
            self._emitir(
                "finalizado",
                {
                    "estado": "error",
                    "mensaje": traducir_error(exc),
                    "carpeta": str(carpeta),
                },
            )

    def _descargar_una_vez(
        self,
        url: str,
        calidad: str,
        carpeta: Path,
        modo_lista: bool,
    ) -> None:
        """Ejecuta una descarga con yt-dlp y sus hooks de progreso."""
        if not self._ffmpeg:
            raise ErrorFinalDescarga(
                "FFmpeg no está disponible. Instálalo y pulsa 'Comprobar de nuevo'.\n\n"
                + instrucciones_ffmpeg()
            )

        yt_dlp = self._importar_yt_dlp_fresco()
        opciones = self._crear_opciones_ytdlp(calidad, carpeta, modo_lista)

        self._emitir("estado", "Descargando...")
        with yt_dlp.YoutubeDL(opciones) as ydl:
            resultado = ydl.extract_info(url, download=True)

        if self._cancelar.is_set():
            raise CancelacionSolicitada()
        if resultado is None:
            raise RuntimeError(
                "yt-dlp no devolvió información del video. "
                "Comprueba el enlace y consulta log.txt."
            )

    def _crear_opciones_ytdlp(
        self,
        calidad: str,
        carpeta: Path,
        modo_lista: bool,
    ) -> dict[str, Any]:
        """Construye las opciones de yt-dlp, todas documentadas en el README."""
        datos_calidad = next(
            (datos for datos in CALIDADES.values() if datos["id"] == calidad),
            CALIDADES["1080p (MP4)"],
        )
        formato = datos_calidad["formato"]
        es_audio = datos_calidad["id"] == "audio"

        opciones: dict[str, Any] = {
            "format": formato,
            # El nombre final es "Título del video.mp4" (o .mp3 en modo audio).
            "outtmpl": str(carpeta / PLANTILLA_SALIDA),
            "merge_output_format": "mp4",
            # Sustituye caracteres problemáticos de Windows (\\ / : * ? \" < > |)
            # y recorta nombres demasiado largos.
            "windowsfilenames": True,
            "trim_file_name": 180,
            "restrictfilenames": False,
            "noplaylist": not modo_lista,
            "ignoreerrors": False,
            "quiet": True,
            "no_warnings": False,
            "noprogress": True,
            "no_color": True,
            "logger": AdaptadorLogYtDlp(LOG),
            "progress_hooks": [self._hook_progreso],
            "postprocessor_hooks": [self._hook_postprocesador],
            "retries": 5,
            "fragment_retries": 5,
            "extractor_retries": 3,
            "socket_timeout": 30,
            "concurrent_fragment_downloads": 4,
            "continuedl": True,
            "overwrites": False,
            "prefer_ffmpeg": True,
        }

        if self._ffmpeg:
            opciones["ffmpeg_location"] = str(self._ffmpeg.parent)

        if es_audio:
            opciones["postprocessors"] = [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ]

        return opciones

    # ------------------------------------------------------------------
    # Hooks de yt-dlp (se ejecutan en el hilo de descarga)
    # ------------------------------------------------------------------

    @staticmethod
    def _titulo_corto(info: dict[str, Any]) -> str:
        titulo = str(info.get("title") or "video").replace("\r", " ").replace("\n", " ")
        titulo = re.sub(r"\s+", " ", titulo).strip()
        if len(titulo) > 95:
            titulo = titulo[:92] + "..."
        return titulo

    @staticmethod
    def _texto_elemento(info: dict[str, Any]) -> str:
        indice = info.get("playlist_index")
        total = info.get("playlist_count")
        if isinstance(indice, int) and indice > 0:
            if isinstance(total, int) and total > 0:
                return f"Elemento {indice} de {total}"
            return f"Elemento {indice}"
        return ""

    def _hook_progreso(self, datos: dict[str, Any]) -> None:
        """Recibe el progreso real de yt-dlp y lo envía a la interfaz."""
        if self._cancelar.is_set():
            raise CancelacionSolicitada()

        estado = datos.get("status")
        info = datos.get("info_dict") or {}
        titulo = self._titulo_corto(info)
        elemento = self._texto_elemento(info)

        if estado == "downloading":
            # Limita la frecuencia de eventos para no saturar la cola en
            # descargas con muchos fragmentos.
            ahora = time.monotonic()
            if ahora - self._ultimo_aviso_progreso < 0.12:
                return
            self._ultimo_aviso_progreso = ahora

            total = datos.get("total_bytes") or datos.get("total_bytes_estimate")
            descargado = datos.get("downloaded_bytes") or 0
            porcentaje: float | None = None
            if isinstance(total, (int, float)) and total > 0:
                porcentaje = float(descargado) / float(total)
            else:
                fragmentos = datos.get("fragment_count")
                fragmento = datos.get("fragment_index")
                if isinstance(fragmentos, int) and fragmentos > 0 and isinstance(fragmento, int):
                    porcentaje = min(1.0, max(0.0, float(fragmento) / float(fragmentos)))

            self._emitir(
                "progreso",
                {
                    "estado": f"Descargando: {titulo}",
                    "porcentaje": porcentaje,
                    "velocidad": formatear_bytes_velocidad(datos.get("speed")),
                    "eta": formatear_tiempo(datos.get("eta")),
                    "elemento": elemento,
                },
            )
            return

        if estado == "finished":
            self._ultimo_aviso_progreso = 0.0
            self._emitir(
                "progreso",
                {
                    "estado": f"Procesando: {titulo}",
                    "porcentaje": 1.0,
                    "velocidad": "Archivo recibido",
                    "eta": "Procesando...",
                    "elemento": elemento,
                },
            )
            return

        if estado == "error":
            self._emitir(
                "estado",
                f"yt-dlp informó de un error al procesar: {titulo}",
            )

    def _hook_postprocesador(self, datos: dict[str, Any]) -> None:
        """Muestra si FFmpeg está uniendo pistas o convirtiendo a MP3."""
        if self._cancelar.is_set():
            raise CancelacionSolicitada()
        if datos.get("status") != "started":
            return

        nombre = str(datos.get("postprocessor") or "")
        nombres = {
            "FFmpegMerger": "Uniendo video y audio con FFmpeg...",
            "FFmpegExtractAudio": "Convirtiendo el audio a MP3 con FFmpeg...",
            "FFmpegVideoRemuxer": "Preparando el video en MP4 con FFmpeg...",
            "FFmpegMetadata": "Añadiendo metadatos al archivo...",
            "MoveFiles": "Moviendo el archivo a la carpeta de destino...",
        }
        texto = nombres.get(nombre, "Procesando el archivo con FFmpeg...")
        self._emitir("estado", texto)
        self._emitir(
            "progreso",
            {
                "velocidad": "Procesando...",
                "eta": "--",
                "porcentaje": 1.0,
            },
        )

    # ------------------------------------------------------------------
    # Finalización
    # ------------------------------------------------------------------

    def _manejar_finalizacion(self, datos: dict[str, Any]) -> None:
        estado = str(datos.get("estado", "error"))
        mensaje = str(datos.get("mensaje", ""))
        carpeta = str(datos.get("carpeta", self.carpeta_var.get()))

        self._hilo_descarga = None
        self._al_cambiar_estado_ocupado(False)

        if estado == "ok":
            self.barra_progreso.set(1.0)
            self.porcentaje_label.configure(text="100 %")
            self.estado_label.configure(text="Descarga completada correctamente.")
            self.velocidad_label.configure(text="Velocidad: --")
            self.tiempo_label.configure(text="Tiempo restante: --")
            self.elemento_label.configure(text="")
            messagebox.showinfo(
                "Descarga completada",
                f"El archivo se guardó en:\n{carpeta}\n\n"
                "Puedes abrir la carpeta con el botón 'Abrir carpeta'.",
            )
            return

        if estado == "cancelado":
            self.estado_label.configure(text="Descarga cancelada.")
            self.velocidad_label.configure(text="Velocidad: --")
            self.tiempo_label.configure(text="Tiempo restante: --")
            return

        self.estado_label.configure(text="No se pudo completar la descarga.")
        self.velocidad_label.configure(text="Velocidad: --")
        self.tiempo_label.configure(text="Tiempo restante: --")
        messagebox.showerror(
            "No se pudo descargar",
            mensaje
            + f"\n\nLos detalles técnicos están en:\n{RUTA_LOG}\n\n"
            "Puedes usar el botón 'Abrir log' para revisarlos.",
        )


# ---------------------------------------------------------------------------
# Punto de entrada
# ---------------------------------------------------------------------------

def main() -> None:
    ctk.set_appearance_mode("System")
    ctk.set_default_color_theme("blue")

    def registrar_excepcion_global(
        tipo: type[BaseException],
        valor: BaseException,
        tb: Any,
    ) -> None:
        LOG.error("Excepción no controlada", exc_info=(tipo, valor, tb))

    sys.excepthook = registrar_excepcion_global

    aplicacion = DescargadorApp()
    aplicacion.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        LOG.exception("La aplicación no pudo iniciarse")
        raise
