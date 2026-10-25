"""Local server runner used by start.bat.

Runs uvicorn programmatically so stop.bat can ask it to shut down cleanly
(finish open requests, close the database) instead of killing the process.
"""
from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)


def main() -> None:
    import uvicorn
    from fastapi import Request
    from fastapi.responses import JSONResponse

    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    token = os.environ.get("LOCAL_SHUTDOWN_TOKEN") or secrets.token_hex(16)

    from app.main import app

    config = uvicorn.Config(app, host=host, port=port, log_level="info", proxy_headers=False)
    server = uvicorn.Server(config)

    @app.post("/__local/shutdown")
    async def _shutdown(request: Request, t: str = ""):
        # only from this computer, only with the token written by tools/local.py
        if request.client is None or request.client.host not in ("127.0.0.1", "::1") or t != token:
            return JSONResponse({"error": "forbidden"}, 403)
        server.should_exit = True
        return {"ok": True}

    server.run()


if __name__ == "__main__":
    main()
