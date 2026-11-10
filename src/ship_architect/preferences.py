"""Local preferences with one-time migration of existing global settings."""
from PySide6.QtCore import QSettings

def settings(data_dir):
    local=QSettings(str(data_dir/'preferences.ini'),QSettings.Format.IniFormat)
    if not local.value('migrated',False,type=bool):
        old=QSettings('ShipArchitect','ShipArchitect')
        for key in old.allKeys():local.setValue(key,old.value(key))
        local.setValue('migrated',True)
    return local
