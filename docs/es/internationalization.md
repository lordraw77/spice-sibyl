# Internacionalización (i18n)

**Qué hace.** SpiceSibyl habla cinco idiomas en ambos canales — **inglés, francés, alemán, italiano y español** — con un selector de idioma en runtime en la consola web y un idioma por chat en el bot de Telegram. En la web, cambiar de idioma no requiere recompilación ni recarga de la página.

> Versiones: [English](../en/internationalization.md) · [Italiano](../it/internazionalizzazione.md)

## Consola web

- **Selector de idioma.** Un botón 🌐 en la barra de navegación (junto a los controles de tema/acento) abre un menú de los cinco idiomas; el activo queda resaltado. El cambio redibuja la interfaz al instante.
- **Detección automática.** En la primera visita — cuando el usuario aún no ha elegido idioma — el idioma del navegador (`navigator.languages`) se compara con los soportados; si ninguno coincide, se usa el predeterminado histórico (**italiano**), preservando el comportamiento original para los usuarios existentes.
- **Persistencia.** La elección se guarda en `localStorage` (inmediata, sin conexión) **y** en el perfil activo vía `PATCH /api/v1/profiles/{id}` (`{ "locale": "es" }`), de modo que sigue al usuario entre dispositivos. El idioma guardado en el perfil se adopta al iniciar sesión/seleccionar *a menos que* el navegador ya contenga una elección explícita local.
- **Cobertura.** Barra de navegación y menús, los tooltips idioma/tema/acento, los indicadores de carga del chat, las páginas (proveedores, estadísticas, herramientas, workflows, MCP, espacios, etc.), el modal de perfil, los toasts y el recorrido de onboarding están localizados. La entrada de voz (Web Speech API) y el TTS siguen el tag BCP-47 del idioma activo (p. ej. `es-ES`) en lugar del anterior `it-IT` fijo.

### Arquitectura

Una capa i18n en runtime ligera y sin dependencias (en el estilo minimalista del proyecto — véase el catálogo de Telegram y el cliente MCP sin SDK):

| Componente | Archivo |
|------------|---------|
| Metadatos de idioma (códigos, etiquetas nativas, tags BCP-47) | [`core/i18n/locale.ts`](../../frontend/src/app/core/i18n/locale.ts) |
| Catálogos (un mapa plano `clave → cadena` por idioma) | [`core/i18n/translations/*.ts`](../../frontend/src/app/core/i18n/translations/) |
| `I18nService` (signal del idioma activo, detección, persistencia, `translate()`, formateadores) | [`core/i18n/i18n.service.ts`](../../frontend/src/app/core/i18n/i18n.service.ts) |
| `TranslatePipe` (`\| t`) — impura, para reaccionar a los cambios de idioma | [`core/i18n/translate.pipe.ts`](../../frontend/src/app/core/i18n/translate.pipe.ts) |
| Pipes de formato localizado (`\| localeNumber`, `\| localeCost`, `\| localeDate`) | [`core/i18n/format.pipes.ts`](../../frontend/src/app/core/i18n/format.pipes.ts) |

Uso en una plantilla:

```html
{{ 'nav.chat' | t }}
{{ 'chat.providerSwitch' | t: { from: 'groq', to: 'ollama' } }}   <!-- los {placeholders} se rellenan desde params -->
{{ message.estimated_cost | localeCost:6 }}
```

Orden de resolución de una clave: **catálogo del idioma activo → catálogo predeterminado (`it`) → la clave misma.** Añadir un idioma = añadir un archivo de catálogo, registrarlo en `I18nService.CATALOGS` y añadir una entrada a `SUPPORTED_LOCALES`.

### Formato localizado

Números, costes y fechas se renderizan según el idioma activo mediante la API `Intl`, indexada por el tag BCP-47 del idioma:

- `localeNumber` → `Intl.NumberFormat` (separadores de miles y decimales)
- `localeCost` → `Intl.NumberFormat` con `style: 'currency', currency: 'USD'` (posición del símbolo según el idioma: `$1.23` vs `1,23 $`)
- `localeDate` → `Intl.DateTimeFormat`

## Bot de Telegram

- **Idioma por chat.** `/lang` muestra un teclado inline con los cinco idiomas; `/lang en|fr|de|es|it` lo establece directamente. La elección persiste en `telegram_prefs` y se precarga al arrancar.
- **Catálogo.** Toda la salida de comandos, los teclados inline, las acciones rápidas, los recordatorios y los mensajes de error viven en [`backend/app/telegram/i18n.py`](../../backend/app/telegram/i18n.py) (`MESSAGES[locale][key]`), con cadena de respaldo `locale → predeterminado (it) → clave`.
- **Confirmaciones localizadas.** Las horas de los recordatorios usan un orden de fecha localizado (el inglés usa mes/día, los demás idiomas día/mes) en el `TIMEZONE` del chat.

## Documentación y capturas de pantalla

Los documentos y sus capturas son por idioma: `docs/<lang>/*.md` + `docs/<lang>/screenshots/*.png` para cada uno de `en`, `it`, `fr`, `de`, `es`. La **Ayuda en la app** (`/help`) carga el conjunto de documentos correspondiente al idioma activo de la interfaz, con repliegue al inglés si falta un conjunto; las capturas apuntan a `docs/<lang>/screenshots/`. Las capturas se generan con Playwright mediante [`frontend/scripts/screenshots.mjs`](../../frontend/scripts/screenshots.mjs).

## Configuración

No se requiere configuración — los cinco idiomas se incluyen en la aplicación. El idioma predeterminado histórico es el italiano; cámbialo editando `DEFAULT_LOCALE` en `core/i18n/locale.ts` (web) e `i18n.py` (Telegram).
