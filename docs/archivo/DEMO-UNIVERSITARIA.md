# Documento histórico retirado de ejecución

Este procedimiento fue reemplazado por `docs/EJECUCION-INTEGRADA.md`.
No ejecutar sus comandos: la aplicación actual no utiliza settings, rutas ni bases de demo.
Se conserva únicamente como evidencia del desarrollo anterior.

# Demostración universitaria de ProFit Studio

Esta entrega permite mostrar planes alimenticios y rutinas con perfiles ficticios, sin preparar
un lanzamiento a producción. El flujo es una generación guiada por formularios y trabajos;
no incorpora un chat conversacional. No se activa facturación ni se despliega a Railway.

## Inicio en Windows

Desde `C:\Proyectos\profit-studio`, con el entorno de dependencias instalado:

```powershell
# Sin red: respuestas locales claramente marcadas como simuladas.
.\scripts\start-demo.ps1

# Gemini real: usar solamente después de confirmar Free en el proyecto de Google AI Studio.
.\scripts\start-demo.ps1 -Real -FreeTierConfirmed
```

La segunda opción requiere la clave privada `GEMINI_API_KEY` del backend y el modelo
`gemini-3.5-flash-lite`. Si `GEMINI_MODEL` está vacío, el perfil demo usa ese modelo.
Si está configurado otro, se bloquea; no se sustituye silenciosamente. No poner claves en
plantillas ni en `.env.example`. No cambiar `GEMINI_BILLING_CONFIRMED` para esta demo.

El script migra exclusivamente `.demo/demo.sqlite3`, prepara las cuentas si la base está vacía
y sirve `http://127.0.0.1:8010/demo/`. La cuenta de entrenador es `demo_entrenador`; los clientes
son `demo_grasa`, `demo_musculo` y `demo_mantenimiento`. Las contraseñas se generan al preparar
la muestra y quedan en `.demo/access.local.txt`, excluido de git. No se imprimen en registros.
Una segunda preparación conserva claves y planes. No usar estas cuentas fuera de localhost.

La carpeta `.demo/` también contiene sus medios, registros y presupuesto de llamadas. La base
`db.sqlite3` y `media/` del desarrollo normal no se usan ni se copian. No borrar una base para
reiniciar la demostración: primero conservar los resultados que se quieran mostrar.

## Recorrido de la presentación

1. Abrir `/demo/`, identificar públicamente si la ejecución es simulada o Gemini real.
2. Entrar como cliente ficticio, revisar su objetivo y rutina, y pedir un plan de alimentación
   desde «Mis planes». Esperar la generación; no repetir clics para forzar otra solicitud.
3. Entrar como entrenador y abrir «Planes por revisar». Revisar el borrador y aprobarlo.
4. Volver al cliente y mostrar desayuno, media mañana, almuerzo, merienda y cena, opciones,
   cantidades y totales calculados. Un cliente no puede consultar los pedidos de otro.
5. Para una rutina generada con IA, solicitar el plan de ejercicio, revisar el borrador y aprobarlo.
   Las rutinas de partida ya sembradas permiten mostrar el entrenamiento sin consumir API.

Para generar desde consola, siempre en la base académica:

```powershell
$env:DEMO_AI_MODE='demo'
$env:DEMO_FREE_TIER_CONFIRMED='true'
.\.venv-secure\Scripts\python.exe manage.py generate_academic_demo --username demo_musculo --kind nutrition --settings=config.settings.demo
```

El comando crea un borrador por el flujo habitual; la revisión permanece en la interfaz.
Para la presentación, conviene conservar un menú real generado anteriormente y los planes
simulados de respaldo. Nunca presentar un resultado simulado como una respuesta de Gemini.

## Organización y límites

- `config/settings/demo.py`: perfil explícito, SQLite y medios separados, solo localhost,
  correo en memoria, WhatsApp apagado, claves de pagos vacías y cookies independientes.
- `apps/plans/ai/demo.py`: cliente real habilitado solo para el entorno académico, el modelo
  permitido y trabajos de cuentas ficticias. La declaración Free no verifica por API el estado
  de facturación de Google; la persona lo comprueba en AI Studio antes de activar el modo.
- Presupuesto local: máximo 30 intentos al día UTC, compartido entre procesos y persistente
  entre reinicios. Los intentos fallidos también cuentan. Este límite reduce llamadas; no
  garantiza la cuota disponible en Google ni impide cobros si alguien cambia el proyecto a pago.
  Nunca se aumenta una cuota ni se cambia a un modelo pago automáticamente.
- `apps/plans/jobs.py` y `training_context.py`: envían un resumen de la rutina activa propia
  (días, ejercicios por día, series e intensidad), sin nombres, notas ni identificadores.
  Los objetivos energéticos siguen calculándose con actividad declarada: no se estima gasto
  calórico exacto de una rutina ni se alteran porciones al margen de las reglas de Django.
- Gemini selecciona recetas permitidas; Django filtra alimentos, valida porciones, recalcula
  cantidades y comprueba el día completo antes de guardar. La salida inválida no se publica.
- La demo sincroniza 70 referencias de composición USDA verificadas contra su evidencia
  y excluye 20 alimentos que necesitan precisar variedad o receta. No se certifica una marca,
  receta local ni revisión profesional. Las cantidades importadas se muestran en gramos;
  los nutrientes ausentes permanecen desconocidos. Los planes anteriores se conservan.
  Inventario, controles y comandos: [catálogo USDA](nutricion/2026-11-18-catalogo-usda.md).
- El tercer perfil utiliza el objetivo existente de resistencia cardiovascular para ilustrar
  mantenimiento energético; no se añadió una nueva categoría comercial «mantenimiento».
- Los textos y consentimientos sembrados son exclusivos de personas ficticias. Los perfiles
  clínicos siguen bloqueados y no se ofrecen tratamientos para diabetes o celiaquía.
- El modo `demo` es rechazado por la configuración de producción y no cambia el comportamiento
  de `live`. No se modifica la fase D de Claude ni su CSS. Esta rama incorpora los fixes del PR #29.

## Comprobación realizada

Se verificaron tres perfiles de extremo a extremo con generación simulada, cinco comidas,
aprobación, consulta del menú (HTTP 200) y rechazo del pedido de otro cliente (HTTP 404).
También se generó un menú real completo de Gemini para el perfil ficticio de ganancia muscular;
y una rutina real para el perfil ficticio de reducción de grasa. Ambos planes se aprobaron
en la base de muestra y sus páginas de cliente respondieron HTTP 200. La suite completa
aprobó 1.401 pruebas y no hay migraciones nuevas. Las comprobaciones automatizadas nunca
utilizan proveedores reales. Evidencia: `docs/auditorias/2026-11-18-demo-resultado.json`.

Fuentes oficiales para comprobar vigencia de nivel y cuotas:
[precios](https://aistudio.google.com/docs/pricing) y
[límites](https://aistudio.google.com/docs/rate-limits).
Las cuotas son por proyecto y varían por modelo; el nivel gratuito no implica uso ilimitado.

Actualización USDA: suite completa de 1.527 pruebas aprobadas; tres perfiles simulados
con cinco comidas y cobertura de composición USDA en sus 45 opciones. La prueba real
de Gemini descrita arriba es anterior a esta actualización del catálogo.

Cierre actualizado: [USDA y Gemini real, 2026-11-23](nutricion/2026-11-23-cierre-usda-gemini.md).
