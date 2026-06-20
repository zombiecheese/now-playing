from settings_store import SettingsStore
from singleton_meta import SingletonMeta


class Config(metaclass=SingletonMeta):
    def get_config(self) -> dict:
        return SettingsStore().load_config()
