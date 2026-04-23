class Addon:
    def __new__(cls, *args, **kwargs):
        from resources.lib.common import Addon as shared_addon
        return shared_addon
