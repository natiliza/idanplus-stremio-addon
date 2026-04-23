#!/usr/bin/env python3
from __future__ import annotations

import base64
import hashlib
import importlib
import json
import logging
import os
import re
import sys
import threading
import time
from dataclasses import dataclass, asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import unquote, urlparse

import requests

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from resources.lib import common  # noqa: E402

DATA_DIR = BASE_DIR / "data"
ASSETS_DIR = BASE_DIR / "assets"
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8090"))
ADDON_ID = os.getenv("ADDON_ID", "community.idanplus.live")
ADDON_NAME = os.getenv("ADDON_NAME", "Idan+ Live + VOD")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", f"http://127.0.0.1:{PORT}").rstrip("/")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
CHANNELS_URL = os.getenv(
    "IDANPLUS_CHANNELS_URL",
    "https://raw.githubusercontent.com/Fishenzon/repo/master/zips/plugin.video.idanplus/channels.json",
)
UPDATE_INTERVAL_SEC = int(os.getenv("IDANPLUS_REFRESH_SECONDS", "60"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "15"))
ALLOW_REMOTE_REFRESH = os.getenv("ALLOW_REMOTE_REFRESH", "true").lower() == "true"
DEFAULT_UA = os.getenv(
    "DEFAULT_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
)
MAX_META_VIDEOS = int(os.getenv("MAX_META_VIDEOS", "120"))
MAX_RECURSION_DEPTH = int(os.getenv("MAX_RECURSION_DEPTH", "4"))
CATALOG_CACHE_SEC = int(os.getenv("CATALOG_CACHE_SEC", "1800"))

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("idanplus-stremio")


@dataclass
class ResolvedStream:
    url: str
    request_headers: Dict[str, str]
    description: str
    external_url: Optional[str] = None


@dataclass
class MenuEntry:
    module: str
    name: str
    url: str
    mode: int
    icon: str = ""
    more_data: str = ""
    description: str = ""
    is_folder: bool = True
    is_playable: bool = False
    catalog_id: str = ""

    @classmethod
    def from_dict(cls, item: dict, catalog_id: str = "") -> "MenuEntry":
        return cls(
            module=item.get("module", ""),
            name=item.get("name", ""),
            url=item.get("url", ""),
            mode=int(item.get("mode", -1)),
            icon=item.get("icon", ""),
            more_data=item.get("moreData", ""),
            description=item.get("description", ""),
            is_folder=bool(item.get("isFolder", True)),
            is_playable=bool(item.get("isPlayable", False)),
            catalog_id=catalog_id,
        )


class ChannelsStore:
    def __init__(self, local_path: Path, remote_url: str, update_interval_sec: int = 60) -> None:
        self.local_path = local_path
        self.remote_url = remote_url
        self.update_interval_sec = update_interval_sec
        self._channels: Dict[str, dict] = {}
        self._last_refresh = 0.0
        self._etag: Optional[str] = None
        self._last_modified: Optional[str] = None
        self._lock = threading.Lock()
        self.refresh(force=True)

    def refresh(self, force: bool = False) -> None:
        with self._lock:
            if not force and (time.time() - self._last_refresh) < self.update_interval_sec:
                return
            if not self._channels:
                self._channels = self._read_local()
            if not ALLOW_REMOTE_REFRESH:
                self._last_refresh = time.time()
                return
            headers = {}
            if self._etag:
                headers["If-None-Match"] = self._etag
            if self._last_modified:
                headers["If-Modified-Since"] = self._last_modified
            try:
                resp = requests.get(self.remote_url, headers=headers, timeout=REQUEST_TIMEOUT)
                if resp.status_code == 304:
                    pass
                elif resp.ok:
                    data = resp.json()
                    if isinstance(data, dict) and data:
                        self._channels = data
                        self._etag = resp.headers.get("ETag")
                        self._last_modified = resp.headers.get("Last-Modified")
                        self.local_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                        log.info("channels.json refreshed (%d items)", len(data))
            except Exception as exc:
                log.warning("channels refresh error: %s", exc)
            self._last_refresh = time.time()

    def _read_local(self) -> Dict[str, dict]:
        if not self.local_path.exists():
            return {}
        return json.loads(self.local_path.read_text(encoding="utf-8"))

    def all(self) -> Dict[str, dict]:
        self.refresh()
        with self._lock:
            return dict(self._channels)

    def get(self, channel_id: str) -> Optional[dict]:
        self.refresh()
        with self._lock:
            channel = self._channels.get(channel_id)
            if not channel:
                return None
            result = dict(channel)
            result["channelID"] = channel_id
            return result


STORE = ChannelsStore(DATA_DIR / "channels.json", CHANNELS_URL, UPDATE_INTERVAL_SEC)
common.set_channels_provider(lambda: STORE.all())
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": DEFAULT_UA})
I24_TOKEN: Dict[str, Optional[str]] = {"value": None}


def response_json(handler: BaseHTTPRequestHandler, payload: dict, status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
    handler.end_headers()
    if handler.command != "HEAD":
        handler.wfile.write(body)


def response_bytes(handler: BaseHTTPRequestHandler, data: bytes, content_type: str, status: int = 200) -> None:
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "public, max-age=3600")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    if handler.command != "HEAD":
        handler.wfile.write(data)


def make_tv_id(channel_id: str) -> str:
    return f"idanplus:tv:{channel_id}"


def parse_tv_meta_id(meta_id: str) -> str:
    if not meta_id.startswith("idanplus:tv:"):
        raise ValueError("unsupported id")
    return meta_id.split(":", 2)[2]


def _b64(data: dict) -> str:
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(value: str) -> dict:
    pad = len(value) % 4
    if pad:
        value += "=" * (4 - pad)
    return json.loads(base64.urlsafe_b64decode(value.encode("ascii")).decode("utf-8"))


def make_series_id(entry: MenuEntry) -> str:
    return f"idanplus:series:{_b64(asdict(entry))}"


def parse_series_id(meta_id: str) -> MenuEntry:
    if not meta_id.startswith("idanplus:series:"):
        raise ValueError("unsupported series id")
    return MenuEntry(**_unb64(meta_id.split(":", 2)[2]))


def make_video_id(entry: MenuEntry, season: Optional[int] = None, episode: Optional[int] = None) -> str:
    payload = asdict(entry)
    if season is not None:
        payload["season"] = season
    if episode is not None:
        payload["episode"] = episode
    return f"idanplus:video:{_b64(payload)}"


def parse_video_id(video_id: str) -> Tuple[MenuEntry, Optional[int], Optional[int]]:
    if not video_id.startswith("idanplus:video:"):
        raise ValueError("unsupported video id")
    payload = _unb64(video_id.split(":", 2)[2])
    season = payload.pop("season", None)
    episode = payload.pop("episode", None)
    return MenuEntry(**payload), season, episode


def channel_type_label(kind: str) -> str:
    return "רדיו" if kind == "radio" else "לייב"


def get_channels(kind: Optional[str] = None) -> List[dict]:
    items = []
    for channel_id, channel in STORE.all().items():
        ch = dict(channel)
        ch["channelID"] = channel_id
        if kind and ch.get("type") != kind:
            continue
        if ch.get("index", 0) == 0:
            continue
        items.append(ch)
    items.sort(key=lambda x: (x.get("index", 9999), x.get("name", "")))
    return items


def _asset_or_remote(image: str) -> str:
    if not image:
        return f"{PUBLIC_BASE_URL}/assets/icon.png"
    if image.startswith("http://") or image.startswith("https://"):
        return image
    asset = ASSETS_DIR / image
    if asset.exists():
        return f"{PUBLIC_BASE_URL}/assets/{image}"
    return image


def build_tv_meta_preview(channel: dict) -> dict:
    kind = channel.get("type", "tv")
    poster = _asset_or_remote(channel.get("image", ""))
    description = f"{channel_type_label(kind)} • מודול: {channel.get('module', 'tv')} • מזהה: {channel.get('channelID', '')}"
    return {
        "id": make_tv_id(channel["channelID"]),
        "type": "tv",
        "name": channel.get("name", channel["channelID"]),
        "poster": poster,
        "posterShape": "square",
        "logo": poster,
        "background": poster,
        "genres": [channel_type_label(kind), channel.get("module", "tv")],
        "description": description,
    }


def build_tv_meta(channel: dict) -> dict:
    meta = build_tv_meta_preview(channel)
    meta["releaseInfo"] = "Live"
    meta["behaviorHints"] = {"defaultVideoId": meta["id"]}
    return {"meta": meta}


def extract_by_regex(url: str, regex: str, headers: Dict[str, str]) -> str:
    text = SESSION.get(url, headers=headers, timeout=REQUEST_TIMEOUT).text
    match = re.search(regex, text, flags=re.S)
    if not match:
        raise RuntimeError("regex did not match source page")
    return match.group(1)


def decode_jwt_payload(token: str) -> dict:
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    payload = parts[1]
    pad = len(payload) % 4
    if pad:
        payload += "=" * (4 - pad)
    try:
        return json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
    except Exception:
        return {}


def get_i24_token() -> Optional[str]:
    token = I24_TOKEN.get("value")
    if token:
        payload = decode_jwt_payload(token)
        if payload.get("exp", 0) - int(time.time()) > 60:
            return token
    hardware_id = time.strftime("%Y-%m-%dT%H:%M:%S.000")
    auth_url = f"https://api.i24news.wiztivi.io/authenticate?userName=I24News&hardwareId={hardware_id}&hardwareIdType=browser"
    resp = SESSION.get(auth_url, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    token = resp.json().get("accessToken")
    I24_TOKEN["value"] = token
    return token


def resolve_generic(channel: dict) -> ResolvedStream:
    link_details = channel.get("linkDetails", {})
    headers = {"User-Agent": DEFAULT_UA}
    if link_details.get("referer"):
        headers["Referer"] = link_details["referer"]
    link = link_details.get("link")
    regex = link_details.get("regex")
    if regex and link:
        link = extract_by_regex(link, regex, headers)
    if isinstance(link, str) and link.startswith("//"):
        link = f"https:{link}"
    if not link:
        raise RuntimeError("missing link")
    return ResolvedStream(url=link, request_headers=headers, description=f"generic:{channel.get('module')}")


def resolve_14tv(channel: dict) -> ResolvedStream:
    link_details = channel.get("linkDetails", {})
    link = link_details.get("link")
    headers = {"User-Agent": DEFAULT_UA}
    ch_api = link_details.get("ch")
    if ch_api:
        resp = SESSION.get(ch_api, headers={"x-tenant-id": "channel14", "User-Agent": DEFAULT_UA}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        link = resp.json().get("vod", {}).get("hlsStream") or link
    return ResolvedStream(url=link, request_headers=headers, description="14tv")


def resolve_keshet(channel: dict) -> ResolvedStream:
    link_details = channel.get("linkDetails", {})
    raw_path = link_details.get("link", "")
    if not raw_path:
        raise RuntimeError("missing Keshet path")
    ticket_headers = {"User-Agent": DEFAULT_UA, "Accept": "application/json, text/plain, */*", "Referer": "https://www.mako.co.il/", "Origin": "https://www.mako.co.il"}
    entitlements = f"https://mass.mako.co.il/ClicksStatistics/entitlementsServicesV2.jsp?et=ngt&lp={raw_path}&rv=AKAMAI"
    resp = SESSION.get(entitlements, headers=ticket_headers, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    ticket = None
    tickets = data.get("tickets") or []
    if tickets:
        ticket = tickets[0].get("ticket")
    if not ticket:
        raise RuntimeError(f"Keshet ticket failure: caseId={data.get('caseId')}")
    path_only = raw_path.split("?", 1)[0]
    stream_url = f"https://mako-streaming.akamaized.net{path_only}?{requests.utils.unquote(ticket)}"
    return ResolvedStream(url=stream_url, request_headers={"User-Agent": DEFAULT_UA}, description="keshet")


def resolve_hidabroot(channel: dict) -> ResolvedStream:
    link_details = channel.get("linkDetails", {})
    headers = {"User-Agent": DEFAULT_UA}
    page = SESSION.get(link_details["link"], headers=headers, timeout=REQUEST_TIMEOUT).text
    match = re.search(r'<source\s*src="(.*?)"', page, flags=re.S)
    if not match:
        raise RuntimeError("hidabroot source not found")
    return ResolvedStream(url=match.group(1), request_headers=headers, description="hidabroot")


def resolve_sport5(channel: dict) -> ResolvedStream:
    link_details = channel.get("linkDetails", {})
    headers = {"User-Agent": DEFAULT_UA, "Referer": "https://radio.sport5.co.il"}
    link = link_details.get("link")
    if link_details.get("ch"):
        data_url = f"https://radio.sport5.co.il/data/data.json?v={int(time.time() * 1000)}"
        resp = SESSION.get(data_url, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        node = data.get(link_details["ch"])
        if isinstance(node, str) and node:
            link = node.replace("https://nekot.sport5.co.il:10000?", "")
    return ResolvedStream(url=link, request_headers=headers, description="sport5")


def resolve_i24news(channel: dict) -> ResolvedStream:
    link_details = channel.get("linkDetails", {})
    channel_lang = link_details.get("ch")
    stream_url = link_details.get("link")
    token = get_i24_token()
    headers = {"User-Agent": DEFAULT_UA}
    if token:
        api_headers = {"Accept": "application/json", "User-Agent": DEFAULT_UA, "Authorization": f"Bearer {token}"}
        media_url = f"https://api.i24news.wiztivi.io/contents/brightcove/channels/{channel_lang}"
        resp = SESSION.get(media_url, headers=api_headers, timeout=REQUEST_TIMEOUT)
        if resp.ok:
            stream_url = resp.json().get("url") or stream_url
    return ResolvedStream(url=stream_url, request_headers=headers, description=f"i24news:{channel_lang}")


def resolve_glz(channel: dict) -> ResolvedStream:
    link_details = channel.get("linkDetails", {})
    headers = {"User-Agent": DEFAULT_UA}
    link = link_details.get("live") or link_details.get("link")
    root_id = link_details.get("rootId")
    if root_id:
        api_url = f"https://glz.co.il/umbraco/api/player/getplayerdata?rootId={root_id}"
        resp = SESSION.get(api_url, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        live = resp.json().get("liveBroadcast", {})
        link = live.get("fileUrl") or link
    return ResolvedStream(url=link, request_headers=headers, description="glz")


def resolve_100fm(channel: dict) -> ResolvedStream:
    link_details = channel.get("linkDetails", {})
    headers = {"User-Agent": DEFAULT_UA}
    link = link_details.get("link")
    ch_api = link_details.get("ch")
    if ch_api:
        resp = SESSION.get(ch_api, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        stations = resp.json().get("stations") or []
        if stations:
            link = stations[0].get("audio") or link
    return ResolvedStream(url=link, request_headers=headers, description="100fm")


def resolve_1064fm(channel: dict) -> ResolvedStream:
    link_details = channel.get("linkDetails", {})
    headers = {"User-Agent": DEFAULT_UA}
    link = link_details.get("link")
    ch_page = link_details.get("ch")
    if ch_page:
        text = SESSION.get(ch_page, headers=headers, timeout=REQUEST_TIMEOUT).text
        match = re.search(r'"webapp\\.broadcast_link":"(.*?)"', text)
        if match:
            link = match.group(1).replace("\\u002F", "/")
    return ResolvedStream(url=link, request_headers=headers, description="1064fm")


def resolve_module(channel: dict) -> ResolvedStream:
    module = channel.get("module", "tv")
    if module in {"tv", "radio", "kan", "reshet", "99fm"}:
        return resolve_generic(channel)
    if module == "14tv":
        return resolve_14tv(channel)
    if module == "keshet":
        return resolve_keshet(channel)
    if module == "hidabroot":
        return resolve_hidabroot(channel)
    if module == "sport5":
        return resolve_sport5(channel)
    if module == "i24news":
        return resolve_i24news(channel)
    if module == "glz":
        return resolve_glz(channel)
    if module == "100fm":
        return resolve_100fm(channel)
    if module == "1064fm":
        return resolve_1064fm(channel)
    return resolve_generic(channel)


def build_tv_streams(channel: dict) -> dict:
    try:
        resolved = resolve_module(channel)
    except Exception as exc:
        log.exception("stream resolve failed for %s", channel.get("channelID"))
        return {"streams": [{"name": "Resolution error", "description": str(exc), "externalUrl": channel.get("linkDetails", {}).get("referer") or channel.get("linkDetails", {}).get("link") or PUBLIC_BASE_URL}]}
    stream_obj = {"name": channel.get("name", channel.get("channelID", "Idan+")), "description": resolved.description}
    if resolved.external_url:
        stream_obj["externalUrl"] = resolved.external_url
    else:
        stream_obj["url"] = resolved.url
        stream_obj["behaviorHints"] = {"notWebReady": True}
        if resolved.request_headers:
            stream_obj["behaviorHints"]["proxyHeaders"] = {"request": resolved.request_headers}
    return {"streams": [stream_obj]}


ROOT_CATALOGS: Dict[str, Dict[str, str]] = {
    "idanplus_live": {"type": "tv", "name": "Idan+ Live TV"},
    "idanplus_radio": {"type": "tv", "name": "Idan+ Radio"},
    "idanplus_vod_kan": {"type": "series", "name": "VOD - כאן"},
    "idanplus_vod_keshet": {"type": "series", "name": "VOD - קשת / מאקו"},
    "idanplus_vod_reshet": {"type": "series", "name": "VOD - רשת 13"},
    "idanplus_vod_14tv": {"type": "series", "name": "VOD - עכשיו 14"},
    "idanplus_vod_kankids": {"type": "series", "name": "VOD - כאן חינוכית"},
    "idanplus_vod_kan_archive": {"type": "series", "name": "VOD - כאן ארכיון"},
    "idanplus_vod_24": {"type": "series", "name": "VOD - ערוץ 24 החדש"},
    "idanplus_vod_i24": {"type": "series", "name": "VOD - i24NEWS"},
    "idanplus_vod_9": {"type": "series", "name": "VOD - ערוץ 9"},
    "idanplus_vod_sport5": {"type": "series", "name": "VOD - ספורט 5"},
    "idanplus_vod_sport1": {"type": "series", "name": "VOD - ספורט 1"},
    "idanplus_radio_kan": {"type": "series", "name": "תכניות רדיו - כאן"},
    "idanplus_radio_sport5": {"type": "series", "name": "תכניות רדיו - ספורט 5"},
    "idanplus_radio_891": {"type": "series", "name": "תכניות רדיו - 89.1FM"},
    "idanplus_radio_1064": {"type": "series", "name": "תכניות רדיו - 106.4FM"},
    "idanplus_podcast_kan": {"type": "series", "name": "פודקאסטים - כאן"},
    "idanplus_podcast_kan_kids": {"type": "series", "name": "פודקאסטים לילדים - כאן"},
    "idanplus_podcast_sport5": {"type": "series", "name": "פודקאסטים - ספורט 5"},
    "idanplus_music_glglz": {"type": "series", "name": "מוזיקה - גלגל\"צ"},
    "idanplus_music_99": {"type": "series", "name": "מוזיקה - eco99fm"},
    "idanplus_music_100": {"type": "series", "name": "מוזיקה - 100FM"},
    "idanplus_search": {"type": "series", "name": "חיפוש עידן+"},
}

CATALOG_ROOTS: Dict[str, MenuEntry] = {
    "idanplus_vod_kan": MenuEntry(module="kan", name="כאן", url="https://www.kan.org.il/lobby/kan11", mode=1, icon="kan.jpg", more_data="כאן__4444", catalog_id="idanplus_vod_kan"),
    "idanplus_vod_keshet": MenuEntry(module="keshet", name="קשת / מאקו", url="https://www.mako.co.il/mako-vod-index", mode=0, icon="mako.png", catalog_id="idanplus_vod_keshet"),
    "idanplus_vod_reshet": MenuEntry(module="reshet", name="רשת 13", url="https://13tv.co.il/allshows/screen/1170108/", mode=0, icon="13.jpg", catalog_id="idanplus_vod_reshet"),
    "idanplus_vod_14tv": MenuEntry(module="14tv", name="עכשיו 14", url="", mode=0, icon="14tv.png", catalog_id="idanplus_vod_14tv"),
    "idanplus_vod_kankids": MenuEntry(module="kan", name="כאן חינוכית", url="https://www.kankids.org.il", mode=5, icon="23tv.jpg", more_data="כאן חינוכית", catalog_id="idanplus_vod_kankids"),
    "idanplus_vod_kan_archive": MenuEntry(module="kan", name="כאן ארכיון", url="https://www.kan.org.il/lobby/archive/", mode=41, icon="kan.jpg", more_data="כאן ארכיון", catalog_id="idanplus_vod_kan_archive"),
    "idanplus_vod_24": MenuEntry(module="keshet", name="ערוץ 24 החדש", url="https://www.mako.co.il/mako-vod-index?filter=provider&vcmId=3377c13070733210VgnVCM2000002a0c10acRCRD", mode=1, icon="24telad.png", catalog_id="idanplus_vod_24"),
    "idanplus_vod_i24": MenuEntry(module="i24news", name="i24NEWS", url="", mode=-1, icon="i24news.png", catalog_id="idanplus_vod_i24"),
    "idanplus_vod_9": MenuEntry(module="9tv", name="ערוץ 9", url="", mode=0, icon="9tv.png", catalog_id="idanplus_vod_9"),
    "idanplus_vod_sport5": MenuEntry(module="sport5", name="ספורט 5", url="", mode=0, icon="Sport5.png", catalog_id="idanplus_vod_sport5"),
    "idanplus_vod_sport1": MenuEntry(module="sport1", name="ספורט 1", url="", mode=0, icon="sport1.jpg", catalog_id="idanplus_vod_sport1"),
    "idanplus_radio_kan": MenuEntry(module="kan", name="תכניות רדיו - כאן", url="", mode=21, icon="kan.jpg", catalog_id="idanplus_radio_kan"),
    "idanplus_radio_sport5": MenuEntry(module="sport5", name="תכניות רדיו - ספורט 5", url="", mode=20, icon="Sport5.png", catalog_id="idanplus_radio_sport5"),
    "idanplus_radio_891": MenuEntry(module="891fm", name="תכניות רדיו - 89.1FM", url="", mode=0, icon="891fm.png", catalog_id="idanplus_radio_891"),
    "idanplus_radio_1064": MenuEntry(module="1064fm", name="תכניות רדיו - 106.4FM", url="", mode=0, icon="1064fm.jpg", catalog_id="idanplus_radio_1064"),
    "idanplus_podcast_kan": MenuEntry(module="kan", name="פודקאסטים - כאן", url="4451", mode=31, icon="kan.jpg", catalog_id="idanplus_podcast_kan"),
    "idanplus_podcast_kan_kids": MenuEntry(module="kan", name="פודקאסטים לילדים - כאן", url="", mode=33, icon="23tv.jpg", catalog_id="idanplus_podcast_kan_kids"),
    "idanplus_podcast_sport5": MenuEntry(module="sport5", name="פודקאסטים - ספורט 5", url="", mode=20, icon="Sport5.png", catalog_id="idanplus_podcast_sport5"),
    "idanplus_music_glglz": MenuEntry(module="glz", name="מוזיקה - גלגל\"צ", url="rd_glglz", mode=1, icon="glglz.jpg", catalog_id="idanplus_music_glglz"),
    "idanplus_music_99": MenuEntry(module="99fm", name="מוזיקה - eco99fm", url="", mode=0, icon="99fm.png", catalog_id="idanplus_music_99"),
    "idanplus_music_100": MenuEntry(module="100fm", name="מוזיקה - 100FM", url="", mode=0, icon="100fm.jpg", catalog_id="idanplus_music_100"),
}

PLAY_MODES = {2, 3, 4, 5, 10, 11, 23}
SEPARATOR_PREFIXES = ("-------",)
PAGE_NAMES = {"עמוד קודם", "עמוד הבא", "מעבר לעמוד"}


class ModuleBridge:
    def __init__(self) -> None:
        self._loaded: Dict[str, object] = {}
        self._lock = threading.Lock()

    def _load(self, module_name: str):
        with self._lock:
            if module_name not in self._loaded:
                self._loaded[module_name] = importlib.import_module(f"resources.lib.{module_name}")
            return self._loaded[module_name]

    def run(self, entry: MenuEntry) -> Dict[str, object]:
        common.begin_capture()
        try:
            mod = self._load(entry.module)
            mod.Run(entry.name, entry.url, entry.mode, entry.icon, entry.more_data)
            return common.end_capture()
        except Exception as exc:
            log.warning("module bridge failed for %s %s: %s", entry.module, entry.mode, exc)
            return {"items": [], "stream": {"error": str(exc)}}


BRIDGE = ModuleBridge()
_catalog_cache_lock = threading.Lock()
_catalog_cache: Dict[str, Tuple[float, List[MenuEntry]]] = {}


def _normalize_icon(icon_value: str) -> str:
    if not icon_value:
        return "icon.png"
    if icon_value.startswith(str(ASSETS_DIR)):
        return Path(icon_value).name
    return icon_value


def _normalize_item(item: dict, catalog_id: str) -> Optional[MenuEntry]:
    name = item.get("name", "").strip()
    if not name:
        return None
    if name in PAGE_NAMES or name.startswith(SEPARATOR_PREFIXES) or item.get("url") == "toggleSortingMethod" or name.startswith("מיון"):
        return None
    icon = _normalize_icon(item.get("icon", ""))
    mode = int(item.get("mode", -1))
    is_playable = bool(item.get("isPlayable", False)) or mode in PLAY_MODES
    is_folder = bool(item.get("isFolder", True)) and not is_playable
    return MenuEntry(
        module=item.get("module", ""),
        name=name,
        url=item.get("url", ""),
        mode=mode,
        icon=icon,
        more_data=item.get("moreData", ""),
        description=item.get("description", ""),
        is_folder=is_folder,
        is_playable=is_playable,
        catalog_id=catalog_id,
    )


def list_catalog_entries(catalog_id: str) -> List[MenuEntry]:
    now = time.time()
    with _catalog_cache_lock:
        cached = _catalog_cache.get(catalog_id)
        if cached and cached[0] > now:
            return cached[1]
    root_entry = CATALOG_ROOTS[catalog_id]
    result = BRIDGE.run(root_entry)
    entries = []
    for item in result.get("items", []):
        normalized = _normalize_item(item, catalog_id)
        if normalized:
            entries.append(normalized)
    with _catalog_cache_lock:
        _catalog_cache[catalog_id] = (now + CATALOG_CACHE_SEC, entries)
    return entries


def build_series_meta_preview(entry: MenuEntry) -> dict:
    poster = _asset_or_remote(entry.icon)
    genres = []
    if entry.catalog_id:
        genres.append(ROOT_CATALOGS.get(entry.catalog_id, {}).get("name", entry.catalog_id))
    genres.append(entry.module)
    return {
        "id": make_series_id(entry),
        "type": "series",
        "name": entry.name,
        "poster": poster,
        "posterShape": "landscape",
        "logo": poster,
        "background": poster,
        "description": entry.description or entry.name,
        "genres": [g for g in genres if g],
    }


def _extract_season_number(name: str) -> Optional[int]:
    match = re.search(r"(?:עונה|Season)\s*(\d+)", name, flags=re.I)
    if match:
        return int(match.group(1))
    return None


def collect_videos(entry: MenuEntry, depth: int = 0, season_hint: Optional[int] = None, visited: Optional[Set[str]] = None, videos: Optional[List[dict]] = None) -> List[dict]:
    if visited is None:
        visited = set()
    if videos is None:
        videos = []
    if len(videos) >= MAX_META_VIDEOS or depth > MAX_RECURSION_DEPTH:
        return videos
    key = hashlib.sha1(f"{entry.module}|{entry.mode}|{entry.url}|{entry.more_data}".encode("utf-8")).hexdigest()
    if key in visited:
        return videos
    visited.add(key)

    if entry.is_playable:
        season = season_hint
        episode = len([v for v in videos if v.get("season") == season]) + 1 if season else len(videos) + 1
        video = {
            "id": make_video_id(entry, season=season, episode=episode),
            "title": entry.name,
            "overview": entry.description or entry.name,
            "thumbnail": _asset_or_remote(entry.icon),
        }
        if season is not None:
            video["season"] = season
            video["episode"] = episode
        videos.append(video)
        return videos

    result = BRIDGE.run(entry)
    children = [c for c in (_normalize_item(item, entry.catalog_id) for item in result.get("items", [])) if c]
    if not children and result.get("stream"):
        fallback = MenuEntry(module=entry.module, name=entry.name, url=entry.url, mode=entry.mode, icon=entry.icon, more_data=entry.more_data, description=entry.description, is_folder=False, is_playable=True, catalog_id=entry.catalog_id)
        return collect_videos(fallback, depth=depth + 1, season_hint=season_hint, visited=visited, videos=videos)

    for child in children:
        next_season = season_hint
        extracted_season = _extract_season_number(child.name)
        if extracted_season is not None:
            next_season = extracted_season
        collect_videos(child, depth=depth + 1, season_hint=next_season, visited=visited, videos=videos)
        if len(videos) >= MAX_META_VIDEOS:
            break
    return videos


def build_series_meta(entry: MenuEntry) -> dict:
    meta = build_series_meta_preview(entry)
    videos = collect_videos(entry)
    if videos:
        meta["videos"] = videos
        meta["releaseInfo"] = f"{len(videos)} פריטים"
    else:
        meta["releaseInfo"] = "אין פריטים זמינים כרגע"
    return {"meta": meta}


def resolve_series_stream(entry: MenuEntry) -> dict:
    result = BRIDGE.run(entry)
    stream = result.get("stream")
    if not stream:
        return {"streams": []}
    if stream.get("error"):
        return {"streams": [{"name": entry.name, "description": stream["error"], "externalUrl": PUBLIC_BASE_URL}]}
    url = stream.get("url", "")
    headers = stream.get("headers", {})
    if "youtube.com/" in url:
        return {"streams": [{"name": entry.name, "description": entry.module, "externalUrl": url}]}
    stream_obj = {"name": entry.name, "description": entry.module or entry.catalog_id}
    if url:
        stream_obj["url"] = url
        stream_obj["behaviorHints"] = {"notWebReady": True}
        if headers:
            stream_obj["behaviorHints"]["proxyHeaders"] = {"request": headers}
    else:
        stream_obj["externalUrl"] = PUBLIC_BASE_URL
    return {"streams": [stream_obj]}


def build_search_results(query: str) -> List[dict]:
    query = query.strip().lower()
    if not query:
        return []
    metas: List[dict] = []
    seen: Set[str] = set()
    searchable_catalogs = [key for key, cfg in ROOT_CATALOGS.items() if cfg["type"] == "series" and key != "idanplus_search"]
    for catalog_id in searchable_catalogs:
        try:
            entries = list_catalog_entries(catalog_id)
        except Exception:
            continue
        for entry in entries:
            hay = f"{entry.name} {entry.description}".lower()
            if query not in hay:
                continue
            meta = build_series_meta_preview(entry)
            if meta["id"] in seen:
                continue
            seen.add(meta["id"])
            metas.append(meta)
            if len(metas) >= 80:
                return metas
    return metas


class AppHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_HEAD(self) -> None:
        self._dispatch()

    def do_GET(self) -> None:
        self._dispatch()

    def log_message(self, format: str, *args) -> None:
        log.info("%s - %s", self.address_string(), format % args)

    def _dispatch(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        if path == "/manifest.json":
            manifest = {
                "id": ADDON_ID,
                "version": "0.2.0",
                "name": ADDON_NAME,
                "description": "Idan+ live TV, radio, VOD, radio shows, podcasts and music with hot-reloaded backend data.",
                "logo": f"{PUBLIC_BASE_URL}/assets/icon.png",
                "background": f"{PUBLIC_BASE_URL}/assets/icon.png",
                "resources": [
                    "catalog",
                    {"name": "meta", "types": ["tv"], "idPrefixes": ["idanplus:tv:"]},
                    {"name": "meta", "types": ["series"], "idPrefixes": ["idanplus:series:"]},
                    "stream",
                ],
                "types": ["tv", "series"],
                "catalogs": [
                    {"type": cfg["type"], "id": catalog_id, "name": cfg["name"], **({"extra": [{"name": "search", "isRequired": True}]} if catalog_id == "idanplus_search" else {})}
                    for catalog_id, cfg in ROOT_CATALOGS.items()
                ],
                "behaviorHints": {"configurable": False},
            }
            return response_json(self, manifest)

        if path.startswith("/assets/"):
            file_name = path.split("/assets/", 1)[1]
            asset = ASSETS_DIR / file_name
            if asset.exists() and asset.is_file():
                content_type = "image/png" if asset.suffix.lower() == ".png" else "image/jpeg"
                return response_bytes(self, asset.read_bytes(), content_type)
            return response_json(self, {"error": "asset not found"}, 404)

        m = re.fullmatch(r"/catalog/tv/([^/]+)\.json", path)
        if m:
            catalog_id = m.group(1)
            if catalog_id == "idanplus_live":
                return response_json(self, {"metas": [build_tv_meta_preview(ch) for ch in get_channels("tv")]})
            if catalog_id == "idanplus_radio":
                return response_json(self, {"metas": [build_tv_meta_preview(ch) for ch in get_channels("radio")]})
            return response_json(self, {"metas": []})

        m = re.fullmatch(r"/catalog/series/([^/]+)\.json", path)
        if m:
            catalog_id = m.group(1)
            if catalog_id == "idanplus_search":
                return response_json(self, {"metas": []})
            root = CATALOG_ROOTS.get(catalog_id)
            if not root:
                return response_json(self, {"metas": []})
            try:
                metas = [build_series_meta_preview(entry) for entry in list_catalog_entries(catalog_id)]
            except Exception as exc:
                log.warning("catalog error %s: %s", catalog_id, exc)
                metas = []
            return response_json(self, {"metas": metas})

        m = re.fullmatch(r"/catalog/series/([^/]+)/search=(.*)\.json", path)
        if m:
            catalog_id = m.group(1)
            search_term = m.group(2)
            if catalog_id == "idanplus_search":
                return response_json(self, {"metas": build_search_results(search_term)})
            return response_json(self, {"metas": []})

        m = re.fullmatch(r"/meta/tv/(.+)\.json", path)
        if m:
            meta_id = m.group(1)
            try:
                channel_id = parse_tv_meta_id(meta_id)
            except ValueError:
                return response_json(self, {"meta": None}, 404)
            channel = STORE.get(channel_id)
            if not channel:
                return response_json(self, {"meta": None}, 404)
            return response_json(self, build_tv_meta(channel))

        m = re.fullmatch(r"/meta/series/(.+)\.json", path)
        if m:
            meta_id = m.group(1)
            try:
                entry = parse_series_id(meta_id)
            except Exception:
                return response_json(self, {"meta": None}, 404)
            return response_json(self, build_series_meta(entry))

        m = re.fullmatch(r"/stream/tv/(.+)\.json", path)
        if m:
            meta_id = m.group(1)
            try:
                channel_id = parse_tv_meta_id(meta_id)
            except ValueError:
                return response_json(self, {"streams": []}, 404)
            channel = STORE.get(channel_id)
            if not channel:
                return response_json(self, {"streams": []}, 404)
            return response_json(self, build_tv_streams(channel))

        m = re.fullmatch(r"/stream/series/(.+)\.json", path)
        if m:
            video_id = m.group(1)
            try:
                entry, _season, _episode = parse_video_id(video_id)
            except Exception:
                return response_json(self, {"streams": []}, 404)
            return response_json(self, resolve_series_stream(entry))

        if path == "/":
            return response_json(self, {"name": ADDON_NAME, "manifest": f"{PUBLIC_BASE_URL}/manifest.json", "channels": len(get_channels()), "catalogs": len(ROOT_CATALOGS)})

        return response_json(self, {"error": "not found"}, 404)


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), AppHandler)
    log.info("Idan+ Stremio server listening on %s:%s", HOST, PORT)
    log.info("Manifest URL: %s/manifest.json", PUBLIC_BASE_URL)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
