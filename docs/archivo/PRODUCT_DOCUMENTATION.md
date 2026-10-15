# ProFit Studio — Documentación de Producto
### Metodología Ágil · Entrega para Product Owner
**Versión:** 1.0 · **Fecha:** Mayo 2026 · **Estado:** Producción activa

---

## 1. Visión del Producto

**Para** gimnasios y entrenadores personales que gestionan clientes de forma manual (WhatsApp, Excel),
**ProFit Studio** es una plataforma web de gestión integral
**que** centraliza clientes, membresías, rutinas, valoraciones físicas y pagos en un solo lugar.

**A diferencia de** herramientas genéricas como Google Sheets o apps de fitness masivas,
**nuestro producto** está personalizado para el flujo de trabajo real de la entrenadora: desde el registro del cliente hasta su rutina diaria con videos de YouTube.

---

## 2. Usuarios del Sistema (Roles)

| Actor | Descripción | Acceso |
| --- | --- | --- |
| **Yiseth (Superusuaria)** | Dueña y entrenadora principal. Gestiona todo el sistema. | Panel completo: clientes, pagos, entrenadores, configuración |
| **Entrenador/a** | Staff contratado. Gestiona solo sus clientes asignados. | Panel parcial: sin pagos, sin gestión de otros trainers |
| **Cliente (Miembro)** | Usuario final del gimnasio. | Dashboard personal: rutinas, valoraciones, pagos propios |
| **Visitante** | Persona que llega desde redes sociales o búsqueda. | Página pública: servicios, planes, contacto por WhatsApp |

---

## 3. Épicas y Estado Actual

### ÉPICA 1 — Presencia Web Pública ✅ Completada

Objetivo: Convertir visitantes en clientes potenciales.

| Historia de Usuario | Criterio de Aceptación | Estado |
| --- | --- | --- |
| Como visitante, quiero ver los servicios del gimnasio para evaluar si me conviene. | Página home con hero, servicios, entrenadores y testimonios. | ✅ |
| Como visitante, quiero conocer al equipo de entrenadores y su experiencia. | Sección "Nosotros" con foto, nombre y bio de cada trainer. | ✅ |
| Como visitante, quiero ver los planes y precios para tomar una decisión. | Página de planes con precios en COP y botón de contratación. | ✅ |
| Como visitante, quiero contactar fácilmente al gimnasio. | Botón WhatsApp flotante en todas las páginas + página de contacto. | ✅ |
| Como visitante, quiero registrarme yo mismo sin necesidad de ir presencialmente. | Formulario de registro público con selección de plan de interés. | ✅ |

---

### ÉPICA 2 — Gestión de Clientes y Membresías ✅ Completada

Objetivo: Reemplazar el seguimiento en WhatsApp/Excel por una herramienta digital.

| Historia de Usuario | Criterio de Aceptación | Estado |
| --- | --- | --- |
| Como entrenadora, quiero agregar un cliente nuevo desde el panel. | Formulario con datos personales y activación de membresía opcional. | ✅ |
| Como entrenadora, quiero activar o renovar la membresía de un cliente. | Vista de gestión de membresía con fecha inicio/fin calculada automáticamente. | ✅ |
| Como entrenadora, quiero saber qué membresías vencen en los próximos 7 días. | Dashboard con lista de membresías próximas a vencer. | ✅ |
| Como cliente, quiero ver el estado de mi membresía desde mi panel. | Dashboard del miembro con días restantes y estado de la membresía. | ✅ |
| Como cliente sin membresía vigente, quiero saber que mi acceso está limitado. | Página de aviso de membresía expirada con enlace a WhatsApp. | ✅ |

---

### ÉPICA 3 — Rutinas de Entrenamiento ✅ Completada

Objetivo: Dar a cada cliente una rutina personalizada accesible desde el celular.

| Historia de Usuario | Criterio de Aceptación | Estado |
| --- | --- | --- |
| Como entrenadora, quiero crear rutinas con múltiples días y ejercicios. | Builder visual con días arrastrables, series, reps y descanso por ejercicio. | ✅ |
| Como entrenadora, quiero asignar una rutina a un cliente específico. | Las rutinas se crean directamente para un usuario y aparecen en su panel. | ✅ |
| Como cliente, quiero ver mi rutina con la imagen y video de cada ejercicio. | Vista de rutina con thumbnail de imagen; clic en imagen abre YouTube. | ✅ |
| Como cliente, quiero registrar cuando completé un día de entrenamiento. | Botón "Completé este día" que guarda un RoutineDayLog. | ✅ |

---

### ÉPICA 4 — Banco de Ejercicios ✅ Completada

Objetivo: Centralizar el contenido de entrenamiento reutilizable entre rutinas.

| Historia de Usuario | Criterio de Aceptación | Estado |
| --- | --- | --- |
| Como entrenadora, quiero crear ejercicios con descripción, músculos y video. | Formulario con campos de texto, imagen y URL de YouTube (no requiere subir video). | ✅ |
| Como entrenadora, quiero organizar ejercicios por categoría muscular. | Filtro por categoría en la lista de ejercicios (12 categorías precargadas). | ✅ |
| Como cliente, quiero ver el video de un ejercicio haciendo clic en su imagen. | Imagen con overlay de play que abre YouTube en pestaña nueva. | ✅ |

---

### ÉPICA 5 — Valoraciones Físicas y Mediciones ✅ Completada

Objetivo: Tener un historial científico del progreso de cada cliente.

| Historia de Usuario | Criterio de Aceptación | Estado |
| --- | --- | --- |
| Como entrenadora, quiero registrar una valoración inicial con datos físicos. | Formulario con peso, talla, edad y objetivos. IMC calculado automáticamente. | ✅ |
| Como entrenadora, quiero aplicar el Test de Ruffier-Dickson para evaluar condición cardíaca. | Campos P0/P1/P2, índice IRD calculado automáticamente con clasificación. | ✅ |
| Como entrenadora, quiero registrar mediciones periódicas de peso y medidas. | Historial de mediciones con peso, cintura e IMC en el perfil del cliente. | ✅ |

---

### ÉPICA 6 — Pagos ✅ Completada

Objetivo: Tener trazabilidad de todos los ingresos del gimnasio.

| Historia de Usuario | Criterio de Aceptación | Estado |
| --- | --- | --- |
| Como cliente, quiero pagar mi membresía en línea con tarjeta, PSE o Nequi. | Checkout con Wompi; webhook activa membresía automáticamente al aprobar pago. | ✅ |
| Como entrenadora, quiero registrar pagos en efectivo o transferencia. | Formulario de pago manual con método, monto, fecha y comprobante opcional. | ✅ |
| Como Yiseth, quiero ver el historial completo de pagos. | Lista de pagos manuales con filtro por cliente (solo visible a superusuaria). | ✅ |

---

### ÉPICA 7 — Panel de Entrenadores y Equipo ✅ Completada

Objetivo: Escalar el modelo de negocio con múltiples entrenadores.

| Historia de Usuario | Criterio de Aceptación | Estado |
| --- | --- | --- |
| Como Yiseth, quiero crear cuentas para nuevos entrenadores. | Formulario de alta de trainer con datos y contraseña inicial. | ✅ |
| Como Yiseth, quiero asignar clientes a cada entrenador. | Vista de asignación con lista de trainers y conteo de clientes por trainer. | ✅ |
| Como Yiseth, quiero ver qué clientes no tienen entrenador asignado. | Alerta en dashboard y badge amarillo "Sin entrenador" en lista de clientes. | ✅ |
| Como Yiseth, quiero actualizar la foto y bio de cada entrenador para la web. | Edición de perfil desde panel de entrenadores; foto aparece en sección Nosotros. | ✅ |
| Como entrenador, quiero actualizar mi propio perfil y foto. | Botón "Mi perfil" en el nav del panel trainer; edición de bio y foto. | ✅ |

---

## 4. Arquitectura Técnica (Resumen Ejecutivo)

```
Visitante/Cliente  →  Web pública (Railway HTTPS)
                          ↓
                   Django 5.0.6 (Python)
                   ├── 7 módulos funcionales
                   ├── Base de datos: PostgreSQL (Railway)
                   ├── Imágenes/archivos: Cloudflare R2
                   ├── Pagos online: Wompi (Colombia)
                   └── Despliegue: automático desde GitHub
```

**Stack tecnológico:**

| Capa | Tecnología |
| --- | --- |
| Backend | Django 5.0.6, Python |
| Base de datos | PostgreSQL (Railway) |
| Almacenamiento media | Cloudflare R2 (S3-compatible) |
| Frontend | Tailwind CSS + Alpine.js |
| Pagos | Wompi (tarjeta, PSE, Nequi, Daviplata) |
| Seguridad | django-axes (límite de intentos de login) |
| Hosting | Railway (CD automático desde GitHub) |

---

## 5. Métricas del Producto (KPIs sugeridos)

| KPI | Cómo medirlo | Meta inicial |
| --- | --- | --- |
| Clientes activos | Membresías con `is_valid=True` | Crecimiento mensual |
| Tasa de renovación | Membresías renovadas / vencidas | > 70% |
| Tiempo de respuesta al cliente nuevo | Desde registro hasta asignación de trainer | < 24 horas |
| Ejercicios con video | Ejercicios con `video_url` / total | > 80% |
| Pagos digitales vs. manuales | `Payment` aprobados / `ManualPayment` | Tendencia online |

---

## 6. Backlog Priorizado (Próximas iteraciones)

### Prioridad Alta

| ID | Historia | Valor de negocio |
| --- | --- | --- |
| B-01 | Como cliente, quiero recibir una notificación (email/WhatsApp) cuando mi membresía esté por vencer. | Reduce pérdida de clientes por olvido de renovación. |
| B-02 | Como entrenadora, quiero exportar el listado de clientes a Excel/PDF. | Reduce dependencia de herramientas externas para reportes. |
| B-03 | Como cliente, quiero ver mi progreso de peso en una gráfica. | Aumenta engagement y percepción de valor del servicio. |

### Prioridad Media

| ID | Historia | Valor de negocio |
| --- | --- | --- |
| B-04 | Como visitante, quiero pagar la membresía directamente desde la web sin registrarme primero. | Reduce fricción en la conversión de leads. |
| B-05 | Como entrenadora, quiero enviar un mensaje grupal por WhatsApp a clientes con membresía próxima a vencer. | Automatiza la gestión de retención. |
| B-06 | Como cliente, quiero calificar mis sesiones de entrenamiento. | Feedback para la entrenadora sin reuniones. |

### Prioridad Baja / Deuda técnica

| ID | Descripción | Impacto |
| --- | --- | --- |
| T-01 | Configurar Cloudflare R2 para almacenamiento permanente de imágenes. | Las fotos de perfil se pierden en cada deploy sin R2. |
| T-02 | Agregar tests automatizados (pytest-django) al pipeline de CI. | Previene regresiones al agregar nuevas funciones. |
| T-03 | Implementar paginación en listas de clientes y ejercicios. | Necesario cuando superen ~100 registros. |

---

## 7. Definition of Done (DoD)

Una historia se considera **terminada** cuando:

- [ ] La funcionalidad está implementada y funciona en producción (Railway)
- [ ] Los datos del negocio (nombres, precios, contacto) son reales, no placeholders
- [ ] El diseño es coherente con el sistema visual (dark theme, color naranja `#FF6B00`)
- [ ] El acceso está protegido según el rol del usuario (visitante / miembro / trainer / superusuaria)
- [ ] Los datos sensibles (contraseñas, claves API) están en variables de entorno, no en el código
- [ ] El cambio está en el repositorio GitHub y desplegado automáticamente

---

## 8. Riesgos Identificados

| Riesgo | Probabilidad | Impacto | Mitigación |
| --- | --- | --- | --- |
| Imágenes se pierden sin R2 configurado | Alta | Medio | Configurar R2 (guía disponible) |
| Un deploy fallido deja la app sin trainer superusuario | Baja | Alto | `create_trainer` en `start.sh` es idempotente |
| Credenciales Wompi expuestas | Baja | Crítico | Variables de entorno en Railway; nunca en código |
| Crecimiento de clientes satura SQLite | N/A | N/A | Ya usa PostgreSQL desde el inicio |

---

## 9. Contacto del Equipo

| Rol | Responsabilidad |
| --- | --- |
| Product Owner | Yiseth Misas García (ProFit Studio) |
| Desarrollo | Claude Code + ProFit Studio Team |
| Repositorio | `github.com/Jdarenas22/profit-studio` |
| Producción | `railway.app` (deploy automático desde `main`) |
