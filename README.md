# 🚀 Hugo Control Center (Hugo Deck)

**Hugo Control Center** es un panel de control y visor de desarrollo en tiempo real premium, diseñado específicamente para simplificar y optimizar la creación, edición y despliegue de blogs estáticos basados en **Hugo**. Todo se ejecuta de manera aislada y eficiente dentro de un contenedor Docker.

---

## ✨ Características y Cualidades

*   **🎨 Interfaz Glassmorphic Ultra-Moderna**: Panel web premium diseñado con Tailwind CSS, modo oscuro integrado, efectos de desenfoque de cristal y animaciones de carga fluidas.
*   **🔄 Autorrefresco en Vivo (Live Reload)**: Hugo detecta cualquier modificación que hagas en tus artículos o plantillas locales y refresca la vista previa del navegador al instante.
*   **⏰ Auto-Apagado Inteligente por Inactividad**: Si cierras la pestaña del panel de control o dejas de interactuar por **15 minutos**, el contenedor apaga automáticamente el proceso del servidor Hugo en segundo plano para **ahorrar 100% de CPU y RAM** en tu servidor. ¡Se reactiva al instante cuando vuelves a abrirlo!
*   **📱 Previsualización Responsive Integrada**: Cambia el tamaño de la vista previa con un solo clic directamente desde la cabecera para ver cómo lucirá tu blog en **Móvil (375px)** 📱, **Tablet (768px)** 📟 o **Escritorio (100%)** 💻.
*   **📊 Estadísticas de Compilación en Tiempo Real**: Visualización de métricas detalladas del último build de Hugo directamente en la barra superior:
    *   *Total de páginas generadas*
    *   *Archivos estáticos procesados*
    *   *Último tiempo de compilación*
*   **📝 Control de Borradores e Hitos (Drafts)**: Un único interruptor sencillo para activar o desactivar la compilación de artículos marcados como borradores (`draft: true`) o con fecha futura. El servidor Hugo se reinicia y actualiza la página automáticamente al cambiarlo.
*   **💾 Configuración Simplificada**: La URL del repositorio Git se define como una variable de entorno (`REPO_URL`) en el archivo `docker-compose.yml`, haciéndola sumamente editable y limpia.
*   **💻 Visor de Logs Interactivo**: Consola de terminal integrada en la parte inferior que lee, colorea e imprime los logs de Hugo en tiempo real para facilitar la depuración de errores. Cuenta con un **indicador LED inteligente de estado (salud de compilación)** en el propio botón de logs:
    *   🟢 **Verde**: Compilación y funcionamiento correcto ("Todo OK").
    *   🔴 **Rojo Pulsante**: Se ha detectado un error o fallo de compilación en los logs de Hugo, avisándote al instante sin necesidad de abrir la consola.
*   **💻 Entorno Split-Screen con Auto-guardado Activo**: Abre el editor lateral derecho en pantalla partida (ocupando el 50% de ancho de manera fuertemente integrada) y previsualiza los cambios al instante en el 50% izquierdo. Cuenta con auto-guardado en segundo plano con *debounce* de 1.2 segundos y avisos de estado visuales (`Escribiendo...`, `Auto-guardando...`, `Cambios guardados automáticamente`).
*   **🕒 Listado Dinámico de Borradores y Futuros (Drafts Sidebar)**: Sidebar lateral premium que muestra todos tus borradores y futuras publicaciones ordenados cronológicamente de más nuevos a más antiguos, con etiquetas visuales (`Borrador`/`Futuro`) y libre de errores 404 de navegación gracias a la slugificación exacta de títulos de Hugo.
*   **✨ One-Click Push con Auto-guardado de Token**: Al realizar tu primer Push manual e introducir tu Token de Acceso Personal (PAT) de GitHub, este se escribirá de forma segura y permanente en el archivo `.env` de tu máquina host. A partir del siguiente segundo, cualquier clic en "Push" será directo, instantáneo y en un solo clic (usando el commit `"Update"` por defecto) sin tener que rellenar ningún dato ni reiniciar.
*   **🆕 Creación de Artículos con Plantilla Personalizada (New)**: La pestaña "New" te solicita el título de un artículo, crea automáticamente el archivo `.md` (ej. `20260507_slug.md`) en la carpeta del blog y lo abre al instante en el editor lateral. La cabecera YAML se genera leyendo el archivo `new_template.md` de tu host.

---

## 🛠️ Guía Rápida de Uso

### 1. Iniciar el Contenedor
Asegúrate de que tu archivo `.env` existe en la raíz del Deck (puede contener opcionalmente tu `GITHUB_TOKEN`). Levanta la pila de Docker de manera habitual:
```bash
docker compose up -d
```

### 2. Acceder al Panel de Control
Abre tu navegador preferido e ingresa a la siguiente dirección:
*   **Panel de Control:** `http://<IP_DE_TU_SERVIDOR>:1314`

### 3. Operaciones de Escritura y Edición Premium

*   **Pestañas Laterales Unificadas:** En el lateral derecho de la pantalla contarás con tres pestañas flotantes verticales alineadas físicamente y con colapso inteligente de espacio:
    *   **Drafts (Indigo):** Abre la barra lateral con tu lista ordenada de borradores y publicaciones programadas cronológicamente para navegar en ellas con un clic. Se oculta si desactivas "Drafts" en la barra superior.
    *   **New (Ámbar):** Crea un nuevo artículo en segundos. Te pedirá el título del post, creará el archivo físico en el blog y lo cargará en pantalla partida listo para escribir.
    *   **Edit (Esmeralda):** Abre el editor lateral de pantalla partida para modificar el artículo que estés visualizando en ese momento en el iframe.
*   **Auto-guardado Inteligente:** Olvídate de pulsar "Guardar". Conforme escribes en el editor lateral, un temporizador de silencio (*debounce*) de 1.2 segundos guardará tus cambios automáticamente en segundo plano de manera silenciosa y recompilará tu sitio Hugo al instante.
*   **One-Click Push:** Pulsa el botón "Push" en la barra superior para subir tus cambios a GitHub. Si es tu primera vez, el modal te solicitará el Token y el mensaje de commit; este Token se guardará permanentemente en tu archivo `.env` de forma automática. De ahí en adelante, pulsar "Push" ejecutará la subida completa directamente en un clic mostrando barras de carga visuales y confirmación en el botón.

---

## ⚙️ Integraciones y Archivos de Configuración

### 1. Sincronización de Scroll Bidireccional (`baseof.html`)
Para habilitar el desplazamiento milimétrico sincronizado entre el cursor de tu editor de texto y el iframe renderizado de vista previa de Hugo, añade el siguiente script dentro de las etiquetas `<head>` o justo antes del cierre de `</body>` en la plantilla principal de tu tema de Hugo (generalmente ubicada en `layouts/_default/baseof.html`):

```html
<!-- Sincronización de Scroll y navegación CORS con Hugo Deck -->
{{ if hugo.IsServer }}
<script>
 if (window.self !== window.top) {
     // Informa a Hugo Deck de la navegación interna del iframe para habilitar la edición rápida
     window.parent.postMessage({
         type: 'hugo-deck-navigate',
         path: window.location.pathname
     }, '*');

     // Escucha el evento de desplazamiento del editor para sincronizar el scroll de vista previa
     window.addEventListener('message', function(event) {
         if (event.data && event.data.type === 'hugo-deck-scroll') {
             const scrollHeight = document.documentElement.scrollHeight - window.innerHeight;
             window.scrollTo({
                 top: scrollHeight * event.data.percentage,
                 behavior: 'auto'
             });
         }
     });
 }
</script>
{{ end }}
```

### 2. Personalización de Plantilla de Nuevos Artículos (`new_template.md`)
En la raíz de la carpeta de tu Deck en el host contarás con el archivo de configuración `new_template.md`. Puedes abrirlo y editar libremente su cabecera YAML o contenido de post predeterminado. El sistema de Hugo Deck leerá esta plantilla cada vez que pulses "New" y sustituirá de forma dinámica los siguientes comodines de texto:
*   `{title}`: Título que le des al artículo al crearlo.
*   `{date}`: Fecha actual del sistema en formato `"AAAA-MM-DD"`.
*   `{creation}`: Fecha de creación del post en formato `"AAAA-MM-DD"`.
*   `{thumbnail}`: Ruta automática pre-calculada de tu imagen de portada (ej. `"images/20260507_mi_post_00.jpg"`).

*Ejemplo de plantilla predeterminada customizable:*
```markdown
---
title: "{title}"
date: "{date}"
creation: "{creation}"
description: "He creado {title} para compartir mis opiniones y conocimientos."
thumbnail: "{thumbnail}"
disable_comments: true
authorbox: false
toc: false
mathjax: false
categories:
- "computing"
tags: 
- "blog"
draft: true
weight: 5
---

Escribe aquí el contenido de tu nuevo artículo...
```
