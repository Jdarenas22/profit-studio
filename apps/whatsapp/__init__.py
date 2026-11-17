"""Avisos de la cuenta por WhatsApp (WhatsApp Business Cloud API de Meta).

Contrato: docs/contracts/whatsapp.md. Avisos de una sola vía con plantillas *utility*; nunca datos
de salud. Capas: `views.py` (HTTP) -> `services.py` / `queue.py` / `webhook.py` (negocio) ->
`models.py` (datos) y `client/` (la API de Meta, o un cliente simulado).
"""
