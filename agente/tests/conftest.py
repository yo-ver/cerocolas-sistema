"""Asignacion automatica de marcadores segun la carpeta de la prueba."""


def pytest_collection_modifyitems(items):
    for item in items:
        ruta = str(item.fspath)
        if "unitarias" in ruta:
            item.add_marker("unitaria")
        elif "integracion" in ruta:
            item.add_marker("integracion")
