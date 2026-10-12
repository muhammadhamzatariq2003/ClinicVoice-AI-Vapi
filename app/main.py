"""Start the webhook server:  python -m app.main"""

from __future__ import annotations

import logging
import sys

import uvicorn

from app.clinic import ConfigError, load_clinic
from app.settings import get_settings, load_dotenv_file


def main() -> None:
    load_dotenv_file()
    settings = get_settings()
    logging.basicConfig(level=logging.DEBUG if settings.debug else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("app.main")

    if not settings.webhook_secret:
        log.warning("VAPI_WEBHOOK_SECRET is not set: the webhook accepts unauthenticated requests. "
                    "Set it before exposing this server to the internet.")
    if not settings.vapi_api_key:
        log.info("VAPI_API_KEY not set: the server runs fine, but scripts/setup_vapi.py needs it.")

    try:
        clinic = load_clinic(settings.clinic_config, settings.transfer_number)
    except (ConfigError, FileNotFoundError) as exc:
        log.error("Cannot start: %s", exc)
        sys.exit(2)
    log.info("Clinic: %s (%s)", clinic.clinic_name, settings.clinic_config)
    log.info("Webhook: http://%s:%s/api/vapi/webhook | Dashboard: /dashboard | Health: /health",
             settings.host, settings.port)

    uvicorn.run("app.server:app", host=settings.host, port=settings.port, reload=settings.debug)


if __name__ == "__main__":
    main()
