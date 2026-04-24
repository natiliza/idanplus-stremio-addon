#!/usr/bin/env python3
from __future__ import annotations

import base64
import json
import logging
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import unquote, urlparse

import requests

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
ASSETS_DIR = BASE_DIR / "assets"
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8090"))
ADDON_ID = os.getenv("ADDON_ID", "community.idanplus.live")
ADDON_NAME = os.getenv("ADDON_NAME", "Idan+")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", f"http://127.0.0.1:{PORT}").rstrip("/")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
CHANNELS_URL = os.getenv(
    "IDANPLUS_CHANNELS_URL",
    "https://raw.githubusercontent.com/Fishenzon/repo/master/zips/plugin.video.idanplus/channels.json",
)
REFRESH_SECONDS = int(os.getenv("IDANPLUS_REFRESH_SECONDS", "300"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "15"))
ALLOW_REMOTE_REFRESH = os.getenv("ALLOW_REMOTE_REFRESH", "true").lower() == "true"
DEFAULT_UA = os.getenv(
    "DEFAULT_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
)

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("idanplus-live-only")


class ChannelsStore:
    def __init__(self, local_path: Path, remote_url: str, refresh_seconds: int) -> None:
        self.local_path = local_path
        self.remote_url = remote_url
        self.refresh_seconds = refresh_seconds
        self._channels: Dict[str, dict] = {}
        self._last_refresh = 0.0
        self._etag: Optional[str] = None
        self._last_modified: Optional[str] = None
        self._lock = threading.Lock()
        self.refresh(force=True)

    def _read_local(self) -> Dict[str, dict]:
        if not self.local_path.exists():
            return {}
        try:
            return json.loads(self.local_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def refresh(self, force: bool = False) -> None:
        with self._lock:
            if not force and (time.time() - self._last_refresh) < self.refresh_seconds:
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
                log.warning("channels refresh failed: %s", exc)
            self._last_refresh = time.time()

    def all(self) -> Dict[str, dict]:
        self.refresh()
        with self._lock:
            return dict(self._channels)

    def get(self, channel_id: str) -> Optional[dict]:
        self.refresh()
        with self._lock:
            item = self._channels.get(channel_id)
            if not item:
                return None
            result = dict(item)
            result["channelID"] = channel_id
            return result


STORE = ChannelsStore(DATA_DIR / "channels.json", CHANNELS_URL, REFRESH_SECONDS)
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": DEFAULT_UA})
I24_TOKEN: Dict[str, Optional[str]] = {"value": None}

CATALOGS = {
    "idanplus_tv": {"name": "עידן פלוס - טלוויזיה", "kind": "tv"},
    "idanplus_radio": {"name": "עידן פלוס - רדיו", "kind": "radio"},
}


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


def make_meta_id(channel_id: str) -> str:
    raw = channel_id.encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def parse_meta_id(meta_id: str) -> str:
    pad = len(meta_id) % 4
    if pad:
        meta_id += "=" * (4 - pad)
    return base64.urlsafe_b64decode(meta_id.encode("ascii")).decode("utf-8")


def asset_url(image: str) -> str:
    if not image:
        return f"{PUBLIC_BASE_URL}/assets/icon.png"
    if image.startswith(("http://", "https://")):
        return image
    name = Path(image).name
    local = ASSETS_DIR / name
    if local.exists():
        return f"{PUBLIC_BASE_URL}/assets/{name}"
    return f"{PUBLIC_BASE_URL}/assets/icon.png"


def get_channels(kind: str) -> list[dict]:
    items = []
    for channel_id, channel in STORE.all().items():
        row = dict(channel)
        row["channelID"] = channel_id
        if row.get("type") != kind:
            continue
        if row.get("index", 0) == 0:
            continue
        items.append(row)
    items.sort(key=lambda x: (x.get("index", 9999), x.get("name", "")))
    return items


def build_meta_preview(channel: dict) -> dict:
    poster = asset_url(channel.get("image", ""))
    kind = channel.get("type", "tv")
    label = "רדיו" if kind == "radio" else "לייב"
    return {
        "id": make_meta_id(channel["channelID"]),
        "type": "tv",
        "name": channel.get("name", channel["channelID"]),
        "poster": poster,
        "posterShape": "square",
        "logo": poster,
        "background": poster,
        "genres": [label, channel.get("module", "tv")],
        "description": f"{label} • מודול: {channel.get('module', 'tv')}",
    }


def build_meta(channel: dict) -> dict:
    meta = build_meta_preview(channel)
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


def resolve_generic(channel: dict) -> tuple[str, Dict[str, str], str]:
    details = channel.get("linkDetails", {})
    headers = {"User-Agent": DEFAULT_UA}
    if details.get("referer"):
        headers["Referer"] = details["referer"]
    link = details.get("link")
    regex = details.get("regex")
    if regex and link:
        link = extract_by_regex(link, regex, headers)
    if isinstance(link, str) and link.startswith("//"):
        link = f"https:{link}"
    if not link:
        raise RuntimeError("missing link")
    return link, headers, f"generic:{channel.get('module', 'tv')}"


def resolve_14tv(channel: dict) -> tuple[str, Dict[str, str], str]:
    details = channel.get("linkDetails", {})
    link = details.get("link")
    headers = {"User-Agent": DEFAULT_UA}
    api = details.get("ch")
    if api:
        resp = SESSION.get(api, headers={"x-tenant-id": "channel14", "User-Agent": DEFAULT_UA}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        link = resp.json().get("vod", {}).get("hlsStream") or link
    if not link:
        raise RuntimeError("missing 14tv link")
    return link, headers, "14tv"


def resolve_keshet(channel: dict) -> tuple[str, Dict[str, str], str]:
    details = channel.get("linkDetails", {})
    raw_path = details.get("link", "")
    if not raw_path:
        raise RuntimeError("missing Keshet path")
    headers = {
        "User-Agent": DEFAULT_UA,
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.mako.co.il/",
        "Origin": "https://www.mako.co.il",
    }
    entitlements = f"https://mass.mako.co.il/ClicksStatistics/entitlementsServicesV2.jsp?et=ngt&lp={raw_path}&rv=AKAMAI"
    resp = SESSION.get(entitlements, headers=headers, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    tickets = data.get("tickets") or []
    ticket = tickets[0].get("ticket") if tickets else None
    if not ticket:
        raise RuntimeError(f"Keshet ticket failure: caseId={data.get('caseId')}")
    path_only = raw_path.split("?", 1)[0]
    stream_url = f"https://mako-streaming.akamaized.net{path_only}?{requests.utils.unquote(ticket)}"
    return stream_url, {"User-Agent": DEFAULT_UA}, "keshet"


def resolve_hidabroot(channel: dict) -> tuple[str, Dict[str, str], str]:
    details = channel.get("linkDetails", {})
    headers = {"User-Agent": DEFAULT_UA}
    page = SESSION.get(details["link"], headers=headers, timeout=REQUEST_TIMEOUT).text
    match = re.search(r'<source\s*src="(.*?)"', page, flags=re.S)
    if not match:
        raise RuntimeError("hidabroot source not found")
    return match.group(1), headers, "hidabroot"


def resolve_sport5(channel: dict) -> tuple[str, Dict[str, str], str]:
    details = channel.get("linkDetails", {})
    headers = {"User-Agent": DEFAULT_UA, "Referer": "https://radio.sport5.co.il"}
    link = details.get("link")
    if details.get("ch"):
        data_url = f"https://radio.sport5.co.il/data/data.json?v={int(time.time() * 1000)}"
        resp = SESSION.get(data_url, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        node = data.get(details["ch"])
        if isinstance(node, str) and node:
            link = node.replace("https://nekot.sport5.co.il:10000?", "")
    if not link:
        raise RuntimeError("missing sport5 link")
    return link, headers, "sport5"


def resolve_i24news(channel: dict) -> tuple[str, Dict[str, str], str]:
    details = channel.get("linkDetails", {})
    stream_url = details.get("link")
    token = get_i24_token()
    if token:
        headers = {"Accept": "application/json", "User-Agent": DEFAULT_UA, "Authorization": f"Bearer {token}"}
        media_url = f"https://api.i24news.wiztivi.io/contents/brightcove/channels/{details.get('ch')}"
        resp = SESSION.get(media_url, headers=headers, timeout=REQUEST_TIMEOUT)
        if resp.ok:
            stream_url = resp.json().get("url") or stream_url
    if not stream_url:
        raise RuntimeError("missing i24 stream")
    return stream_url, {"User-Agent": DEFAULT_UA}, f"i24news:{details.get('ch', '')}"


def resolve_glz(channel: dict) -> tuple[str, Dict[str, str], str]:
    details = channel.get("linkDetails", {})
    headers = {"User-Agent": DEFAULT_UA}
    link = details.get("live") or details.get("link")
    root_id = details.get("rootId")
    if root_id:
        api_url = f"https://glz.co.il/umbraco/api/player/getplayerdata?rootId={root_id}"
        resp = SESSION.get(api_url, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        live = resp.json().get("liveBroadcast", {})
        link = live.get("fileUrl") or link
    if not link:
        raise RuntimeError("missing glz link")
    return link, headers, "glz"


def resolve_100fm(channel: dict) -> tuple[str, Dict[str, str], str]:
    details = channel.get("linkDetails", {})
    headers = {"User-Agent": DEFAULT_UA}
    link = details.get("link")
    api = details.get("ch")
    if api:
        resp = SESSION.get(api, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        stations = resp.json().get("stations") or []
        if stations:
            link = stations[0].get("audio") or link
    if not link:
        raise RuntimeError("missing 100fm link")
    return link, headers, "100fm"


def resolve_1064fm(channel: dict) -> tuple[str, Dict[str, str], str]:
    details = channel.get("linkDetails", {})
    headers = {"User-Agent": DEFAULT_UA}
    link = details.get("link")
    page_url = details.get("ch")
    if page_url:
        text = SESSION.get(page_url, headers=headers, timeout=REQUEST_TIMEOUT).text
        match = re.search(r'"webapp\\.broadcast_link":"(.*?)"', text)
        if match:
            link = match.group(1).replace("\\u002F", "/")
    if not link:
        raise RuntimeError("missing 1064fm link")
    return link, headers, "1064fm"


def resolve_channel(channel: dict) -> dict:
    module = channel.get("module", "tv")
    if module in {"tv", "radio", "kan", "reshet", "99fm"}:
        url, headers, description = resolve_generic(channel)
    elif module == "14tv":
        url, headers, description = resolve_14tv(channel)
    elif module == "keshet":
        url, headers, description = resolve_keshet(channel)
    elif module == "hidabroot":
        url, headers, description = resolve_hidabroot(channel)
    elif module == "sport5":
        url, headers, description = resolve_sport5(channel)
    elif module == "i24news":
        url, headers, description = resolve_i24news(channel)
    elif module == "glz":
        url, headers, description = resolve_glz(channel)
    elif module == "100fm":
        url, headers, description = resolve_100fm(channel)
    elif module == "1064fm":
        url, headers, description = resolve_1064fm(channel)
    else:
        url, headers, description = resolve_generic(channel)

    return {
        "streams": [
            {
                "name": channel.get("name", channel.get("channelID", "Idan+")),
                "description": description,
                "url": url,
                "behaviorHints": {
                    "notWebReady": True,
                    "proxyHeaders": {"request": headers} if headers else {},
                },
            }
        ]
    }


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
        path = unquote(urlparse(self.path).path)

        if path == "/manifest.json":
            manifest = {
                "id": ADDON_ID,
                "version": "0.5.0",
                "name": ADDON_NAME,
                "description": "Idan+ live TV and radio only. Fast, simple and without VOD.",
                "logo": f"{PUBLIC_BASE_URL}/assets/icon.png",
                "background": f"{PUBLIC_BASE_URL}/assets/icon.png",
                "resources": ["catalog", "meta", "stream"],
                "types": ["tv"],
                "catalogs": [
                    {"type": "tv", "id": catalog_id, "name": cfg["name"]}
                    for catalog_id, cfg in CATALOGS.items()
                ],
                "behaviorHints": {"configurable": False},
            }
            return response_json(self, manifest)

        if path.startswith("/assets/"):
            file_name = Path(path.split("/assets/", 1)[1]).name
            asset = ASSETS_DIR / file_name
            if asset.exists() and asset.is_file():
                ctype = "image/png" if asset.suffix.lower() == ".png" else "image/jpeg"
                return response_bytes(self, asset.read_bytes(), ctype)
            return response_json(self, {"error": "asset not found"}, 404)

        m = re.fullmatch(r"/catalog/tv/([^/]+)\.json", path)
        if m:
            catalog_id = m.group(1)
            cfg = CATALOGS.get(catalog_id)
            if not cfg:
                return response_json(self, {"metas": []})
            metas = [build_meta_preview(item) for item in get_channels(cfg["kind"])]
            return response_json(self, {"metas": metas})

        m = re.fullmatch(r"/meta/tv/(.+)\.json", path)
        if m:
            try:
                channel_id = parse_meta_id(m.group(1))
            except Exception:
                return response_json(self, {"meta": None}, 404)
            channel = STORE.get(channel_id)
            if not channel:
                return response_json(self, {"meta": None}, 404)
            return response_json(self, build_meta(channel))

        m = re.fullmatch(r"/stream/tv/(.+)\.json", path)
        if m:
            try:
                channel_id = parse_meta_id(m.group(1))
            except Exception:
                return response_json(self, {"streams": []}, 404)
            channel = STORE.get(channel_id)
            if not channel:
                return response_json(self, {"streams": []}, 404)
            try:
                return response_json(self, resolve_channel(channel))
            except Exception as exc:
                log.exception("stream resolve failed for %s", channel.get("channelID"))
                return response_json(
                    self,
                    {
                        "streams": [
                            {
                                "name": channel.get("name", channel.get("channelID", "Idan+")),
                                "description": str(exc),
                                "externalUrl": channel.get("linkDetails", {}).get("referer") or PUBLIC_BASE_URL,
                            }
                        ]
                    },
                )

        if path == "/":
            return response_json(
                self,
                {
                    "name": ADDON_NAME,
                    "manifest": f"{PUBLIC_BASE_URL}/manifest.json",
                    "tv_channels": len(get_channels("tv")),
                    "radio_channels": len(get_channels("radio")),
                },
            )

        return response_json(self, {"error": "not found"}, 404)


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), AppHandler)
    log.info("Idan+ live-only server listening on %s:%s", HOST, PORT)
    log.info("Manifest URL: %s/manifest.json", PUBLIC_BASE_URL)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
