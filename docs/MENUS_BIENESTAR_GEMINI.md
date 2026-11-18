# Menús de bienestar con recetas reales y Gemini

Implementado el 17 de noviembre de 2026. Este documento actualiza el alcance de alimentación del contrato anterior: dietas clínicas y asesoramiento para tratar enfermedades quedan pausados.

## Alcance actual

Se generan borradores de menús cotidianos para objetivos de entrenamiento: pérdida de peso/grasa, mantenimiento y ganancia muscular según los objetivos ya existentes. El motor Python estima energía y macronutrientes; Gemini selecciona entre preparaciones completas compatibles.

No se quitaron restricciones de alimentos, permisos, revisión del entrenador, consentimiento ni controles del proveedor. Cambió el servicio ofrecido: ya no se genera automáticamente una adaptación para condiciones médicas. Si la ficha declara condiciones, medicamentos, embarazo/lactancia/posparto o antecedentes de dificultades alimentarias (incluida respuesta reservada), la generación de alimentación queda bloqueada antes de contactar al proveedor. Un menú anterior deja de mostrarse y no puede aprobarse para una ficha fuera de ese alcance. Los registros previos no se borran.

Las alergias e intolerancias se conservan como exclusiones de ingredientes; el sistema no recomienda tratamientos. Las pantallas de salud y sus datos existentes no se eliminaron: siguen siendo necesarios para el resto de funciones del proyecto.

## Recetario y variedad

El catálogo técnico inicial contiene 52 plantillas de preparación en apps/plans/rules/recipes.py, versión everyday-world/1:

- Arepa con huevo y aguacate; arepa con queso y huevo.
- Avena con yogur, fruta y frutos secos; bowls con soya.
- Tostadas, sándwich frío de atún, tortillas.
- Arroz con proteína y ensalada; yuca, papa o plátano con acompañamientos.
- Bowls de quinua, tacos, pasta con atún y preparaciones de inspiración mediterránea.
- Lentejas, fríjoles, ensalada de garbanzos y opciones con tofu.

Cada plantilla define nombre, estilo culinario, tiempos de comida, ingredientes exactos, intervalos de porción y preparación. No se limita a cocina colombiana; los ingredientes deben existir en el catálogo local permitido. Las cifras nutricionales siguen siendo las del catálogo Food del proyecto: este cambio no las convierte en datos verificados ni importa nuevas tablas de nutrientes.

Una receta desaparece de las opciones si falta un ingrediente o si está excluido. No se reemplaza arbitrariamente por otro alimento. Para quienes declaran que no cocinan se ofrecen solo montajes fríos/sin cocción etiquetados como tales; se presupone que los ingredientes indicados como cocidos se consiguen listos. Las porciones respetan los límites técnicos y las comprobaciones de energía y macronutrientes existentes.

## Qué cambió en la generación

1. Se verifican acceso, consentimientos, alcance y datos necesarios.
2. Se calculan los objetivos en Python usando el motor ya existente.
3. Se filtra Food por dieta, alergias, trazas, intolerancias y disponibilidad.
4. Para el tiempo de comida, se buscan recetas completas y porciones compatibles.
5. Antes de llamar a Gemini se comprueba que hay suficientes opciones distintas.
6. Gemini recibe recipe_options, una lista de nombres y porciones autorizados, y devuelve opciones con el contrato meal-slot/1. El esquema limita los nombres a los ofrecidos.
7. El backend rechaza cambios de ingredientes o porciones con V09_RECIPE. Recalcula totales y mantiene energía, macros, variedad y revisión de texto. La preparación final se toma del recetario.
8. Se revisa el conjunto del día y se guarda el borrador. El entrenador lo revisa y aprueba antes de publicarlo.

Los prompts nuevos son meal/3 y meal/4 (con historial). El catálogo debe versionarse junto con esos prompts cuando se cambien recetas o reglas de porción. No se actualizan automáticamente menús ya aprobados: se debe generar un borrador nuevo para usar el recetario.

El simulador también selecciona recetas completas. La ruta normal de generación ya no utiliza el combinador aleatorio de alimentos. La función anterior queda únicamente para pruebas unitarias que ejercitan el contrato antiguo sin recipe_options.

Cuando no hay tres opciones válidas, no se improvisan combinaciones ni se relajan alergias: se informa que no hay suficientes recetas compatibles. Puede ocurrir con catálogos muy pequeños, restricciones simultáneas o porciones fuera de lo que cubren las recetas iniciales. Ampliar el recetario es una tarea continua.

## Cinco tiempos y entrenamiento

Las fichas nuevas proponen desayuno, media mañana, almuerzo, merienda y cena. El usuario puede mantener tres a cinco tiempos; los horarios y selecciones guardados anteriormente se conservan.

El payload de alimentación incluye ahora frecuencia semanal, duración de sesión y nivel de experiencia. El objetivo y nivel de actividad ya alimentaban el cálculo energético. Esta entrega no agrega un selector nuevo de modalidad deportiva ni un cálculo específico por deporte; no debe confundirse la duración con una medición real del gasto.

Los datos de condiciones y medicamentos no se envían a Gemini. Las notas libres del entrenador tampoco se envían en alimentación, para evitar introducir información clínica o instrucciones que cambien el alcance. Esas notas se conservan en el trabajo local; el flujo de ejercicio mantiene sus notas como antes.

## API y configuración

Se reutiliza la integración REST existente en apps/plans/ai/gemini.py; no se necesita otra API de cálculo ni se añadieron dependencias. La clave solo vive en el servidor.

Variables ya existentes para usar Gemini real:

- AI_PLANS_MODE=live
- GEMINI_API_KEY: clave del proyecto, guardada como secreto.
- GEMINI_MODEL: identificador de un modelo disponible y compatible con salida JSON estructurada.
- GEMINI_BILLING_CONFIRMED=true: confirmar la facturación del proyecto.
- GEMINI_SCHEMA_FIELD=responseJsonSchema, salvo que la versión del proveedor requiera el modo alternativo ya admitido.
- HEALTH_FEATURES_ENABLED y los consentimientos de salud/IA: los requisitos existentes de disponibilidad se conservan.

No se activó live ni se modificaron secretos o variables de producción. Si la página está en simulated, seguirá sin hacer llamadas a Gemini, pero sus ejemplos nuevos usan el recetario.

Para comprobar conectividad, el comando existente check_gemini usa datos ficticios y realiza una llamada real con costo potencial; no fue ejecutado en esta entrega. Ese comando comprueba el cliente/formato general: la nueva selección de recetas se prueba a través del flujo de alimentación en un entorno de ensayo.

## Uso

1. Crear o revisar la ficha de una persona dentro del alcance de bienestar, con objetivo, medidas, preferencias y tiempos.
2. Pedir un nuevo plan de alimentación desde el flujo habitual.
3. Revisar el borrador del entrenador: recetas, porciones y compatibilidad.
4. Aprobarlo para que aparezca en la cuenta de la clienta.
5. Pedir otra opción usa el mismo recetario y excluye las opciones ya activas.

No hay migraciones nuevas por estos cambios. Conservar las migraciones pendientes de la API Ruffier que ya existían en el trabajo local.

## Verificación

Las pruebas nuevas están en apps/plans/tests/test_wellness_recipes.py: platos internacionales, ingredientes permitidos, restricciones de cocina, independencia de los datos del simulador, rechazo de platos inventados, generación de cinco comidas y pausa del alcance clínico antes de la red y al aprobar/publicar.

Las pruebas anteriores se actualizaron únicamente donde cambió el contrato: versiones del prompt, campos nuevos del payload, pausa de condiciones clínicas, texto de bienestar y cinco tiempos iniciales.

Resultado final: pasaron 184 pruebas de menús, simulador, prompts, vistas de alimentación y formularios
de perfil, incluidas las 8 pruebas nuevas de recetas/bienestar. Se comprobaron pérdida de peso y
ganancia muscular con cinco comidas, así como las restricciones existentes de ingredientes.
Django check, makemigrations --check --dry-run y git diff --check terminaron sin problemas.
Tailwind se recompiló correctamente. No se realizó llamada real a Gemini, prueba visual en navegador,
commit ni despliegue.

