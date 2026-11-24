# Cierre de APIs y Gemini: demostración universitaria

2026-11-23 · Rama `feature/usda-catalog-precision` · PR #39.

## Entrega comprobada

El catálogo inicial de 90 alimentos tiene 70 referencias USDA identificadas por fuente,
estado de preparación y composición. Los otros 20 permanecen excluidos en el modo estricto
de demo hasta precisar producto, variedad o receta. El registro y las importaciones locales
funcionan sin nuevas llamadas a USDA. Los nutrientes faltantes no se convierten en ceros.

Se integró `main` hasta `1e137c3` conservando sus recetas, variedad, PWA y ajustes recientes.
El único conflicto fue documental; se conservaron ambos pedidos del contrato.
La carpeta principal tiene cambios locales en `chore/operacion-y-worker` y no se modificó
ni se cambió de rama. La prueba usa exclusivamente el worktree USDA y su base ficticia.

## Prueba de extremo a extremo

| Perfil ficticio | Generación | Comidas / opciones | Procedencia completa | Página |
| --- | --- | --- | --- | --- |
| Ganancia muscular | Gemini real (`gemini-3.5-flash-lite`) | 5 / 15 | Sí | HTTP 200 |
| Reducción de grasa | Simulada | 5 / 15 | Sí | HTTP 200 |
| Mantenimiento | Simulada | 5 / 15 | Sí | HTTP 200 |

Se generaron borradores por el servicio habitual, se validaron las reglas de Django y
se aprobaron con el entrenador ficticio. Las versiones previas se conservaron como historial.
En las 45 opciones existe sodio calculable; en 43 hay azúcares totales; en ninguna hay
cobertura de azúcares añadidos. Esto describe esta muestra, no cualquier plan futuro.
La prueba real no usó un reemplazo simulado. Evidencia sin claves ni respuestas privadas:
`docs/auditorias/2026-11-23-usda-demo-resultado.json`.

La comprobación manual tuvo inicialmente un fallo al usar `override_settings` sin conservar
`SETTINGS_MODULE`: el control de entorno bloqueó la operación. Se corrigió el contexto de
comprobación y se verificó HTTP 200 sin debilitar el control de aislamiento.

## Mostrar la demo local

Mientras Claude ocupa la carpeta principal, la vista previa USDA se sirve desde
`C:\Proyectos\profit-studio-usda` en `http://127.0.0.1:8011/demo/`.
Las cuentas son `demo_entrenador`, `demo_grasa`, `demo_musculo` y `demo_mantenimiento`.
Las claves locales permanecen privadas en `.demo/access.local.txt` de ese worktree.
No se comparten ni se sustituyen las claves de la demo de otra carpeta.

Para presentar Gemini, abrir el perfil `demo_musculo` y su plan aprobado; los otros dos
planes están marcados como simulados. Se puede consultar los resultados sin generar de nuevo.
El servidor temporal carga las variables privadas mediante el cargador de entorno habitual,
sin copiar `.env` al worktree ni guardar claves en el repositorio. Al cerrar ese proceso,
la dirección deja de estar disponible; los planes permanecen en la base de muestra.

Tras revisar y fusionar el PR, el inicio normal en la carpeta actualizada sigue siendo:

```powershell
.\scripts\start-demo.ps1
# Solo con la confirmación Free vigente y la clave privada configurada:
.\scripts\start-demo.ps1 -Real -FreeTierConfirmed
```

El script prepara únicamente `.demo`, sincroniza las referencias de forma repetible
y conserva cuentas, credenciales y planes. No copiar bases ni `.env` entre carpetas.

## Instrucciones para Claude

1. Revisar PR #39 y su CI. No aplicar manualmente sus archivos sobre el worker sin guardar
   antes el trabajo de `chore/operacion-y-worker`; integrar por git y resolver solapamientos.
2. Mantener el flag estricto en demo. Conservar los campos de nutrientes/unidades añadidos
   al payload de `jobs.py` al integrar el worker, sin enviar identificadores personales.
3. Conservar la instantánea histórica de cada opción y los datos ausentes como desconocidos.
4. Comprobar después de integrar el worker: generación en cola/en línea según settings,
   transición a borrador, aprobación y consulta de cinco comidas. No probar sobre producción.
5. No habilitar facturación, WhatsApp o pagos ni tratamientos clínicos para esta entrega.

El núcleo USDA/Gemini queda implementado y la muestra real comprobada. La fusión del PR
es la entrega pendiente a revisión; ampliar las 20 referencias es una mejora posterior,
no requiere inventar equivalencias para finalizar la demostración.
