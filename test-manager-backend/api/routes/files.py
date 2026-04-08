import hashlib
import hmac
from uuid import uuid4
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import Response
from quixportal import get_filesystem
from pymongo.database import Database

from ..auth import update_permission, read_permission
from ..models import File, PresignedUploadRequest, PresignedUploadResponse
from ..mongo import get_mongo
from ..settings import Settings, get_settings
from ..utils import timestamp

router = APIRouter()


@router.post("/tests/{test_id}/files", response_model=PresignedUploadResponse)
def get_presigned_upload_url(
    test_id: str,
    payload: PresignedUploadRequest,
    request: Request,
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    settings: Settings = Depends(get_settings),
    _: None = Depends(update_permission),
) -> PresignedUploadResponse:
    if not mongo.tests.find_one({"_id": test_id}):
        raise HTTPException(status_code=404, detail="Test not found")

    expires = timestamp() + settings.file_signature_expiration_seconds
    url = request.url_for("upload_file", test_id=test_id)
    url = url.replace(scheme="https")  # force https

    signature = _get_signature(
        path=url.path,
        expires=expires,
        secret_key=settings.secret_key,
        filename=payload.filename,
    )

    query_params = {
        "expires": expires,
        "signature": signature,
        "filename": payload.filename,
    }
    url = url.replace(query=urlencode(query_params))
    return PresignedUploadResponse(url=str(url))


@router.post("/tests/{test_id}/files/upload", response_model=File, name="upload_file")
async def upload_file(
    test_id: str,
    request: Request,
    expires: int = Query(...),
    signature: str = Query(...),
    filename: str = Query(...),
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    fs: Any = Depends(get_filesystem),
    settings: Settings = Depends(get_settings),
    _: None = Depends(update_permission),
) -> File:
    if not _verify_signature(
        signature=signature,
        path=request.url.path,
        expires=expires,
        secret_key=settings.secret_key,
        filename=filename,
    ):
        raise HTTPException(status_code=403, detail="Invalid signature")

    if not mongo.tests.find_one({"_id": test_id}):
        raise HTTPException(status_code=404, detail="Test not found")

    path = f"{settings.workspace_id}/test-manager/{test_id}/{filename}"
    body = await request.body()
    fs.write_bytes(path, body)

    file_id = str(uuid4())
    url = request.url_for("download_file", test_id=test_id, file_id=file_id)
    url = url.replace(scheme="https")  # force https
    file_data = File(id=file_id, name=filename, url=str(url), size=len(body))

    mongo.tests.update_one(
        {"_id": test_id},
        {
            "$set": {
                f"files.{file_id}": file_data.model_dump(),
            },
        },
    )

    return file_data


@router.get("/tests/{test_id}/files", response_model=list[File])
def list_files(
    test_id: str,
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(read_permission),
) -> list[File]:
    if not (test := mongo.tests.find_one({"_id": test_id})):
        raise HTTPException(status_code=404, detail="Test not found")

    files = test.get("files", {})
    return [File(**data) for data in files.values()]


@router.get("/tests/{test_id}/files/{file_id}", response_model=File)
def get_file(
    test_id: str,
    file_id: str,
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(read_permission),
) -> File:
    if not (test := mongo.tests.find_one({"_id": test_id})):
        raise HTTPException(status_code=404, detail="Test not found")

    if not (file_data := test.get("files", {}).get(file_id)):
        raise HTTPException(status_code=404, detail="File not found")

    return File(**file_data)


@router.get("/tests/{test_id}/files/{file_id}/download", name="download_file")
def download_file(
    test_id: str,
    file_id: str,
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    fs: Any = Depends(get_filesystem),
    settings: Settings = Depends(get_settings),
    _: None = Depends(read_permission),
) -> Response:
    if not (test := mongo.tests.find_one({"_id": test_id})):
        raise HTTPException(status_code=404, detail="Test not found")

    if not (file_data := test.get("files", {}).get(file_id)):
        raise HTTPException(status_code=404, detail="File not found")

    file_name = file_data["name"]
    path = f"{settings.workspace_id}/test-manager/{test_id}/{file_name}"
    if not fs.exists(path):
        raise HTTPException(status_code=404, detail="File not found in storage")

    return Response(
        content=fs.read_bytes(path),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename={file_name}"},
    )


@router.delete("/tests/{test_id}/files/{file_id}", status_code=204)
def delete_file(
    test_id: str,
    file_id: str,
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    fs: Any = Depends(get_filesystem),
    settings: Settings = Depends(get_settings),
    _: None = Depends(update_permission),
) -> None:
    test = mongo.tests.find_one({"_id": test_id})
    if not test:
        raise HTTPException(status_code=404, detail="Test not found")

    files = test.get("files", {})
    if file_id not in files:
        raise HTTPException(status_code=404, detail="File not found")

    file_name = files[file_id]["name"]
    path = f"{settings.workspace_id}/test-manager/{test_id}/{file_name}"
    if fs.exists(path):
        fs.rm_file(path)

    result = mongo.tests.update_one(
        {"_id": test_id},
        {"$unset": {f"files.{file_id}": ""}},
    )

    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="File not found")


def _get_signature(path: str, expires: int, secret_key: str, filename: str) -> str:
    signing_key = hmac.new(
        secret_key.encode(),
        str(expires).encode(),
        hashlib.sha256,
    ).digest()
    query = {"expires": expires, "filename": filename}
    query_string = urlencode(query)
    return hmac.new(
        signing_key,
        f"{path}?{query_string}".encode(),
        hashlib.sha256,
    ).hexdigest()


def _verify_signature(
    signature: str,
    path: str,
    expires: int,
    secret_key: str,
    filename: str,
) -> bool:
    if timestamp() > expires:
        return False

    expected_signature = _get_signature(
        path=path,
        expires=expires,
        secret_key=secret_key,
        filename=filename,
    )

    return hmac.compare_digest(expected_signature, signature)
