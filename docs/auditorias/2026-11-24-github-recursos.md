# GitHub y eficiencia de recursos — 24 de noviembre de 2026

Se revisó el proyecto integrado sobre PR #41. Se conservaron la aplicación única, las APIs reales,
las cuentas existentes y el cambio local de Claude en `apps/plans/tests/test_worker.py`.
No se ejecutaron pruebas sobre la base de usuarios ni se consumieron llamadas de Gemini.

## Resultado comprobado en GitHub

Las últimas ejecuciones de CI de main y PR #41 estaban aprobadas. Los fallos anteriores de
`test_check_gemini_history` pertenecían a commits anteriores a `4e7917e` y estaban corregidos.

Había 332 alertas de Dependabot, pero el SBOM declaraba Django 5.0.6 y Pillow 10.3.0.
Tanto los manifiestos de main como el entorno instalado usaban Django 5.2.19 y Pillow 13.0.0.
Se compararon los 332 rangos vulnerables con estas versiones: ninguna coincidía.

Se generó un snapshot a partir de los manifiestos de main y de las distribuciones realmente
instaladas, comprobando restricciones, extras y dependencias transitivas. GitHub aceptó el
snapshot 126585216 y su SBOM pasó a mostrar las versiones vigentes. Dependabot quedó con **0
alertas abiertas**, sin descartes manuales. Esto describe esa comprobación, no garantiza ausencia
de vulnerabilidades futuras. Snapshot asociado al commit `c2943b303cf3429942085bba0b0e3693f9b65fb2`.

La API de Code Scanning respondió que no había análisis; no equivale a un análisis limpio.
La cuenta de CLI no permitió verificar Secret Scanning con los permisos disponibles. No se
ampliaron permisos de cuenta para estas consultas ni se mostraron valores de secretos.

## Optimizaciones implementadas

| Área | Antes | Después | Límite de la medición |
| --- | --- | --- | --- |
| Repetir 67 candidatas de almuerzo | Mediana de 88,105 ms | 2,188 ms | Cinco repeticiones, mismo catálogo y objetivos, distinto identificador de trabajo; prueba local aislada |
| Primer cálculo de las mismas candidatas | 85,360 ms | 85,849 ms | Una observación por versión; no se afirma mejora de la primera llamada |
| Consulta de seis rutinas, planes y revisores | 16 consultas SQL | 4 consultas SQL | SQLite en memoria; comprobación adicional de coste constante con una y seis rutinas |
| Generación en proceso web | Un trabajo activo por hilo lanzado, sin tope explícito | Máximo dos generaciones activas por proceso | Los hilos que no consiguen cupo terminan sin esperar ni abrir conexión; las peticiones aún pueden crear hilos breves |
| Cola de IA | Lanzamiento individual y recuperación posterior | El mismo hilo activo atiende trabajos pendientes hasta vaciar la cola | Sin servicio adicional ni sondeo cuando no hay trabajos; recuperación de huérfanos conservada |
| CI de una rama con PR abierto | Suite por push y por PR | Una suite por actualización del PR | Main conserva CI por push; aún requiere integrar el cambio de workflow |
| CI al actualizar solo el tablero | Suite completa | Sin suite por ese push documental | Los PR mantienen sus comprobaciones |
| Auditoría de dependencias | base, development y local por separado | development y local | development incluye íntegramente base; se evita una resolución repetida sin quitar cobertura de esos paquetes |

La caché de porciones usa LRU de **256 entradas como máximo**. Añade memoria acotada para ahorrar
CPU; no se presenta como una reducción de RAM. La clave contiene alimentos y composición completos,
rangos y objetivos. Los filtros se vuelven a ejecutar y los resultados se reconstruyen para que no
se compartan listas mutables. Cambios de nutrientes, alimentos u objetivos producen otra clave.
No se guardan modelos Django, fichas, textos personales ni respuestas del proveedor en esa caché.

Se revisaron estáticos: imágenes WebP, CSS compilado (~55,5 kB sin compresión), imágenes con carga
diferida y scripts diferidos ya estaban presentes. No se cambiaron tecnologías ni se añadieron
dependencias de ejecución para estos ajustes. Se conservan cálculo nutricional, validación,
permisos, cuotas y revisión de planes. No se agregaron migraciones ni otra versión de la aplicación.

## Verificación y mantenimiento

Las 81 pruebas específicas de recetas, worker y eficiencia pasaron. Incluyen aislamiento de
resultados mutables, invalidación por composición/objetivos, restricciones de alimentos, consultas
constantes, concurrencia real con dos cupos y liberación de capacidad incluso ante errores.
La suite completa convencional pasó 1.802 pruebas en 444,841 s con cuatro procesos.
Después se incorporó un ejecutor explícito de pruebas y una regresión adicional: 1.803 pruebas
pasaron en 456,130 s en un solo proceso. Son configuraciones de paralelismo diferentes: no se
atribuye esa comparación de tiempo exclusivamente al algoritmo de contraseñas ni al sitio web.
El CI definitivo queda registrado en el tablero.

`config.test_runner.ResourceTestRunner` reduce el trabajo criptográfico de las cuentas ficticias
solo al ejecutar pruebas con `--testrunner=config.test_runner.ResourceTestRunner`. Mantiene los
hashers anteriores para fixtures y restaura la configuración incluso si la suite falla. No cambia
settings normales ni contraseñas existentes; se verificó que sin ese ejecutor sigue usándose
`pbkdf2_sha256`. Esta práctica está documentada por Django para acelerar suites funcionales.

`scripts/audit/dependency_snapshot.py` genera JSON de dependencias sin instalar paquetes, leer `.env`,
publicar datos ni cambiar alertas. Ejecutar con el entorno que tenga instalados los requirements del
commit, desde su repositorio:

```powershell
python scripts/audit/dependency_snapshot.py --sha <SHA_VERIFICADO_DE_MAIN> --ref refs/heads/main > snapshot.json
```

Debe regenerarse el snapshot al actualizar dependencias: GitHub da prioridad a los envíos
manuales sobre el análisis estático. El CI conserva pip-audit para auditar las versiones nuevas,
independientemente del grafo. No incluye credenciales. La herramienta falla si una versión instalada incumple el manifiesto.
El JSON debe revisarse y enviarse mediante el proceso autorizado de GitHub. La revisión automática
rechazó añadir un workflow permanente con `contents: write`; no se incorporó ese workflow ni se
aumentaron los permisos del CI. El envío puntual verificado ya corrigió el estado actual del grafo.

Estas mediciones no establecen un máximo de personas concurrentes ni certifican seguridad absoluta.
Para dimensionar capacidad hacen falta hardware, mezcla de peticiones y objetivos de latencia.
El túnel y la publicación siguen dependiendo del PC y de las cuotas gratuitas vigentes.

Fuentes técnicas oficiales consultadas:

- [Priorización de snapshots y reconocimiento de dependencias de GitHub](https://docs.github.com/en/code-security/concepts/supply-chain-security/dependency-graph-data).
- [API de envío de snapshots](https://docs.github.com/en/rest/dependency-graph/dependency-submission).
- [QuerySets y carga de relaciones en Django 5.2](https://docs.djangoproject.com/en/5.2/ref/models/querysets/).

- [Aceleración de pruebas de Django y hashing exclusivo de pruebas](https://docs.djangoproject.com/en/5.2/topics/testing/overview/#password-hashing).
