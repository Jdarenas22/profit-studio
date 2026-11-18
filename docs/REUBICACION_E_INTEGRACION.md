# Reubicación e integración nutricional

Fecha: 17 de noviembre de 2026.

## Carpeta de trabajo

La copia principal es `C:\Proyectos\profit-studio`. Abrir esa carpeta en VS Code.
`CLAUDE.md` ya identifica esta ubicación. La copia anterior de OneDrive queda como respaldo.
El entorno virtual y los módulos cargados se verificaron desde la ubicación nueva.

## Comparación y cambios aplicados

La nueva copia ya contenía Ruffier y los planes de alimentación de bienestar, además de cambios posteriores
para Railway. Faltaba la integración con USDA FoodData Central que seguía en la copia anterior.

Se incorporaron 24 archivos de código, plantillas, migración y documentación. Los cambios compartidos en
`.env.example` y `CLAUDE.md` se fusionaron. Se conservaron la configuración de Railway, `Procfile`,
`start.sh` y los ajustes de despliegue existentes; no se restauraron railway.toml ni railway.json.

Lo incorporado permite:

- Buscar alimentos en USDA y previsualizar una importación antes de guardarla.
- Registrar fuente, estado del alimento y nutrientes por 100 g, incluidos azúcares y sodio.
- Mantener los nutrientes no disponibles como desconocidos, sin convertirlos en cero.
- Guardar instantáneas de los ingredientes de cada plan y presentar totales por receta y día.
- Proteger la clave del proveedor y exigir confirmación explícita para importar.

Los alimentos importados quedan inactivos y pendientes de revisión. Importar una tabla nutricional no
certifica alérgenos ni convierte un menú en una dieta adecuada para una condición médica.

No se copiaron `.env`, bases de datos, medios, entornos virtuales ni configuración Git desde la copia anterior.
La base de datos propia de esta ubicación recibió las migraciones plans.0004 y plans.0005, después de
crear un respaldo SQLite fuera del repositorio. Su comprobación de integridad devolvió `ok`.
El respaldo está en la carpeta de artefactos de esta sesión, dentro de
`relocation-integration/private-local-backup`.

## Validación en esta ubicación

- 683 pruebas aprobadas en plans, assessments, check_infra y deploy_config, con avisos de deprecación tratados como errores.
- Django check: sin incidencias. makemigrations --check --dry-run: sin cambios pendientes.
- Compilación de Tailwind completada y git diff --check sin errores.
- Las cinco migraciones de plans están aplicadas en la base de datos local.

Esta validación cubre los módulos indicados; no constituye una auditoría completa de seguridad,
una prueba de penetración ni una validación del despliegue en producción.

## Pendientes y recomendaciones

1. Configurar una clave USDA propia en el `.env` local: FDC_API_KEY está vacía.
2. Seguir [la guía de integración](INTEGRACION_DATOS_NUTRICIONALES.md): buscar, comprobar identidad y estado,
   previsualizar, importar y revisar los alimentos antes de activarlos. No asociar pollo crudo con recetas de pollo cocido.
3. Completar y revisar las fuentes de los ingredientes del catálogo. La incorporación del cliente API no implica
   que todas las recetas tengan ya valores completos y verificados.
4. Configurar y probar Gemini antes de activar generación real: actualmente la IA está en modo `simulated`
   y la configuración live no está completa. Los cálculos deben usar el catálogo verificado de Django;
   Gemini debe proponer combinaciones y recetas sin inventar nutrientes.
5. Probar el recorrido completo en la interfaz con un catálogo revisado y después preparar el despliegue.
   En esta entrega no se hizo commit ni despliegue y no se activaron servicios con claves privadas.

## Prompt para continuar con Claude en VS Code

```text
Trabaja exclusivamente en C:\Proyectos\profit-studio. Lee CLAUDE.md,
docs/REUBICACION_E_INTEGRACION.md y docs/INTEGRACION_DATOS_NUTRICIONALES.md.
Revisa primero git status y conserva los cambios existentes de Railway y de la integración USDA.
No leas ni muestres secretos de .env ni copies datos desde la carpeta de respaldo de OneDrive.

La integración USDA ya está implementada y las migraciones locales están aplicadas.
Verifica su estado actual antes de modificarla. Continúa con la conexión de nutrición:
identifica qué ingredientes del catálogo carecen de fuente o nutrientes completos y prepara
un listado revisable de correspondencias USDA, distinguiendo crudo, cocido y preparado.
No importes ni actives coincidencias dudosas automáticamente.

Revisa el adaptador Gemini existente y el contrato de generación. Los planes deben usar
ingredientes revisados y cálculos deterministas de Django; la IA no debe inventar nutrientes.
Conserva los datos desconocidos como desconocidos y la trazabilidad por plan.
No prometas adecuación clínica ni ausencia de alérgenos basándote solo en una respuesta de IA.
Ejecuta las pruebas pertinentes y documenta los cambios, limitaciones y pasos de configuración.
Si falta una clave, deja la integración preparada sin simular que se probó una conexión real.
No despliegues ni hagas commit sin una instrucción posterior del usuario.
```
