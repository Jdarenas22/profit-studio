"""Listas cerradas de cuentas (docs/contracts/cuentas-eliminar.md, sección 11.2).

Solo códigos y textos fijos: la auditoría nunca guarda texto libre.
"""
REASON_HOLDER_REQUEST = 'holder_request'
REASON_LEFT = 'left'
REASON_DUPLICATE = 'duplicate'
REASON_SECURITY = 'security'
REASON_OTHER = 'other'

REASON_CHOICES = [
    (REASON_HOLDER_REQUEST, 'La persona pidió eliminar sus datos (derecho de supresión)'),
    (REASON_LEFT, 'Ya no entrena aquí o ya no trabaja aquí'),
    (REASON_DUPLICATE, 'Cuenta repetida o creada por error'),
    (REASON_SECURITY, 'Seguridad (cuenta comprometida o uso indebido)'),
    (REASON_OTHER, 'Otro motivo'),
]
REASON_CODES = tuple(code for code, _label in REASON_CHOICES)

ACTION_DEACTIVATE = 'deactivate'
ACTION_REACTIVATE = 'reactivate'
ACTION_ERASE = 'erase'
# Traspaso de la administración (docs/contracts/transferir-administracion.md): `actor` = quien
# entrega, `target` = quien recibe.
ACTION_TRANSFER_ADMIN = 'transfer_admin'
ACTION_CHOICES = [
    (ACTION_DEACTIVATE, 'Desactivar'),
    (ACTION_REACTIVATE, 'Reactivar'),
    (ACTION_ERASE, 'Eliminar definitivamente'),
    (ACTION_TRANSFER_ADMIN, 'Transferir la administración'),
]

# Filtro de las listas del panel (?status=)
STATUS_ACTIVE = 'active'
STATUS_INACTIVE = 'inactive'
STATUS_ALL = 'all'
STATUS_CHOICES = (STATUS_ACTIVE, STATUS_INACTIVE, STATUS_ALL)

ERASED_LABEL = 'Cuenta eliminada'
