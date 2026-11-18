# Experiencia de cliente y revisión de planes

Entrega autorizada el 2026-11-18, sobre main con fase D y demo Gemini fusionadas.
Se conserva la identidad oscura/naranja y se da prioridad a las tareas de cada rol.

## Cambios

- Panel del cliente: accesos principales a rutina y alimentación; las rutinas completas
  se despliegan bajo demanda y la membresía pasa después del progreso y las preferencias.
- Navegación: enlaces según rol, «Membresías» para la oferta comercial y barra inferior
  para el cliente en móvil (Hoy, Rutina, Alimentación cuando está habilitada y Perfil).
  Se mantienen las banderas de funciones y los permisos de servidor.
- Alimentación: resumen separado de comidas elegidas y cumplidas, objetivos diarios
  desplegables, cinco comidas y sus opciones con controles HTML nativos. Se conservan
  ingredientes, porciones, advertencias, selección, alternativas y registro de cumplimiento.
  Las cantidades ausentes siguen identificadas como incompletas, sin convertirlas en cero.
- Entrenador: aprobación primero en la columna de acciones, acceso directo desde el resumen,
  regeneración/archivo desplegables y revisión de comidas por secciones. Si hay problemas de
  aprobación o conflictos alimenticios, las secciones se abren para hacerlos visibles.
  Los flujos de clonación de fase D se conservan.
- Estados de generación: etapas y progreso basados en los datos del trabajo. No se inventan
  porcentajes ni tiempos restantes. Las respuestas y códigos de error mantienen su tratamiento.
- Demo: bienvenida integrada con el estilo del proyecto y accesos de cliente/entrenador
  al inicio de sesión habitual; no hay accesos automáticos ni claves expuestas en la interfaz.
- Lectura y teclado: contraste de textos secundarios y botones, foco visible, enlace para
  saltar al contenido, etiqueta/estado del menú móvil, Escape y retorno del foco.
- Impresión: se abren temporalmente los desplegables y se restaura su estado después;
  los enlaces internos también revelan sus secciones. Se respeta movimiento reducido.

## Verificación

- 1.510 pruebas completas aprobadas, sin secretos ni servicios externos.
- Después de los últimos ajustes: 90 pruebas de menú de cuenta, alimentación y compilación
  de Tailwind aprobadas; comprobación final de los 6 tests del build CSS también aprobada.
- `makemigrations --check --dry-run`: sin cambios. Sin modificaciones de modelos, servicios,
  cálculos alimenticios, APIs, configuración de producción o dependencias.
- Vistas renderizadas mediante el cliente de Django con cuentas ficticias: panel, alimentación,
  revisión de entrenador y bienvenida. Inspección en navegador de escritorio y móvil a 390 px;
  las tres pantallas principales también comprobadas a 320 px, sin desbordamiento horizontal.
- Verificados en navegador los desplegables, apertura/cierre de menú y Escape con retorno de foco.
  Verificada la lógica de impresión/restauración y enlaces internos mediante un entorno JS aislado.
- La revisión visual usa capturas HTML sin sesiones ni tokens de los perfiles de `.demo/`.
  Las operaciones POST y permisos se comprueban con las pruebas de Django, no con esas capturas.
  La entrada real `/demo/` se verificó en el servidor local actualizado. No hubo generación real de IA.

Estas comprobaciones no equivalen a una certificación completa de accesibilidad ni sustituyen
un ensayo con usuarios. La siguiente revisión útil es observar a una persona completando el
recorrido desde su teléfono, especialmente comparación de opciones y aprobación.

## Coordinación con Claude

Rama `feature/ux-plans-workspace`. Cambios de presentación, con fase D preservada.
`static/css/app.css` se regeneró mediante `scripts/build-css.ps1`; no resolver su minificado
a mano si otra rama lo cambia. Regenerarlo desde `frontend/tailwind.css` tras integrar.
La carpeta local `.wrangler/` ya existía sin seguimiento y no se modifica ni se incluye.
No fusionar o desplegar como parte de esta entrega sin coordinar la revisión habitual.
