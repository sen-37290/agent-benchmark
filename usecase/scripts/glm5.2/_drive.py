"""Authenticated flat-folder Google Drive input loader for GLM-5.2 figures."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

DRIVE_FOLDER_ID = "1hBIuy5TDgJDsLSlj6WEbEzNt-RUR206q"
USECASE_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = USECASE_ROOT / ".cache" / "glm5.2-drive"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def drive_credentials(login: bool):
    from google.oauth2.credentials import Credentials

    if shutil.which("gcloud") is None:
        raise RuntimeError(
            "Google Cloud CLI is required. Install it from "
            "https://cloud.google.com/sdk/docs/install and try again."
        )
    if login:
        subprocess.run(
            ["gcloud", "auth", "login", "--enable-gdrive-access"], check=True
        )
    try:
        token = subprocess.run(
            ["gcloud", "auth", "print-access-token"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except subprocess.CalledProcessError as error:
        raise RuntimeError(
            "Google login is required. Run this script again with --login."
        ) from error
    return Credentials(token=token)


def download(filename: str, login: bool) -> Path:
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload

    service = build("drive", "v3", credentials=drive_credentials(login))
    escaped = filename.replace("'", "\\'")
    response = (
        service.files()
        .list(
            q=f"'{DRIVE_FOLDER_ID}' in parents and name = '{escaped}' and trashed = false",
            fields="files(id,name,mimeType)",
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
        )
        .execute()
    )
    files = response.get("files", [])
    if len(files) != 1:
        raise FileNotFoundError(
            f"Expected exactly one {filename} in Drive folder {DRIVE_FOLDER_ID}; "
            f"found {len(files)}"
        )

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    target = CACHE_DIR / filename
    request = service.files().get_media(fileId=files[0]["id"], supportsAllDrives=True)
    with target.open("wb") as stream:
        downloader = MediaIoBaseDownload(stream, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    return target


def get_data_file(
    filename: str,
    expected_sha256: str,
    local_path: Path | None,
    refresh: bool,
    login: bool,
) -> Path:
    if local_path is not None:
        path = local_path.resolve()
    else:
        path = CACHE_DIR / filename
        if refresh or not path.exists():
            path = download(filename, login)

    if not path.is_file():
        raise FileNotFoundError(path)
    actual = sha256(path)
    if actual != expected_sha256:
        raise ValueError(
            f"Checksum mismatch for {path}: expected {expected_sha256}, got {actual}"
        )
    return path
