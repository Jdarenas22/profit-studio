/* API del mismo origen. Nunca usar innerHTML con los datos de la evaluación. */
(() => {
  "use strict";
  const fields = ["resting_hr_bpm", "post_exercise_hr_bpm", "recovery_hr_bpm"];
  document.querySelectorAll("[data-cardio-api]").forEach((panel) => {
    const form = panel.querySelector("[data-cardio-form]");
    const status = panel.querySelector("[data-cardio-status]");
    const history = panel.querySelector("[data-cardio-history]");
    const button = form.querySelector("button");
    let token = null;
    let busy = false;

    const showRecord = (record, prepend = false) => {
      const item = document.createElement("li");
      item.textContent = new Date(record.created_at).toLocaleString("es-CO") +
        " · IR: " + record.ruffier_index + " · IRD: " + record.ruffier_dickson_index +
        " · P0/P1/P2: " + fields.map((name) => record[name]).join("/") + " bpm";
      if (prepend) history.prepend(item);
      else history.append(item);
    };
    const jsonResponse = async (response) => {
      if (!(response.headers.get("Content-Type") || "").includes("application/json")) {
        throw new Error("La sesión o el token de seguridad expiró. Recarga la página.");
      }
      const data = await response.json();
      if (!response.ok) {
        const error = data.error || {};
        const details = Object.values(error.fields || {}).join(" ");
        throw new Error(details || error.message || "No se pudo procesar la solicitud.");
      }
      return data;
    };
    // El token representa un envío; reintentar tras un fallo de red no crea otra fila.
    form.addEventListener("input", () => {
      if (!busy) token = null;
    });
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (busy || !form.reportValidity()) return;
      if (!window.crypto || !window.crypto.randomUUID) {
        status.textContent = "Abre el sitio con HTTPS (o localhost) para registrar la evaluación.";
        return;
      }
      token = token || window.crypto.randomUUID();
      const payload = {request_token: token, protocol_confirmed: form.elements.protocol_confirmed.checked};
      fields.forEach((name) => { payload[name] = Number(form.elements[name].value); });
      busy = true;
      button.disabled = true;
      form.querySelectorAll("input:not([type=hidden])").forEach((input) => { input.disabled = true; });
      status.textContent = "Guardando evaluación…";
      try {
        const response = await fetch(panel.dataset.cardioApi, {
          method: "POST", mode: "same-origin", credentials: "same-origin", cache: "no-store",
          headers: {"Content-Type": "application/json", "X-CSRFToken": form.elements.csrfmiddlewaretoken.value},
          body: JSON.stringify(payload)
        });
        const data = await jsonResponse(response);
        showRecord(data, true);
        token = null;
        form.reset();
        status.textContent = "Evaluación guardada. IR: " + data.ruffier_index +
          " · IRD: " + data.ruffier_dickson_index + ". Requiere interpretación profesional.";
      } catch (error) {
        status.textContent = error.message;
      } finally {
        busy = false;
        button.disabled = false;
        form.querySelectorAll("input:not([type=hidden])").forEach((input) => { input.disabled = false; });
      }
    });
    fetch(panel.dataset.cardioApi, {mode: "same-origin", credentials: "same-origin", cache: "no-store"})
      .then(jsonResponse)
      .then((data) => {
        data.results.forEach((record) => showRecord(record));
        status.textContent = data.results.length ? "Últimas evaluaciones de esta valoración." : "Aún no hay evaluaciones nuevas.";
      })
      .catch((error) => { status.textContent = error.message; });
  });
})();

