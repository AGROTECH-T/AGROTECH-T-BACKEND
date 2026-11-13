"""URLconf aislado para la validación desechable de FARMS-01.3.

Vacío a propósito: el entorno aislado no expone rutas HTTP y no debe importar
admin, DRF ni dominios de negocio, que no forman parte del INSTALLED_APPS mínimo.
Sin handlers personalizados, los system checks resuelven los handlers por defecto
de Django.

@author Cristian Deysdayr Jimenez
"""
urlpatterns: list = []
