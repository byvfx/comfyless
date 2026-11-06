"""Comfyless: run ComfyUI workflows on a cloud GPU in your own Modal account.

Adds no graph nodes. It registers HTTP routes on ComfyUI's server and a frontend extension
(web/js) that sends queued prompts to Modal while "Run on Cloud" is on.
"""

import logging

NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}
WEB_DIRECTORY = "./web/js"

try:
    from .bridge import routes

    routes.setup()
except ImportError as exc:  # e.g. the modal package failed to install
    logging.error("[modal-bridge] disabled: %s", exc)
    _reason = f"The Modal Cloud Bridge couldn't load ({exc}). Reinstall it from ComfyUI-Manager and restart ComfyUI."

    try:  # still answer the frontend's status check, so the user sees why instead of nothing
        from aiohttp import web
        from server import PromptServer

        @PromptServer.instance.routes.get("/modal_bridge/status")
        async def _broken_status(_request):
            return web.json_response({"connected": False, "broken": True, "error": _reason, "setup_done": True})
    except Exception:
        pass

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
