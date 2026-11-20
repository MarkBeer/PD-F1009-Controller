"""Search Discogs for the CD whose title the cover search suggested.

Preference is a UK retail CD. A Europe-wide Warner, Island, EMI, or Polydor
pressing is often the UK shop copy, so Europe ranks just behind UK. The
chosen release is still a guess when several editions share a title: the
review file lists the other hits so a release id can be pinned in overrides.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

USER_AGENT = "CDIndex/1.0 (local collection research)"
API = "https://api.discogs.com"

_WORD = re.compile(r"[a-z0-9']+")
_BOUNDARY = r"(?<![a-z0-9]){phrase}(?![a-z0-9])"
_STOP = {
    "the",
    "a",
    "an",
    "of",
    "and",
    "vol",
    "volume",
    "cd",
    "album",
    "ost",
    "soundtrack",
    "music",
    "from",
    "original",
}
_GENERIC_TAGS = {
    "cd",
    "compact disc",
    "album",
    "cover",
    "album cover",
    "oblojka",
    "обложка",
}


class DiscogsError(RuntimeError):
    pass


def latin_ratio(text: str) -> float:
    letters = [char for char in text if char.isalpha()]
    if not letters:
        return 0.0
    return sum(char.isascii() for char in letters) / len(letters)


def tokens(text: str) -> list[str]:
    return [word for word in _WORD.findall(text.lower()) if word not in _STOP and len(word) > 1]


def album_phrase(title: str) -> str:
    name = title.split(" - ", 1)[-1]
    name = re.sub(r"\s*\([^)]*\)\s*$", "", name).strip()
    return name


def _phrase_in(phrase: str, text: str) -> bool:
    if len(phrase) < 2:
        return False
    pattern = _BOUNDARY.format(phrase=re.escape(phrase.lower()))
    return re.search(pattern, text.lower()) is not None


def choose_queries(yandex: dict, limit: int = 2) -> list[str]:
    """Pick the cover-search phrases worth sending to Discogs.

    A recognised object title (Believe, for example) promotes the tag that
    contains it. Longer tags are otherwise just song titles printed on the art.
    """
    tags = []
    for tag in yandex.get("tags") or []:
        text = tag.strip()
        if len(text) < 4 or text.lower() in _GENERIC_TAGS:
            continue
        if latin_ratio(text) < 0.6:
            continue
        tags.append(text)
    objects = []
    for obj in yandex.get("objects") or []:
        title = (obj.get("title") or "").strip()
        if len(title) < 3 or latin_ratio(title) < 0.6:
            continue
        objects.append(title)

    def rank_tag(text: str) -> tuple:
        contains_object = sum(1 for title in objects if _phrase_in(title, text))
        return (contains_object, len(tokens(text)), len(text))

    tags.sort(key=rank_tag, reverse=True)
    queries: list[str] = []
    for tag in tags:
        if tag not in queries:
            queries.append(tag)
        if len(queries) >= limit:
            break
    for title in objects:
        if any(_phrase_in(title, query) for query in queries):
            continue
        queries.append(title)
    return queries[:limit]


def evidence_texts(yandex: dict) -> list[str]:
    return [tag for tag in (yandex.get("tags") or []) if latin_ratio(tag) >= 0.45]


def country_score(country: str) -> int:
    name = (country or "").strip().lower()
    exact = {
        "uk": 8,
        "united kingdom": 8,
        "uk & ireland": 7,
        "uk & europe": 7,
        "uk, europe & us": 7,
        "europe": 5,
    }
    if name in exact:
        return exact[name]
    if name.startswith("uk"):
        return 6
    if "europe" in name:
        return 4
    if not name:
        return 1
    return 2


def score_release(hit: dict, evidence: list[str], objects: list[str] | None = None) -> float | None:
    """Rank one Discogs search hit. None means it is not a usable CD.

    Tags are the cover search's own guesses and count more than a single
    object title, which is sometimes a different album with similar art.
    """
    fmt = " ".join(hit.get("format") or []).lower()
    if "cd" not in fmt:
        return None
    objects = objects or []
    phrase = album_phrase(hit.get("title") or "")
    support = sum(1 for text in evidence if _phrase_in(phrase, text))
    object_support = sum(1 for text in objects if _phrase_in(phrase, text))
    shared = len(set(tokens(phrase)) & set(tokens(" ".join([*evidence, *objects]))))
    if support == 0 and object_support == 0 and shared < 2:
        return None
    object_weight = 10 if not evidence else 3
    score = support * 10 + object_support * object_weight + shared * 2
    score += country_score(hit.get("country") or "")
    # The same title is often both the album and a CD single. The album is the case.
    if "album" in fmt:
        score += 3
    elif "single" in fmt:
        score -= 5
    if any(word in fmt for word in ("vinyl", "lp")):
        score -= 8
    if any(word in fmt for word in ("unofficial", "promo", "acetate", "test pressing")):
        score -= 6
    return float(score)


def token() -> str | None:
    return os.environ.get("DISCOGS_TOKEN") or os.environ.get("DISCOGS_USER_TOKEN") or None


def _request(url: str, params: dict | None = None, auth: str | None = None) -> dict:
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if auth:
        headers["Authorization"] = f"Discogs token={auth}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read()[:300].decode("utf-8", errors="replace")
        if exc.code == 429:
            raise DiscogsError("Discogs rate limit reached") from exc
        raise DiscogsError(f"Discogs HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise DiscogsError(f"Could not reach Discogs: {exc.reason}") from exc


def _call(url: str, params: dict | None, auth: str | None, pause: float) -> dict:
    try:
        data = _request(url, params, auth)
    except DiscogsError as exc:
        if "rate limit" not in str(exc).lower():
            raise
        time.sleep(30)
        data = _request(url, params, auth)
    time.sleep(pause)
    return data


def search(query: str, pause: float = 2.7, auth: str | None = None) -> list[dict]:
    payload = _call(
        f"{API}/database/search",
        {"q": query, "type": "release", "format": "CD", "per_page": 25},
        auth if auth is not None else token(),
        pause,
    )
    hits = []
    for item in payload.get("results") or []:
        hits.append(
            {
                "id": item.get("id"),
                "title": item.get("title") or "",
                "country": item.get("country") or "",
                "year": item.get("year") or "",
                "catno": item.get("catno") or "",
                "label": item.get("label") or [],
                "format": item.get("format") or [],
                "master_id": item.get("master_id") or 0,
                "uri": item.get("uri") or "",
            }
        )
    return hits


def _artist_field(artists) -> str:
    from indexer.catalog import artist_text

    return artist_text(artists)


def slim_release(payload: dict) -> dict:
    """Keep the fields the CSV writer needs, including featured credits."""
    tracks = []
    for item in payload.get("tracklist") or payload.get("tracks") or []:
        featuring = []
        for extra in item.get("extraartists") or []:
            role = (extra.get("role") or "").lower()
            if "feat" not in role:
                continue
            name = extra.get("name") or ""
            if name:
                featuring.append({"name": name, "role": extra.get("role") or ""})
        tracks.append(
            {
                "pos": item.get("position") or item.get("pos") or "",
                "title": item.get("title") or "",
                "type": item.get("type_") or item.get("type") or "track",
                "artists": _artist_field(item.get("artists")),
                "featuring": [entry["name"] for entry in featuring],
            }
        )
    formats = []
    for fmt in payload.get("formats") or []:
        if isinstance(fmt, str):
            formats.append({"name": fmt, "qty": "1", "descriptions": []})
        else:
            formats.append(
                {
                    "name": fmt.get("name") or "",
                    "qty": fmt.get("qty") or "1",
                    "descriptions": fmt.get("descriptions") or [],
                }
            )
    labels = []
    for label in payload.get("labels") or []:
        if isinstance(label, str):
            labels.append({"name": label, "catno": ""})
        else:
            labels.append({"name": label.get("name") or "", "catno": label.get("catno") or ""})
    return {
        "id": payload.get("id"),
        "title": payload.get("title") or "",
        "artists": _artist_field(payload.get("artists")),
        "year": payload.get("year") or "",
        "country": payload.get("country") or "",
        "formats": formats,
        "labels": labels,
        "master_id": payload.get("master_id") or 0,
        "tracks": tracks,
    }


def fetch_release(release_id: int, pause: float = 2.7, auth: str | None = None) -> dict:
    payload = _call(f"{API}/releases/{release_id}", None, auth if auth is not None else token(), pause)
    return slim_release(payload)


def rank_hits(
    hits: list[dict],
    evidence: list[str],
    objects: list[str] | None = None,
    limit: int = 8,
) -> list[dict]:
    ranked = []
    seen: set[int] = set()
    for hit in hits:
        if not hit.get("id") or hit["id"] in seen:
            continue
        seen.add(hit["id"])
        score = score_release(hit, evidence, objects)
        if score is None:
            continue
        ranked.append({**hit, "score": score})
    ranked.sort(key=lambda item: (-item["score"], -country_score(item.get("country") or ""), item["id"]))
    return ranked[:limit]


def choose_status(ranked: list[dict]) -> str:
    if not ranked:
        return "unable to identify"
    best = ranked[0]
    phrase = album_phrase(best.get("title") or "").lower()
    support = best["score"]
    if support < 12:
        return "needs_review"
    for other in ranked[1:]:
        other_phrase = album_phrase(other.get("title") or "").lower()
        if other_phrase != phrase and best["score"] - other["score"] <= 8:
            return "needs_review"
    return "identified"


def load_release_file(directory: Path, release_id: int) -> dict | None:
    path = Path(directory) / f"{release_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def save_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
