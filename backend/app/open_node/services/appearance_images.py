"""Bounded image decoding; SVG stays an isolated image, never inline application markup."""

import io
import re
import warnings
from xml.etree import ElementTree

from PIL import Image, UnidentifiedImageError

from open_node.domain.appearance import ASSET_LIMITS, AppearanceError

RASTER_TYPES = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp",
                "GIF": "image/gif", "ICO": "image/x-icon"}
IMAGE_TYPES = {*RASTER_TYPES.values(), "image/svg+xml"}
MAX_PIXELS = 25_000_000
MAX_FRAME_PIXELS = 50_000_000


def _svg(data):
    source = data.decode("utf-8-sig")
    if re.search(r"<!\s*(DOCTYPE|ENTITY)", source, re.I):
        raise ValueError()
    root = ElementTree.fromstring(source)
    if root.tag != "{http://www.w3.org/2000/svg}svg":
        raise ValueError()
    stack = [(root, 0)]
    count = 0
    while stack:
        node, depth = stack.pop()
        count += 1
        if count > 10000 or depth > 64:
            raise ValueError()
        if not isinstance(node.tag, str) or not node.tag.startswith("{http://www.w3.org/2000/svg}"):
            raise ValueError()
        name = node.tag.split("}")[-1].lower()
        if name in {"script", "foreignobject", "handler", "listener"}:
            raise ValueError()
        for key, value in node.attrib.items():
            attribute = key.split("}")[-1].lower()
            if attribute.startswith("on") or (attribute == "href" and not value.startswith("#")):
                raise ValueError()
        stack.extend((child, depth + 1) for child in node)
    return "image/svg+xml"


def validate_image(slot, data):
    if slot not in ASSET_LIMITS or not data:
        raise AppearanceError(422, "appearance_invalid_image")
    if len(data) > ASSET_LIMITS[slot]:
        raise AppearanceError(413, "appearance_invalid_image")
    try:
        if data.lstrip(b"\xef\xbb\xbf \r\n\t").startswith(b"<"):
            return _svg(data)
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data), formats=list(RASTER_TYPES)) as picture:
                media = RASTER_TYPES[picture.format]
                width, height = picture.size
                frames = getattr(picture, "n_frames", 1)
                if (not 0 < width <= 8192 or not 0 < height <= 8192
                        or width * height > MAX_PIXELS or frames > 120
                        or width * height * frames > MAX_FRAME_PIXELS):
                    raise ValueError()
                picture.verify()
            with Image.open(io.BytesIO(data), formats=list(RASTER_TYPES)) as picture:
                for frame in range(frames):
                    picture.seek(frame)
                    picture.load()
        return media
    except (ValueError, KeyError, OSError, EOFError, SyntaxError, UnidentifiedImageError,
            Image.DecompressionBombError, Image.DecompressionBombWarning,
            ElementTree.ParseError):
        raise AppearanceError(422, "appearance_invalid_image") from None
