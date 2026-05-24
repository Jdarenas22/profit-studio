---
name: web-scrolling
description: "Genera una web profesional de una sola página con animaciones de scroll, parallax y diseño premium. Usa esta skill siempre que el usuario quiera crear una web, landing page, página de negocio, portfolio, o cualquier sitio web estático. Triggers: 'hazme una web', 'crea una web', 'web para mi negocio', 'landing page', 'necesito una página web', 'web para restaurante/clínica/gimnasio/estudio/peluquería', 'web con efectos', 'web con animaciones', 'web profesional', 'diseño web', 'quiero una web', 'página para mi empresa', 'sitio web'."
---

# Web Premium con Efectos de Scroll

Generas webs de una sola página con animaciones de scroll que parecen hechas por una agencia de diseño. Un único archivo HTML que se abre en el navegador.

**Regla fundamental: no inventes ningún dato del negocio.** Servicios, precios, horarios, contacto, testimonios — todo viene del usuario. Si no te lo ha dado, pregúntaselo.

---

## Paso 1 — Recoger la información REAL del negocio

Pregunta al usuario lo necesario en **2-3 mensajes**, agrupando por bloques. No hagas un interrogatorio de 20 preguntas, pero tampoco te saltes nada.

### Bloque 1 — Lo básico (preguntar siempre si no lo ha dado)

- Nombre del negocio
- Tipo de negocio (restaurante, clínica, gimnasio, peluquería, estudio, etc.)
- Ciudad y dirección (aunque sea aproximada)
- Servicios reales con precios
- Teléfono y email de contacto reales
- Horario de apertura real

### Bloque 2 — Identidad visual

- **Colores de marca** — si no tiene, proponle 2-3 paletas adaptadas a su sector y que elija
- **Eslogan** — si no tiene, proponle 2-3 opciones y que elija
- **Fotos o imágenes** — si tiene, que las ponga en una carpeta `assets/`. Si no tiene, usa placeholders con gradientes CSS y avísale de que debería sustituirlos

### Bloque 3 — Contenido opcional

- **Testimonios reales** — si tiene reseñas de clientes, úsalas. Si no tiene, **omite la sección**. No inventes reseñas.
- **Redes sociales** — URLs reales. Si no tiene, no pongas iconos de redes.
- **Vídeo para el hero** — si tiene un vídeo, se activa el efecto scroll-video (ver más abajo)

### Si el usuario da info parcial

Genera con lo que tienes. Donde falte info, pon **placeholders visibles**: `[TU TELÉFONO AQUÍ]`, `[DIRECCIÓN]`, `[PRECIO]`. Avísale al final qué datos faltan.

---

## Paso 2 — Adaptar el diseño al tipo de negocio

La web de un restaurante japonés debe sentirse completamente diferente a la de un gimnasio. Adapta el tono, la paleta, la tipografía, los nombres de sección y el estilo visual al sector.

### Guía de adaptación

**Restaurante / Bar / Cafetería**
- Tono: cálido, sensorial, evocador
- Sección estrella: "Nuestra Carta" con categorías y precios (no "Servicios")
- Tipografía: serif elegante para títulos, sans-serif para cuerpo
- Detalles: si es un restaurante de una cultura específica (japonés, italiano, mexicano), refleja esa cultura en la tipografía, iconos decorativos y vocabulario
- CTA: "Reserva tu mesa" / "Ver la carta"

**Gimnasio / CrossFit / Centro deportivo**
- Tono: enérgico, directo, motivacional
- Sección estrella: "Planes" con comparativa de tarifas destacando el plan recomendado
- Tipografía: sans-serif potente y bold para títulos
- CTA: "Empieza tu prueba gratis" / "Apúntate hoy"

**Peluquería / Barbería / Centro de estética**
- Tono: elegante, cuidado, personal
- Sección estrella: servicios con precios detallados
- Tipografía: mezcla de serif elegante y sans-serif limpia
- CTA: "Pide tu cita" / "Reserva ahora"

**Clínica / Consulta médica**
- Tono: profesional, confiable, cercano
- Considera usar **tema claro** en vez de oscuro — transmite limpieza
- Sección estrella: especialidades y equipo médico
- CTA: "Pide cita" / "Consulta sin compromiso"

**Estudio creativo / Agencia / Freelance**
- Tono: creativo, moderno, seguro
- Sección estrella: portfolio de trabajos reales
- CTA: "Hablemos de tu proyecto"

**Tienda / Negocio local**
- Tono: cercano, práctico, confiable
- Sección estrella: productos destacados con precios
- CTA: "Visítanos" / "Ver productos"

---

## Paso 3 — Generar la web

Crea **un único archivo HTML** autocontenido con CSS y JS inline. Sin dependencias externas excepto Google Fonts. Debe funcionar abriéndolo directamente en el navegador.

### Principio creativo

Tienes total libertad para diseñar. No te limites a un template genérico — haz que cada web se sienta única y hecha a medida para ese negocio. Elige las fuentes, colores, espaciados, animaciones y detalles decorativos que mejor encajen con el sector y la personalidad del negocio.

Lo que sí debe cumplir siempre:
- **Responsive** (funcionar bien en móvil, tablet y escritorio)
- **Rendimiento** (no cargar librerías externas pesadas)
- **Accesibilidad básica** (contraste legible, textos no demasiado pequeños)

### Idioma

El mismo que usó el usuario.

### Secciones

Incluye estas secciones en este orden. Omite las que no apliquen:

1. **Navegación fija** — Se fija al hacer scroll con fondo semi-transparente y blur. Links a cada sección con scroll suave. Logo si el usuario lo proporcionó.

2. **Hero** — Pantalla completa. Dos variantes:
   - **Estándar (por defecto):** Título impactante, subtítulo, CTA. Diseña el hero para que sea la pieza más llamativa de la web — usa tipografía expresiva, efectos visuales creativos, elementos decorativos que reflejen el sector.
   - **Con vídeo scroll-driven:** Solo si el usuario da un vídeo o pide "efecto Apple". El vídeo avanza fotograma a fotograma con el scroll. Usa `position: sticky` + `height: 500vh` para el contenedor padre, y sincroniza `video.currentTime` con el progreso de scroll vía `requestAnimationFrame`. En móvil: autoplay silenciado. Respeta `prefers-reduced-motion`.

3. **Sobre nosotros** — Texto descriptivo con datos reales. Estadísticas reales (solo las que el usuario proporcionó) con números que se animan contando desde 0 al entrar en viewport.

4. **Servicios / Carta / Planes** — Nombre adaptado al sector. Los servicios REALES del usuario con precios REALES. Diseño en tarjetas o lista según lo que mejor encaje.

5. **Galería** — Solo si el usuario proporcionó imágenes o la pidió. Con fotos: usar rutas relativas a `assets/`. Sin fotos: placeholders con gradientes y aviso al usuario.

6. **Testimonios** — **Solo con testimonios reales del usuario.** Respeta la cantidad exacta que proporcionó (si dio 2, pon 2, no 3). Sin testimonios: omitir la sección.

7. **Contacto + Footer** — Formulario, datos de contacto REALES, redes sociales solo si dio URLs. Copyright con año actual.

### Efectos de scroll obligatorios

Implementa con **Intersection Observer nativo** (sin librerías):

- **Reveal animations** — Los elementos aparecen con transiciones al entrar en viewport (fade, slide, scale — elige las que mejor queden)
- **Stagger** — Los elementos de listas/grids entran escalonados
- **Counter** — Los números de stats se animan contando desde 0
- **Parallax** — Al menos 1 sección o elemento con movimiento diferencial al scroll
- **Hover effects** — Las tarjetas/cards responden al hover con elevación y/o transformación

La implementación concreta es tuya — elige las transiciones, duraciones, easings y efectos que hagan que la web se sienta premium. No te limites a un fade-up genérico si otra animación queda mejor.

### Diseño visual

- **Tema**: oscuro por defecto, pero usa tema claro si el sector lo requiere (clínicas, bodas, etc.)
- **Tipografía**: elige las fuentes de Google Fonts que mejor encajen con el sector — serif para elegancia, sans-serif para modernidad, display para impacto. Usa 2-3 fuentes máximo.
- **Paleta**: los colores del usuario, o los que eligió de tu propuesta
- **Detalles creativos**: scrollbar personalizada, elementos decorativos de fondo, gradientes sutiles, separadores visuales entre secciones — lo que haga falta para que no parezca un template genérico
- **Espaciado generoso** entre secciones
- **Max-width** ~1200px centrado

---

## Paso 4 — Guardar y abrir

- Guarda como `web-[nombre-negocio].html` (kebab-case, sin caracteres especiales)
- Abre automáticamente: `open` (macOS), `start` (Windows), `xdg-open` (Linux)

---

## Paso 5 — Presentar el resultado

Muestra:

1. Nombre del archivo generado
2. Secciones incluidas
3. **Datos que faltan** — lista exacta de placeholders que el usuario debe rellenar
4. Instrucciones para sustituir imágenes placeholder si las hay
5. Pregunta si quiere ajustar algo

No muestres precios sugeridos ni consejos de venta.

---

## Referencia técnica: Efecto Scroll-Video (Hero Variante B)

Este efecto solo se usa cuando el usuario proporciona un vídeo o pide explícitamente "efecto Apple", "vídeo con scroll", "que el vídeo avance con el scroll". El diseño visual del resto de la web sigue siendo libre.

### Cómo funciona

1. El usuario llega a la página — el vídeo está parado en el primer fotograma
2. Al hacer scroll hacia abajo, el vídeo avanza fotograma a fotograma sincronizado
3. Cuando el vídeo termina, el scroll normal de la página continúa
4. En móvil: autoplay silenciado (el scroll-driven no funciona bien en táctil)

### Estructura HTML

```html
<section class="scroll-video-hero" id="scroll-hero">
  <div class="scroll-video-sticky">
    <video id="heroVideo" src="assets/hero.mp4" poster="assets/hero-poster.jpg"
           playsinline muted preload="auto"></video>
    <div class="hero-content">
      <!-- Título, subtítulo, CTA van encima del vídeo con overlay oscuro -->
    </div>
    <div class="scroll-progress-bar">
      <div class="scroll-progress-fill" id="progressFill"></div>
    </div>
  </div>
</section>
```

### CSS necesario

```css
.scroll-video-hero {
  height: 500vh; /* Controla la velocidad: 300vh=rápido, 500vh=medio, 700vh=lento */
  position: relative;
}
.scroll-video-sticky {
  position: sticky;
  top: 0;
  height: 100vh;
  width: 100%;
  overflow: hidden;
}
.scroll-video-sticky video {
  position: absolute;
  top: 0; left: 0;
  width: 100%; height: 100%;
  object-fit: cover;
}
.hero-content {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  z-index: 2;
  background: rgba(0, 0, 0, 0.55);
  color: white;
  text-align: center;
  padding: 0 20px;
}
.scroll-progress-bar {
  position: absolute;
  bottom: 0; left: 0;
  width: 100%; height: 4px;
  background: rgba(255, 255, 255, 0.2);
  z-index: 3;
}
.scroll-progress-fill {
  height: 100%;
  width: 0%;
  transition: width 0.05s linear;
}

/* Móvil: autoplay en vez de scroll-driven */
@media (max-width: 768px) {
  .scroll-video-hero { height: 100vh; }
  .scroll-video-sticky { position: relative; }
}
@media (prefers-reduced-motion: reduce) {
  .scroll-video-hero { height: 100vh; }
}
```

### JavaScript necesario

```javascript
(function() {
  const video = document.getElementById('heroVideo');
  const hero = document.getElementById('scroll-hero');
  const progressFill = document.getElementById('progressFill');
  if (!video || !hero) return;

  const isMobile = window.matchMedia('(max-width: 768px)').matches;
  const prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  if (isMobile || prefersReduced) {
    video.autoplay = true;
    video.loop = true;
    video.play().catch(() => {});
    return;
  }

  video.pause();
  video.currentTime = 0;
  let isReady = false;
  video.addEventListener('loadedmetadata', () => { isReady = true; });
  video.load();

  function updateVideo() {
    if (!isReady || !video.duration) return;
    const rect = hero.getBoundingClientRect();
    const scrolled = -rect.top;
    const scrollable = hero.offsetHeight - window.innerHeight;
    if (scrolled <= 0) { video.currentTime = 0; if (progressFill) progressFill.style.width = '0%'; return; }
    if (scrolled >= scrollable) { video.currentTime = video.duration; if (progressFill) progressFill.style.width = '100%'; return; }
    const progress = scrolled / scrollable;
    video.currentTime = progress * video.duration;
    if (progressFill) progressFill.style.width = (progress * 100) + '%';
  }

  let ticking = false;
  window.addEventListener('scroll', () => {
    if (!ticking) {
      requestAnimationFrame(() => { updateVideo(); ticking = false; });
      ticking = true;
    }
  }, { passive: true });
  window.addEventListener('resize', updateVideo);
})();
```

### Ajuste de velocidad

Si el usuario dice que va demasiado rápido, aumenta el `height` de `.scroll-video-hero`. Si dice que va lento, redúcelo.

### Al implementarlo

Integra estos estilos y scripts dentro del diseño creativo que hayas elegido para la web — adapta los colores de la barra de progreso, el overlay del hero-content y la tipografía al estilo general. El código de arriba es la base funcional, el estilo visual es tuyo.
