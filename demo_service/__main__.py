import os

import uvicorn

uvicorn.run("demo_service.app:app", host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", "8000")),
            log_level=os.environ.get("LOG_LEVEL", "warning"))
