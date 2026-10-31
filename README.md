# Descargador de YouTube

Aplicación de escritorio para Windows, macOS y Linux que descarga videos de
YouTube en MP4 (hasta 1080p) o extrae el audio en MP3. Solo hay que pegar el
enlace.

Incluye:

- Selector de calidad: 1080p, 720p, 480p o solo audio MP3.
- Descarga de videos individuales y listas de reproducción.
- Barra de progreso con porcentaje, velocidad y tiempo restante.
- Descarga en segundo plano, sin congelar la ventana.
- Botón **Cancelar**.
- Actualización automática de `yt-dlp` al iniciar.
- Reintento automático: si una descarga falla, intenta actualizar `yt-dlp` y
  vuelve a probar una vez.
- Mensajes de error en español y registro de incidencias en `log.txt`.
- La última carpeta y la calidad elegida se guardan en `config.json`.

> Usa esta herramienta únicamente con contenido que tengas derecho a descargar
> y respeta las condiciones de servicio de YouTube y los derechos de autor.

---

## 1. Requisitos

| Programa | Para qué se necesita |
|---|---|
| Python 3.10 o superior | Ejecutar la aplicación |
| FFmpeg | Unir video y audio y convertir a MP3 |
| Internet | Descargar videos y actualizar yt-dlp |

La interfaz usa únicamente `customtkinter`. `yt-dlp` es el motor de descarga.

---

## 2. Instalar Python (Windows)

1. Entra en <https://www.python.org/downloads/>.
2. Descarga Python 3.10 o una versión más reciente para Windows.
3. Ejecuta el instalador y **marca la casilla “Add Python to PATH”** antes de
   pulsar “Install Now”.
4. Comprueba la instalación: abre **PowerShell** o **Símbolo del sistema** y
   escribe:

   ```powershell
   python --version
   ```

   Debe aparecer algo como `Python 3.13.x`. Si aparece un error, vuelve a
   ejecutar el instalador y activa “Add Python to PATH”.

En macOS y Linux, Python suele estar disponible. En Ubuntu/Debian puede que
necesites instalar también Tk:

```bash
sudo apt install python3 python3-venv python3-tk
```

---

## 3. Instalar FFmpeg

FFmpeg es obligatorio: sin él no se pueden unir la pista de video y la de audio,
ni crear el MP3. La aplicación comprueba FFmpeg al arrancar y muestra las
instrucciones si no lo encuentra.

### Windows: opción recomendada (winget)

1. Abre **PowerShell como administrador**.
2. Ejecuta:

   ```powershell
   winget install --id Gyan.FFmpeg -e
   ```

3. Cierra y vuelve a abrir la terminal y la aplicación.
4. Comprueba:

   ```powershell
   ffmpeg -version
   ```

### Windows: instalación manual

1. Descarga la versión “release essentials” desde
   <https://www.gyan.dev/ffmpeg/builds/>.
2. Descomprime el archivo, por ejemplo en `C:\ffmpeg`.
3. Debe existir `C:\ffmpeg\bin\ffmpeg.exe`.
4. Añade `C:\ffmpeg\bin` a la variable `Path`:
   - Pulsa Inicio y busca “Variables de entorno”.
   - Abre “Editar las variables de entorno del sistema”.
   - En “Variables del sistema”, selecciona `Path` → **Editar** → **Nuevo**.
   - Escribe `C:\ffmpeg\bin` y acepta todas las ventanas.
5. Cierra y vuelve a abrir la aplicación y comprueba con `ffmpeg -version`.

También puedes colocar `ffmpeg.exe` junto a `main.py` o en una carpeta
`ffmpeg\bin` dentro del proyecto; la aplicación lo detecta sin tocar el PATH.

### macOS

```bash
brew install ffmpeg
```

### Linux (Debian/Ubuntu/Mint)

```bash
sudo apt update
sudo apt install ffmpeg
```

---

## 4. Instalar la aplicación

1. Descarga o copia esta carpeta completa a tu equipo.
2. Abre una terminal **dentro de esa carpeta**. En Windows puedes abrir la
   carpeta en el Explorador, hacer clic en la barra de direcciones, escribir
   `cmd` y pulsar Enter.
3. Crea un entorno virtual (recomendado):

   ```powershell
   py -3 -m venv .venv
   ```

   Si `py` no se reconoce, usa `python -m venv .venv`.

4. Instala las dependencias usando directamente el Python del entorno
   virtual. Así no tienes que activarlo ni cambiar la política de scripts de
   PowerShell:

   ```powershell
   .venv\Scripts\python.exe -m pip install --upgrade pip
   .venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

   En macOS/Linux:

   ```bash
   python3 -m venv .venv
   .venv/bin/python -m pip install --upgrade pip
   .venv/bin/python -m pip install -r requirements.txt
   ```

Si prefieres activar el entorno, en Windows también puedes ejecutar
`.venv\Scripts\activate` desde el Símbolo del sistema. En PowerShell usa
`.\.venv\Scripts\Activate.ps1` (puede requerir cambiar la política de ejecución
de scripts).

Con esto quedan instalados `customtkinter` y `yt-dlp`.

---

## 5. Ejecutar

### Windows: doble clic

Haz doble clic en **`ejecutar.bat`**.

El archivo `.bat`:

- Usa el entorno virtual `.venv` si existe.
- Comprueba que Python y las dependencias estén instalados.
- Abre la aplicación sin dejar una ventana de consola abierta.
- Si falta algo, muestra un mensaje y no se cierra de golpe.

### Desde una terminal

```powershell
.venv\Scripts\python main.py
```

o, si no usas entorno virtual:

```powershell
python main.py
```

En macOS/Linux:

```bash
python3 main.py
```

---

## 6. Cómo se usa

1. Copia el enlace del video o de la lista en YouTube.
2. Pega el enlace en el campo de texto o pulsa **Pegar**.
3. Elige la calidad. Por defecto se usa **1080p (MP4)**.
4. Elige la carpeta con **Examinar...**.
5. Pulsa **Descargar**.
6. Observa el porcentaje, la velocidad y el tiempo restante.
7. Para detener la descarga, pulsa **Cancelar**.

### Listas de reproducción

Si el enlace contiene una lista, la aplicación pregunta antes de empezar:

- **Sí**: descarga toda la lista.
- **No**: si el enlace también apunta a un video concreto, descarga solo ese
  video.
- **Cancelar**: vuelve sin descargar nada.

En listas largas se recomienda no cancelar a mitad si no es necesario, porque
YouTube puede limitar temporalmente las peticiones.

### Nombres de archivo

Los archivos se guardan como `Título del video.mp4` o `Título del video.mp3`.
yt-dlp sustituye automáticamente los caracteres no válidos de Windows
(`\ / : * ? " < > |`), elimina puntos o espacios problemáticos al final y
recorta nombres demasiado largos.

### Si no hay 1080p

El formato usado es:

```text
bestvideo[height<=1080]+bestaudio/best[height<=1080]
```

Si el video no tiene 1080p, yt-dlp elige la mejor calidad disponible sin
fallar. Lo mismo ocurre con 720p y 480p.

### Audio MP3

En el modo “Solo audio (MP3)” se descarga la mejor pista de audio y FFmpeg la
convierte a MP3 de 192 kbps.

---

## 7. Actualizaciones automáticas de yt-dlp

YouTube cambia con frecuencia. Por eso, al iniciar, la aplicación ejecuta en
segundo plano el equivalente a:

```powershell
python -m pip install -U yt-dlp
```

- La versión instalada se muestra en la esquina inferior derecha.
- La interfaz no se bloquea durante la actualización.
- Si no hay internet, pip falla y el programa continúa con la versión que ya
  tenga instalada.
- Si una descarga falla, la aplicación intenta actualizar `yt-dlp`, recarga el
  módulo actualizado y reintenta una vez automáticamente.
- Si la actualización falla porque el equipo no tiene permisos, usa el mismo
  Python con el que instalaste las dependencias o crea el entorno virtual
  `.venv` como se explicó arriba.

**Importante:** la actualización se instala en el mismo Python que ejecuta la
aplicación. Si abres el programa con el `.bat`, se actualiza el Python del
entorno `.venv` (si existe) o el Python del sistema.

---

## 8. Configuración y registro

La aplicación crea estos archivos junto a `main.py`:

- **`config.json`**: última carpeta de destino y calidad elegida.
- **`log.txt`**: información y errores para depurar. Se rota al llegar a 2 MB
  y conserva dos copias antiguas.

Si la carpeta del programa es de solo lectura (por ejemplo, Programas de
Windows), los archivos se guardan en:

```text
%APPDATA%\DescargadorYouTube
```

En macOS/Linux se usa `~/.descargador_youtube` como respaldo.

El botón **Abrir log** de la ventana abre el registro directamente.

---

## 9. Solución de problemas

| Problema | Solución |
|---|---|
| “FFmpeg: NO encontrado” | Instala FFmpeg y añade su carpeta `bin` al PATH. Cierra y vuelve a abrir el programa o pulsa “Comprobar de nuevo”. |
| “El enlace no pertenece a YouTube” | Pega una URL de `youtube.com`, `youtu.be`, `music.youtube.com` o de una lista de reproducción. |
| “No hay conexión con YouTube” | Revisa internet, el firewall, el proxy o la VPN. |
| “YouTube rechazó la descarga (HTTP 403)” | Suele ser una versión antigua de yt-dlp o un cambio de YouTube. El programa intenta actualizar y reintentar. Si persiste, actualiza manualmente: `pip install -U yt-dlp`. |
| “YouTube pidió verificar que no eres un robot” | Espera unos minutos, actualiza yt-dlp y prueba otra red. |
| “Este video es privado” | No se puede descargar sin autorización del propietario. |
| “YouTube pide verificar la edad” | Requiere una sesión/cookies de una cuenta válida. Esta aplicación no solicita contraseñas ni gestiona cookies. |
| “No hay espacio suficiente” | Libera espacio o elige otra carpeta. |
| La ventana no abre al hacer doble clic | Ejecuta `python main.py` en una terminal para ver el error. Comprueba que instalaste `requirements.txt` dentro del entorno correcto. |
| `python` no se reconoce | Reinstala Python marcando “Add Python to PATH”, o usa `py -3` en Windows. |
| Faltan dependencias en el `.bat` | Abre una terminal en la carpeta y ejecuta `.venv\Scripts\python.exe -m pip install -r requirements.txt` (o `python -m pip install -r requirements.txt` si no usas `.venv`). |

Los detalles completos de cada error se guardan en `log.txt`.

### Nota sobre versiones recientes de yt-dlp

Algunas versiones nuevas de `yt-dlp` avisan en `log.txt` de que no encuentran un
motor de JavaScript (`No supported JavaScript runtime`). YouTube lo usa para
resolver ciertos formatos. La descarga suele seguir funcionando, pero si faltan
calidades puedes instalar **Deno** (opcional) y volver a abrir el programa:

```powershell
winget install --id DenoLand.Deno -e
```

En macOS: `brew install deno`. En Linux, consulta
<https://docs.deno.com/runtime/getting_started/installation/>. No es necesario
para usar la aplicación, pero mejora la compatibilidad futura con YouTube.

---

## 10. Crear un .exe con PyInstaller (opcional)

Primero instala PyInstaller en el mismo entorno:

```powershell
pip install pyinstaller
```

Después, desde la carpeta del proyecto:

```powershell
pyinstaller --noconfirm --clean --onefile --windowed --name DescargadorYouTube --collect-all customtkinter --collect-all yt_dlp main.py
```

El ejecutable aparecerá en:

```text
dist\DescargadorYouTube.exe
```

### Limitaciones del .exe (importante)

- El `.exe` **no se actualiza solo**. Incluye una copia fija de `yt-dlp` en el
  momento de compilarlo, y `pip install -U yt-dlp` no puede modificar esa copia
  interna. Cuando YouTube cambie, puede dejar de funcionar hasta que generes un
  nuevo `.exe`.
- FFmpeg sigue siendo externo: debe estar en el PATH del sistema o colocado en
  una carpeta `ffmpeg\bin` junto al `.exe`.
- `config.json` y `log.txt` se guardarán junto al `.exe` si la carpeta permite
  escritura; si no, en `%APPDATA%\DescargadorYouTube`.
- Para mantener yt-dlp actualizado, **es preferible usar `ejecutar.bat`** con
  Python instalado, en lugar del `.exe`.
- Si quieres un icono, añade `--icon=ruta\al\icono.ico`.
- Algunos antivirus tardan en analizar ejecutables de PyInstaller la primera
  vez. Es habitual que el arranque sea más lento que con Python.

Para actualizar un `.exe`, vuelve a compilarlo después de actualizar
`yt-dlp`:

```powershell
pip install -U yt-dlp
pyinstaller --noconfirm --clean --onefile --windowed --name DescargadorYouTube --collect-all customtkinter --collect-all yt_dlp main.py
```

---

## 11. Estructura del proyecto

```text
.
├── main.py             # Programa completo, comentado en español
├── requirements.txt    # customtkinter y yt-dlp
├── ejecutar.bat        # Lanzador para Windows
├── README.md           # Este archivo
├── config.json         # Se crea al usar la aplicación
└── log.txt             # Se crea al usar la aplicación
```

Comprobación rápida de sintaxis (opcional):

```powershell
python -m py_compile main.py
```
