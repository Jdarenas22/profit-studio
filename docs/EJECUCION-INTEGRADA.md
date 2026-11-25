# ProFit Studio integrado y gratuito

Alcance vigente: una sola aplicación con APIs reales, sin variante demo ni cuentas sembradas.
Código y base habituales en `C:\Proyectos\profit-studio`; puerto único `127.0.0.1:8000`.
La separación de worktrees sirve para desarrollar en equipo, no representa otra versión de la web.

## Configuración privada

En `.env`, conservando la clave existente y sin copiarla a `.env.example`:

```dotenv
AI_PLANS_MODE=live
GEMINI_MODEL=gemini-3.5-flash-lite
GEMINI_ACCESS_TIER=free
GEMINI_FREE_TIER_CONFIRMED=true
GEMINI_BILLING_CONFIRMED=false
GEMINI_MAX_DAILY_CALLS=30
NUTRITION_REQUIRE_USDA_REFERENCE=true
```

La declaración Free recoge la comprobación de la persona en AI Studio: el servidor no puede
certificar el estado de facturación de Google. No cambia a un modelo pago ni a respuestas falsas.
La cuota local cuenta intentos, también fallidos, en `GeminiDailyUsage` dentro de la base normal.
Las cuotas efectivas de Google siguen aplicándose; un límite devuelve un error controlado.

## Arranque único

Instalar dependencias con el entorno del proyecto: `pip install -r requirements/local.txt`.
Ejecutar `scripts/start.ps1` para acceso local o `scripts/start.ps1 -Public` para Internet.
Ambos usan exactamente la misma aplicación y base. Se debe detener un arranque antes de iniciar otro.
El script utiliza Waitress, sin DEBUG, en loopback y puerto 8000. Prepara las migraciones,
sincroniza el catálogo USDA y genera estáticos. Si hay migraciones pendientes sobre una base
existente, crea antes una copia local en `.runtime/backups/`, privada e ignorada por Git.

`-Public` conecta un Cloudflare Quick Tunnel al mismo puerto: no crea otro servidor Django.
Si hace falta, instala cloudflared portable en `.tools/` con una versión y SHA256 verificadas
contra el artefacto oficial. La URL HTTPS se muestra al iniciar y se guarda en `.runtime/public-url.txt`.
Ctrl+C detiene servidor y túnel. Si el arranque está oculto, `scripts/start.ps1 -Stop` solicita la misma parada sin cerrar otros procesos. Si el PC se apaga o pierde conexión, la web deja de estar disponible.
La URL cambia en cada nuevo túnel. El servicio no ofrece garantía de disponibilidad.
Las cookies de sesión y CSRF llevan Secure en la URL pública; el mismo proceso permite el acceso local.

No se crean usuarios, membresías ni planes de ejemplo. Utiliza tus cuentas existentes y el recorrido
normal de ficha, solicitud, revisión y aprobación. No se borran ni mezclan los datos históricos
de `.demo/`; dejaron de ser una base activa y sus ejecutables fueron retirados.

## IA y catálogo reales

Free recibe nombres y características de recetas o ejercicios públicos y restricciones de estructura.
No recibe ficha, edad, sexo, peso, medidas, metas energéticas, notas personales o historial.
En alimentación devuelve nombres de recetas; Django recupera ingredientes y cantidades ya calculados,
valida porciones y el día completo y guarda la procedencia USDA. Se conservan revisión y permisos.
La lista de recetas se filtra localmente antes de consultar al modelo. No se ofrecen tratamientos clínicos.

La importación offline normal es `python manage.py load_usda_catalog --apply --activate`.
Existen 70 referencias utilizables y 20 pendientes de especificación; ausente significa desconocido,
no cero. No se sobrescriben referencias diferentes o revisiones profesionales sin revisión explícita.
La auditoría sin escribir es `python manage.py audit_food_catalog`; añadir `--database` revisa la base.
`python manage.py check_gemini` hace una comprobación real acotada y cuenta contra el presupuesto.

Las simulaciones se conservan como soporte de pruebas automatizadas; el arranque normal exige live
y nunca las usa como sustituto silencioso del proveedor. Los planes históricos conservan su origen real.
Wompi, correo y WhatsApp mantienen sus integraciones normales según credenciales y configuración;
no se activan cobros ni envíos por preparar la aplicación. Sus condiciones son independientes de Gemini.

## Integración y comprobaciones

Esta rama incorpora el worker opcional de Claude; en el PC no exige un segundo servicio ni Redis.
`AI_PLANS_WORKER` permanece apagado para el arranque local: los trabajos se procesan por el flujo normal.
La migración `plans 0006` guarda solo fecha y contador de llamadas, sin datos personales.
No se tocan migraciones anteriores ni se migra producción desde un equipo local.

Fuentes verificadas el 23 de noviembre de 2026:
- [Gemini precios y nivel gratuito](https://aistudio.google.com/docs/pricing).
- [Gemini condiciones y uso de datos](https://aistudio.google.com/docs/terms).
- [Cloudflare Quick Tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/).
- [Waitress en PyPI](https://pypi.org/project/waitress/).

No se recomienda SQLite en el disco efímero de un alojamiento gratuito: puede perderse al reiniciar.
Esta alternativa mantiene base y archivos en tu PC; publicar en nube de forma permanente requerirá
una decisión aparte sobre persistencia y límites, aunque se utilicen planes gratuitos.
