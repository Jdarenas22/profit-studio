# Archivo: documentación de la versión 1.0 (mayo de 2026)

Estos documentos describen ProFit Studio **tal como era en la versión 1.0**, antes de la ficha de salud, el consentimiento y los planes con IA. Se conservan como instantánea histórica y **no se mantienen al día**.

| Archivo | Qué es |
| --- | --- |
| `PRODUCT_DOCUMENTATION.md` | Documentación de producto para la persona dueña del producto (metodología ágil), versión 1.0 |
| `ProFitStudio_Documentacion_Agile_PO.docx` | Versión en Word de la documentación de producto, generada con `generate_docs.py` |
| `ProFitStudio_Guia_Tecnica_Desarrolladores.docx` | Guía técnica para desarrolladores, versión 1.0, generada con `generate_docs.py` |
| `generate_docs.py` | Script que produjo los dos `.docx`. Necesita `python-docx`, que **no** está en `requirements`, y escribe sus salidas en `docs/` (no en esta carpeta) |

## Documentación vigente

- `DOCUMENTACION.md` (raíz del proyecto): guía técnica completa y al día, incluida la sección 19 (salud y planes con IA).
- `docs/DEPLOY.md`: despliegue en Railway.
- `docs/contracts/`: contratos de diseño de la ficha de salud y los planes con IA (`plan-ia.md` y `plan-ia-adaptacion.md`) y los pedidos entre backend y frontend (`requests.md`).
- `CLAUDE.md` (raíz): resumen de la arquitectura y las reglas del proyecto.
