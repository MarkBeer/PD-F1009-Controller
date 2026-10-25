"""Reverse image search through Yandex Images.

Google Lens, Bing, and TinEye did not return usable results from this
environment. Yandex accepts the crop as an upload and the results page
embeds tags, object titles, and matching sites.
"""

from __future__ import annotations

import html
import json
import uuid
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
UPLOAD_URL = "https://yandex.com/images/search"


class YandexError(RuntimeError):
    pass


def _extract_object(text: str, key: str) -> dict | None:
    needle = f'"{key}":'
    start = text.find(needle)
    if start < 0:
        return None
    brace = text.find("{", start)
    if brace < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(brace, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[brace : index + 1])
                except json.JSONDecodeError:
                    return None
    return None


def parse_results_html(page: str) -> dict:
    """Pull tags, recognised objects, and site titles out of a results page."""
    text = html.unescape(page)
    if "&quot;" in text:
        text = html.unescape(text)
    if "captcha" in text.lower() and "cbirTags" not in text and "cbirObjectResponses" not in text:
        raise YandexError("Yandex returned a captcha instead of image results.")
    tags_obj = _extract_object(text, "cbirTags") or {}
    objects_obj = _extract_object(text, "cbirObjectResponses") or {}
    sites_obj = _extract_object(text, "cbirSites") or {}
    tags = [item.get("text", "").strip() for item in tags_obj.get("tags") or [] if item.get("text")]
    objects = []
    for item in objects_obj.get("objectResponses") or []:
        title = (item.get("title") or "").strip()
        if not title:
            continue
        objects.append({"title": title, "subtitle": (item.get("subtitle") or "").strip()})
    sites = []
    for item in (sites_obj.get("sites") or [])[:12]:
        title = (item.get("title") or "").strip()
        if not title:
            continue
        sites.append(
            {
                "title": title,
                "url": item.get("url") or "",
                "domain": item.get("domain") or "",
            }
        )
    return {"tags": tags, "objects": objects, "sites": sites}


def _request(url: str, data: bytes | None = None, headers: dict | None = None) -> bytes:
    req = urllib.request.Request(url, data=data, headers=headers or {}, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read()[:300].decode("utf-8", errors="replace")
        raise YandexError(f"Yandex HTTP {exc.code} for {url}: {body}") from exc
    except urllib.error.URLError as exc:
        raise YandexError(f"Could not reach Yandex: {exc.reason}") from exc


def search_cover(image_path: Path) -> dict:
    """Upload one crop and return tags, objects, and site titles."""
    content = Path(image_path).read_bytes()
    boundary = f"----cdindex{uuid.uuid4().hex}"
    filename = Path(image_path).name
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="upfile"; filename="{filename}"\r\n'
        f"Content-Type: image/jpeg\r\n\r\n"
    ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
    query = urllib.parse.urlencode(
        {
            "rpt": "imageview",
            "format": "json",
            "request": json.dumps({"blocks": [{"block": "b-page_type_search-by-image__link"}]}),
        }
    )
    raw = _request(
        f"{UPLOAD_URL}?{query}",
        data=body,
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
        },
    )
    try:
        uploaded = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise YandexError("Yandex upload did not return JSON.") from exc
    blocks = uploaded.get("blocks") or []
    if not blocks:
        raise YandexError("Yandex upload returned no image id. The request may have been blocked.")
    params = blocks[0].get("params") or {}
    cbir_id = params.get("cbirId") or ""
    original = params.get("originalImageUrl") or ""
    if not cbir_id:
        raise YandexError("Yandex upload did not include a cbir id.")
    page_url = UPLOAD_URL + "?" + urllib.parse.urlencode(
        {"rpt": "imageview", "cbir_id": cbir_id, "url": original}
    )
    page = _request(page_url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"}).decode(
        "utf-8", errors="replace"
    )
    parsed = parse_results_html(page)
    parsed["cbir_id"] = cbir_id
    return parsed
