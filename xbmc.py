import time

LOGDEBUG = 0
LOGINFO = 1
LOGWARNING = 2
LOGERROR = 3


def log(message, level=LOGINFO):
    return None


def executebuiltin(command, wait=False):
    return None


def sleep(ms):
    time.sleep(ms / 1000.0)


def getInfoLabel(_label):
    return ""


class Keyboard:
    def __init__(self, default='', heading=''):
        self._default = default
        self._heading = heading

    def doModal(self):
        return None

    def isConfirmed(self):
        return False

    def getText(self):
        return self._default


class Player:
    def play(self, url, listitem=None):
        return None
