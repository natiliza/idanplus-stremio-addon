INPUT_NUMERIC = 0


class ListItem:
    def __init__(self, label='', path='', iconImage='', thumbnailImage=''):
        self.label = label
        self.path = path
        self.iconImage = iconImage
        self.thumbnailImage = thumbnailImage
        self.art = {}
        self.properties = {}

    def setArt(self, art):
        self.art.update(art)

    def setInfo(self, _type, _info):
        return None

    def setProperty(self, key, value):
        self.properties[key] = value

    def addContextMenuItems(self, items=None):
        return None


class Dialog:
    def select(self, title, items):
        return 0 if items else -1

    def input(self, title, defaultt='', type=0):
        return defaultt

    def browse(self, *_args, **_kwargs):
        return ''
