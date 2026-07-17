from __future__ import annotations

from aiohttp import web

from services.messenger.audio_access import get_audio_access_grant, register_audio_access
from services.messenger.audio_links import resolve_public_audio_path


async def audio_media(request: web.Request) -> web.StreamResponse:
    filename = request.match_info.get("filename", "")
    path = resolve_public_audio_path(filename)
    if path is None:
        raise web.HTTPNotFound()
    return web.FileResponse(path)


async def audio_access(request: web.Request) -> web.StreamResponse:
    token = request.match_info.get("token", "")
    grant = get_audio_access_grant(token)
    if grant is None or not grant.file_path.exists() or not grant.file_path.is_file():
        raise web.HTTPNotFound()
    registered = register_audio_access(token)
    if registered is None:
        raise web.HTTPNotFound()
    return web.FileResponse(
        registered.file_path,
        headers={
            "Cache-Control": "private, no-store, max-age=0",
            "Pragma": "no-cache",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )
