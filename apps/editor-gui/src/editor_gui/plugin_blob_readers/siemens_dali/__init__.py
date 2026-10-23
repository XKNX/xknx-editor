"""Siemens N141_21 DALI DCA port (classic plug-in for N 141/03, N 141/21).

Public entry: ``read(bytes) -> list[PluginSectionView]`` on the submodule itself, used by
``editor_gui.plugin_params`` via its ``blob_reader`` mapping lookup. The schema catalog
``schema.py`` is extracted from the plug-in DLLs (``ImportExportSchema.dll``,
``Parameters.dll``, …).
"""

from editor_gui.plugin_blob_readers.siemens_dali.reader import read, sensor_type_label

__all__ = ("read", "sensor_type_label")
