# -*- coding: utf-8 -*-
from __future__ import annotations

import io
import json
import os
import random
import re
import threading
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, parse_qsl, quote, quote_plus, unquote, unquote_plus, urlencode, urljoin, urlparse, urlunparse

import requests

try:
    import xmltodict
except Exception:  # pragma: no cover
    xmltodict = None

BASE_DIR = Path(__file__).resolve().parents[2]
ASSETS_DIR = BASE_DIR / "assets"
DATA_DIR = BASE_DIR / "data"
CACHE_DIR = DATA_DIR / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
profileDir = str(DATA_DIR)
imagesDir = str(ASSETS_DIR)
epgFile = str(DATA_DIR / "epg.json")
seriesFile = str(DATA_DIR / "series.json")
seriesUrl = "https://raw.githubusercontent.com/Fishenzon/repo/master/zips/plugin.video.idanplus/series.json.zip"
channelsLogosDir = str(DATA_DIR / "logos" / "channels")
Path(channelsLogosDir).mkdir(parents=True, exist_ok=True)
youtubePlugin = "https://www.youtube.com/watch?v"
AddonID = "plugin.video.idanplus"
AddonVer = "3.9.9"
AddonName = "Idan Plus"
icon = str(ASSETS_DIR / "icon.png")

STRINGS = {
    30001: "מיון",
    30002: "ברירת מחדל",
    30003: "א-ב",
    30004: "שנה סדר",
    30005: "בחר איכות",
    30011: "עמוד קודם",
    30012: "עמוד הבא",
    30013: "מעבר לעמוד",
    30023: "קבע איכות ברירת מחדל",
    30024: "אוטומטי",
    30602: "כאן",
    30603: "קשת / מאקו",
    30604: "רשת 13",
    30606: "עכשיו 14",
    30607: "כאן חינוכית",
    30608: "ערוץ 24 החדש",
    30630: "ערוץ 9",
    30632: "ספורט 5",
    30643: "i24NEWS",
    30702: "גל\"צ / גלגל\"צ",
    30704: "eco99fm",
    30726: "100FM",
    30734: "89.1FM",
    30900: "ספורט 5",
    31000: "ספורט 1",
}

DEFAULT_SETTINGS = {
    "boldLables": "false",
    "viewModeEpisodes": "Auto",
    "updateChannelsLinksInterval": "1",
    "tv_res": "best",
    "radio_res": "best",
    "kan_res": "best",
    "keshet_res": "best",
    "reshet_res": "best",
    "14tv_res": "best",
    "9tv_res": "best",
    "sport5_res": "best",
    "sport1_res": "best",
    "99fm_res": "best",
    "100fm_res": "best",
    "891fm_res": "best",
    "1064fm_res": "best",
    "i24news_res": "best",
    "kanSortBy": "0",
    "makoSortBy": "0",
    "reshetSortBy": "0",
    "14tvSortBy": "0",
    "sport5SortBy": "0",
    "sport1SortBy": "0",
    "programNameFormat": "0",
    "channelNameFormat": "0",
    "kanPagesPerList": "80",
    "makoShowShortSubtitle": "true",
    "MakoBuildId": "",
    "makoUsername": "",
    "makoPassword": "",
    "saveKanImages": "false",
    "prColor": "none",
    "chColor": "none",
    "timesColor": "none",
}

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
]
_user_agent = random.choice(USER_AGENTS)

_thread_state = threading.local()
_channels_provider = None


class FakeAddon:
    def __init__(self) -> None:
        self.settings = dict(DEFAULT_SETTINGS)

    def getSetting(self, key: str) -> str:
        return str(self.settings.get(key, ""))

    def getSettingBool(self, key: str) -> bool:
        return self.getSetting(key).lower() == "true"

    def getSettingInt(self, key: str) -> int:
        value = self.getSetting(key)
        try:
            return int(value)
        except Exception:
            return 0

    def getSettingString(self, key: str) -> str:
        return self.getSetting(key)

    def setSetting(self, key: str, value: Any) -> None:
        self.settings[key] = str(value)

    def setSettingBool(self, id: str, value: bool) -> None:
        self.settings[id] = "true" if value else "false"

    def setSettingString(self, id: str, value: str) -> None:
        self.settings[id] = value

    def getAddonInfo(self, key: str) -> str:
        mapping = {
            "name": AddonName,
            "icon": icon,
            "path": str(BASE_DIR),
            "profile": profileDir,
            "version": AddonVer,
        }
        return mapping.get(key, "")

    def getLocalizedString(self, key: int) -> str:
        return STRINGS.get(key, str(key))


Addon = FakeAddon()


def set_channels_provider(provider) -> None:
    global _channels_provider
    _channels_provider = provider


def _ctx() -> Dict[str, Any]:
    ctx = getattr(_thread_state, "capture", None)
    if ctx is None:
        ctx = {"items": [], "stream": None}
        _thread_state.capture = ctx
    return ctx


def begin_capture() -> None:
    _thread_state.capture = {"items": [], "stream": None}


def end_capture() -> Dict[str, Any]:
    ctx = _ctx()
    result = {"items": list(ctx["items"]), "stream": ctx["stream"]}
    _thread_state.capture = {"items": [], "stream": None}
    return result


def decode(text, dec, force=False):
    if text is None:
        return ""
    if isinstance(text, bytes):
        return text.decode(dec, errors="ignore")
    if force:
        try:
            return bytearray(text, "utf-8").decode(dec, errors="ignore")
        except Exception:
            return str(text)
    return text


def encode(text, dec):
    return text


def translatePath(path):
    return path


def GetAddon():
    return Addon


def GetAddonSetting(key):
    value = Addon.getSetting(key)
    if key.endswith("_res") and value == "":
        return "best"
    return value


def SetAddonSetting(key, value):
    Addon.setSetting(key, value)


def GetHandle():
    return -1


def GetIconFullPath(icon_name):
    return str(ASSETS_DIR / icon_name)


def GetKodiVer():
    return 21.0


def NewerThanPyVer(ver):
    req = [int(x) for x in ver.split(".")]
    cur = [3, 13, 0]
    return cur >= req


def IsAddonInstalled(addonid):
    return False


def InstallAddon(addonid):
    return None


def IsAddonEnabled(addonid):
    return False


def EnableAddon(addonid):
    return None


def DisableAddon(addonid):
    return None


def GetTextFile(filename):
    try:
        return Path(filename).read_text(encoding="utf-8")
    except Exception:
        return ""


def ReadList(fileName):
    try:
        with io.open(fileName, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def WriteList(filename, data):
    try:
        Path(filename).parent.mkdir(parents=True, exist_ok=True)
        with io.open(filename, "w", encoding="utf-8") as f:
            f.write(uni_code(json.dumps(data, indent=2, ensure_ascii=False)))
        return True
    except Exception:
        return False


def isFileOld(filename, deltaInSec=86400):
    p = Path(filename)
    if not p.exists():
        return True
    return (time.time() - p.stat().st_mtime) > deltaInSec


def GetUserAgent():
    return _user_agent


def GetSession():
    s = requests.Session()
    s.headers.update({"User-Agent": _user_agent})
    return s


def _request(method, url, headers=None, user_data=None, session=None, cookies=None, retries=1, verify=True):
    sess = session or requests.Session()
    req_headers = {"User-Agent": _user_agent, "Accept-Encoding": "gzip"}
    if headers:
        req_headers.update(headers)
    last_exc = None
    for _ in range(max(retries, 1)):
        try:
            if method == "post":
                resp = sess.post(url, data=user_data, headers=req_headers, cookies=cookies, timeout=20, verify=verify)
            else:
                resp = sess.get(url, headers=req_headers, cookies=cookies, timeout=20, verify=verify)
            return resp
        except Exception as exc:
            last_exc = exc
            time.sleep(0.2)
    raise last_exc  # type: ignore[misc]


def OpenURL(url, headers=None, user_data=None, session=None, cookies=None, retries=1, responseMethod="text", verify=True):
    try:
        method = "post" if user_data is not None else "get"
        resp = _request(method, url, headers=headers, user_data=user_data, session=session, cookies=cookies, retries=retries, verify=verify)
        resp.raise_for_status()
        if responseMethod == "json":
            return resp.json()
        if responseMethod == "content":
            return resp.content
        if responseMethod == "full":
            return resp
        return resp.text
    except Exception:
        return None if responseMethod in {"json", "full"} else ""


def GetRedirect(url, headers=None):
    try:
        resp = requests.head(url, headers=headers or {}, allow_redirects=False, timeout=10)
        if resp.status_code in {301, 302, 303, 307, 308}:
            return resp.headers.get("location")
        if 400 <= resp.status_code < 500:
            return None
        return url
    except Exception:
        return url


def _strip_kodi_tags(text: str) -> str:
    return re.sub(r"\[/?(?:COLOR|B).*?\]", "", text or "").strip()


def addDir(name, url, mode, iconimage='DefaultFolder.png', infos=None, contextMenu=None, module='', moreData='', totalItems=None, isFolder=True, isPlayable=False, addFav=True, urlParamsData=None):
    ctx = _ctx()
    plot = ""
    if infos:
        plot = infos.get("plot") or infos.get("Plot") or infos.get("title") or infos.get("Title") or ""
    ctx["items"].append({
        "name": _strip_kodi_tags(name),
        "raw_name": name,
        "url": url,
        "mode": int(mode),
        "icon": iconimage,
        "module": module,
        "moreData": moreData,
        "isFolder": bool(isFolder),
        "isPlayable": bool(isPlayable),
        "description": _strip_kodi_tags(plot),
        "infos": infos or {},
        "urlParamsData": urlParamsData or {},
    })


def DelFile(aFile):
    try:
        Path(aFile).unlink(missing_ok=True)
    except Exception:
        pass


def DelCookies():
    return None


def GetStreams(url, headers=None, user_data=None, session=None, retries=1, quality='best'):
    if quality and str(quality).startswith('set'):
        quality = 'best'
    text = OpenURL(url, headers=headers or {}, user_data=user_data, session=session, retries=retries)
    if text in (None, ""):
        return url
    base = urlparse(url)
    baseUrl = f"{base.scheme}://{base.netloc}{base.path}"
    variants = [x for x in re.compile(r'^#EXT-X-STREAM-INF:.*?BANDWIDTH=(\d+)(.*?)\n(.*?)$', re.M).findall(text)]
    if not variants:
        return url
    variants = sorted(variants, key=lambda item: int(item[0]), reverse=True)
    if quality in {'best', 'choose', 'auto', ''}:
        link = variants[0][2].strip()
    else:
        try:
            wanted = int(str(quality))
        except Exception:
            wanted = 10**12
        link = variants[0][2].strip()
        best_match = None
        for variant in variants:
            bw = int(variant[0])
            if bw <= wanted:
                best_match = variant[2].strip()
                break
        if best_match:
            link = best_match
    if not link.startswith('http'):
        link = urljoin(baseUrl, link)
    if base.query and base.query not in link:
        link = f"{link}?{base.query}"
    return link


def _parse_pipe_url(url: str) -> Dict[str, Any]:
    if '|' not in url:
        return {"url": url, "headers": {}}
    base, raw_headers = url.split('|', 1)
    parsed = parse_qs(raw_headers, keep_blank_values=True)
    headers = {k: unquote_plus(v[-1]) for k, v in parsed.items() if v}
    return {"url": base, "headers": headers}


def PlayStream(url, quality='best', name='', iconimage='', adaptive=False):
    ctx = _ctx()
    stream = _parse_pipe_url(url)
    stream["name"] = _strip_kodi_tags(name)
    stream["icon"] = iconimage
    stream["adaptive"] = adaptive
    ctx["stream"] = stream


def GetLocaleString(id):
    return Addon.getLocalizedString(id)


def EscapeXML(text):
    return (text or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;').replace("'", '&#39;')


def UnEscapeXML(st):
    if st is None:
        return ""
    st = st.replace("&hellip;", "").replace("&nbsp;", " ")
    st = st.replace("&amp;", "&").replace("&quot;", '"').replace("&#39;", "'")
    st = st.replace("&lt;", "<").replace("&gt;", ">")
    return st


def XmlToDict(text):
    if xmltodict is None:
        raise RuntimeError("xmltodict not installed")
    return xmltodict.parse(text)


def GetLabelColor(text, keyColor=None, bold=False, color=None):
    return text


def getDisplayName(title, subtitle, programNameFormat, bold=False):
    title = _strip_kodi_tags(title)
    subtitle = _strip_kodi_tags(subtitle)
    if programNameFormat == 1:
        return f"{subtitle} - {title}".strip(" -")
    return f"{title} - {subtitle}".strip(" -")


def GetUnColor(text):
    return _strip_kodi_tags(text)


def GetImageUrl(iconimage):
    return iconimage


def SetViewMode(content):
    return None


def ToggleSortMethod(id, sortBy):
    Addon.setSetting(id, "1" if int(sortBy) == 0 else "0")


def MoveInList(index, step, listFile):
    return None


def GetNumFromUser(title, defaultt=''):
    return None


def GetIndexFromUser(title, listLen):
    return 0


def _remote_cache_path(listFile: str) -> Path:
    path = Path(listFile)
    if path.is_absolute() and str(path).startswith(str(DATA_DIR)):
        return path
    return DATA_DIR / path.name


def GetUpdatedList(listFile, listUrl, headers=None, deltaInSec=86400, isZip=False, sort=False, decode_text=None):
    local_path = _remote_cache_path(listFile)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    if isFileOld(local_path, deltaInSec=deltaInSec):
        try:
            data = OpenURL(listUrl, headers=headers or {}, responseMethod='content')
            if data:
                if decode_text is not None:
                    data = data.decode(decode_text).encode('utf-8')
                if isZip:
                    with zipfile.ZipFile(io.BytesIO(data)) as zf:
                        json_names = [n for n in zf.namelist() if n.lower().endswith('.json')]
                        if json_names:
                            local_path.write_bytes(zf.read(json_names[0]))
                else:
                    local_path.write_bytes(data)
        except Exception:
            pass
    items = ReadList(local_path)
    if sort and isinstance(items, list):
        try:
            return sorted(items, key=lambda item: item.get('name', ''))
        except Exception:
            return items
    return items


def GetDisplayChannels(displayChannelsFile):
    return ReadList(displayChannelsFile)


def GetChannels(type=None, downloadOnly=False):
    if _channels_provider is None:
        channels = {}
    else:
        channels = _channels_provider() or {}
    if type is None:
        return channels
    return [[channel_id, item] for channel_id, item in items(channels) if item.get('type') == type]


def GetChannel(channelID):
    return GetChannels().get(channelID)


def GetChannelLinkDetails(channelID):
    return GetChannels().get(channelID, {}).get('linkDetails')


def SetChannel(channelID, key, value):
    return None


def GetKeyboardText(title='', defaultText=''):
    return None


def GetSourceLocation(title, choiceList):
    return 0


def GetChoice(choiceTitle, fileTitle, urlTitle, choiceFile, choiceUrl, choiceNone=None, fileType=1, fileMask=None, defaultText=None):
    return None


def SaveLogo(logoSource, logoDir, filename, isFromUrl):
    return ''


def GetChannelIconFullPath(channel):
    image = channel.get('my_image') or channel.get('image', '')
    return str(ASSETS_DIR / image) if image else icon


def GetChannelName(channel):
    return channel.get('my_name') or channel.get('name', '')


def GetChannelTvgId(channel):
    return channel.get('my_tvgID') or channel.get('tvgID', '')


def GetChannelAdaptive(channel):
    if channel is None:
        return False
    if channel.get('my_adaptive', '') != '':
        return bool(channel.get('my_adaptive'))
    return bool(channel.get('linkDetails', {}).get('adaptive', False))


def quoteNonASCII(text):
    t = ''
    for ch in str(text):
        t += ch if ord(ch) < 128 else quote(ch)
    return t


def url_parse(text):
    return urlparse(text)


def urlunparse_wrapper(parts):
    return urlunparse(parts)


def uni_code(text):
    return str(text)


def items(d):
    return d.items()


def isnumeric(text):
    return str(text).isnumeric()


def GetKaltura(entryId, partnerId, baseUrl, userAgent, quality='best'):
    headers = {
        'accept': '*/*',
        'accept-language': 'en',
        'content-type': 'application/json',
        'referrer': baseUrl,
        'User-Agent': userAgent,
    }
    payload = json.dumps({
        "1": {"service": "session", "action": "startWidgetSession", "widgetId": f"_{partnerId}"},
        "2": {"service": "baseEntry", "action": "list", "ks": "{1:result:ks}", "filter": {"redirectFromEntryId": entryId}, "responseProfile": {"type": 1, "fields": "id,referenceId,name,description,thumbnailUrl,dataUrl,duration,msDuration,flavorParamsIds,mediaType,type,tags,dvrStatus,externalSourceType,status"}},
        "3": {"service": "baseEntry", "action": "getPlaybackContext", "entryId": "{2:result:objects:0:id}", "ks": "{1:result:ks}", "contextDataParams": {"objectType": "KalturaContextDataParams", "flavorTags": "all"}},
        "4": {"service": "metadata_metadata", "action": "list", "filter": {"objectType": "KalturaMetadataFilter", "objectIdEqual": entryId, "metadataObjectTypeEqual": "1"}, "ks": "{1:result:ks}"},
        "apiVersion": "3.3.0", "format": 1, "ks": "", "clientTag": "html5:v0.56.1", "partnerId": partnerId,
    })
    try:
        response = OpenURL("https://cdnapisec.kaltura.com/api_v3/service/multirequest", headers=headers, user_data=payload, responseMethod='json')
        for source in response[2].get('sources', []):
            if source.get('format') == 'applehttp':
                return GetStreams(source.get('url', ''), quality=quality)
    except Exception:
        pass
    return ''


def GetDailymotion(url):
    return url


def GetYouTube(url):
    if url.endswith('/'):
        url = url[:-1]
    video_id = url[url.rfind('/') + 1:]
    if '?' in video_id:
        video_id = video_id[:video_id.find('?')]
    return f"https://www.youtube.com/watch?v={video_id}"


def GetCF(url, ua=None, retries=10, responseMethod='text'):
    headers = {'User-Agent': ua or _user_agent}
    try:
        import cloudscraper
        scraper = cloudscraper.create_scraper()
        resp = scraper.get(url, headers=headers, timeout=20)
        if responseMethod == 'json':
            return resp.json()
        if responseMethod == 'full':
            return resp
        return resp.text
    except Exception:
        return OpenURL(url, headers=headers, retries=retries, responseMethod=responseMethod)


def GetCFheaders(url, ua=None, retries=10):
    try:
        response = requests.get(url, headers={'User-Agent': ua or _user_agent}, timeout=20)
        return dict(response.headers)
    except Exception:
        return {}


def SaveImage(logoUrl, logoFile):
    try:
        if Path(logoFile).exists():
            return
        data = OpenURL(logoUrl, responseMethod='content')
        if data:
            Path(logoFile).parent.mkdir(parents=True, exist_ok=True)
            Path(logoFile).write_bytes(data)
    except Exception:
        pass


def slugify(value, allow_unicode=False):
    value = str(value)
    if not allow_unicode:
        value = value.encode('ascii', 'ignore').decode('ascii')
    value = re.sub(r'[^\w\s-]', '', value).strip().lower()
    return re.sub(r'[-\s]+', '-', value)

# aliases expected by addon modules
urlunparse = urlunparse_wrapper
