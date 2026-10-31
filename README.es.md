# EveryTensor

[简体中文](README.md) | [English](README.en.md) | [日本語](README.ja.md) | **Español**

Versión preliminar para Windows · v0.1.0-alpha.1

Cada archivo es un vector.

No necesitas recordar los nombres de los archivos. Los vectores te ayudan a encontrarlos.

## Funciones de búsqueda

- Busca nombres de archivo, rutas y texto. Puedes usar palabras clave en chino.
- Busca texto por su significado con el modelo de texto ligero.
- Busca imágenes, vídeo y audio por su contenido con el modelo completo.
- Consulta una vista previa de los resultados. Un resultado puede incluir una página, línea, hoja de cálculo o posición temporal.
- Actualiza el índice en segundo plano. Puedes pausar o reanudar la indexación en la configuración.

Los formatos incluyen TXT, Markdown, código fuente, PDF, DOCX, PPTX, XLSX, EPUB, imágenes, vídeo y audio.

El índice guarda información de los archivos para las búsquedas. Puedes buscar cada archivo por su nombre cuando la aplicación lo encuentra. Para buscar contenido, la aplicación debe leerlo primero.

## Requisitos

- Windows de 64 bits.
- Python 3.11. Durante la instalación de Python, selecciona «Add python.exe to PATH».
- Conexión a internet para instalar paquetes y modelos.

Para usar audio y vídeo, instala [FFmpeg](https://ffmpeg.org/download.html). Comprueba que los comandos `ffmpeg`, `ffprobe` y `ffplay` funcionan en una terminal.

## Instalación e inicio

Esta versión preliminar se ejecuta desde el código fuente.

1. Descarga el [ZIP con el código fuente](https://github.com/SinsapZ/everytensor/releases/download/v0.1.0-alpha.1/everytensor-0.1.0-alpha.1-source.zip).
2. Extrae el ZIP en una carpeta donde tengas permiso de escritura.
3. Ejecuta `setup-quick-model.cmd`.
4. Espera a que termine la instalación.
5. Ejecuta `launch.cmd`.
6. Pulsa `Ctrl+,` para abrir la configuración.
7. Selecciona las carpetas de búsqueda. Para el primer uso, empieza con una carpeta pequeña.

El script de instalación crea `.venv`, instala los paquetes y descarga el modelo de texto ligero.

**Al iniciar por primera vez, la aplicación busca en todas las unidades locales fijas a las que tiene acceso.** Puedes reducir el alcance de búsqueda o excluir carpetas en la configuración. El límite de espacio del índice es de 8 GiB por defecto. Los archivos de los modelos ocupan espacio adicional.

### Otras opciones de instalación

| Script | Función |
| --- | --- |
| `setup-desktop.cmd` | Instala la aplicación sin descargar un modelo. Permite buscar por nombre y palabras clave, y ver vistas previas. |
| `setup-model.cmd` | Instala el modelo completo y sus paquetes. También prepara el modelo de texto ligero. |

Cierra EveryTensor antes de ejecutar `setup-model.cmd`. Al terminar la instalación, inicia la aplicación con `launch.cmd`.

El modelo de texto ligero es [static-similarity-mrl-multilingual-v1](https://huggingface.co/sentence-transformers/static-similarity-mrl-multilingual-v1). El modelo completo es [Google EmbeddingGemma 2](https://huggingface.co/google/embeddinggemma-2). El modelo completo necesita más espacio en disco y memoria.

Si el modelo completo no funciona, puedes usar los resultados existentes de nombres, palabras clave y del modelo de texto ligero.

### Inicio desde una terminal

Abre una terminal en la carpeta del proyecto. Ejecuta estos comandos en orden:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -X utf8 tools\install_quick_model.py
.\.venv\Scripts\python.exe -m everytensor
```

Si la ventana se cierra al iniciar, ejecuta el último comando en una terminal para ver el error.

## Teclas y datos locales

| Tecla | Acción |
| --- | --- |
| `Ctrl+K`, `Ctrl+L` o `Ctrl+F` | Ir al campo de búsqueda |
| `↑`, `↓` | Seleccionar un resultado |
| `Enter` | Abrir el resultado |
| `Esc` | Borrar la búsqueda |
| `Ctrl+,` | Abrir la configuración |

La aplicación lee archivos, crea el índice y ejecuta los modelos en tu equipo. Las descargas de paquetes y modelos no suben tus archivos. La aplicación no modifica los archivos originales.

Estas carpetas están dentro de la carpeta del proyecto. El ZIP con el código fuente no las incluye.

| Carpeta | Contenido |
| --- | --- |
| `.everytensor/` | Índice y configuración, con rutas de archivo y texto extraído |
| `models/` | Modelos descargados |
| `.venv/` | Entorno de Python y paquetes |

El índice puede contener datos personales. No lo subas a un lugar público. Antes de compartir registros, elimina las rutas y el contenido privados.

## Licencia

Copyright (C) 2026 EveryTensor contributors.

El código fuente usa **GNU Affero General Public License v3.0 only** (`AGPL-3.0-only`). Consulta los términos y la exclusión de garantías en [LICENSE](LICENSE).

Los paquetes y modelos de terceros tienen sus propias licencias. Consulta [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
