"""Download authenticated Twilio media into local development storage."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

ALLOWED_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024
UPLOADS_DIR = Path(__file__).resolve().parent / "uploads"


class StorageError(Exception):
    """Raised when a meal image cannot be safely downloaded or saved."""


def download_and_save_image(
    media_url: str,
    message_sid: str,
    content_type: str,
) -> Path:
    """Download one Twilio image and save it locally using a stable filename."""
    normalized_content_type = content_type.split(";", 1)[0].strip().lower()
    extension = ALLOWED_CONTENT_TYPES.get(normalized_content_type)
    if extension is None:
        raise StorageError(f"Unsupported content type: {normalized_content_type}")

    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    if not account_sid or not auth_token:
        raise StorageError("Twilio credentials are not configured.")

    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    message_hash = hashlib.sha256(message_sid.encode("utf-8")).hexdigest()
    destination = UPLOADS_DIR / f"{message_hash}{extension}"
    temporary_destination = UPLOADS_DIR / f"{message_hash}{extension}.tmp"

    if destination.exists():
        return destination

    try:
        with requests.get(
            media_url,
            auth=(account_sid, auth_token),
            stream=True,
            timeout=20,
        ) as response:
            response.raise_for_status()

            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    if int(content_length) > MAX_FILE_SIZE_BYTES:
                        raise StorageError(
                            "Image is too large. Please send an image under 10 MB."
                        )
                except ValueError:
                    pass

            total_bytes = 0
            with temporary_destination.open("wb") as image_file:
                for chunk in response.iter_content(chunk_size=8192):
                    if not chunk:
                        continue

                    total_bytes += len(chunk)
                    if total_bytes > MAX_FILE_SIZE_BYTES:
                        raise StorageError(
                            "Image is too large. Please send an image under 10 MB."
                        )

                    image_file.write(chunk)

        temporary_destination.replace(destination)
        return destination

    except requests.RequestException as error:
        temporary_destination.unlink(missing_ok=True)
        raise StorageError("Could not download the meal image.") from error
    except StorageError:
        temporary_destination.unlink(missing_ok=True)
        raise
    except OSError as error:
        temporary_destination.unlink(missing_ok=True)
        raise StorageError("Could not save the meal image.") from error
