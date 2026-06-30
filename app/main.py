import asyncio
import base64
import io
import json

import httpx
import pandas as pd
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.yandex import YandexApiError, compute_distance

app = FastAPI(title="Address Distance Calculator")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition", "X-Errors-Count", "X-Errors-Detail"],
)


def _read_table(filename: str, content: bytes) -> pd.DataFrame:
    if filename.lower().endswith(".csv"):
        return pd.read_csv(io.BytesIO(content))
    return pd.read_excel(io.BytesIO(content))


def _write_table(df: pd.DataFrame, filename: str) -> tuple[bytes, str, str]:
    if filename.lower().endswith(".csv"):
        buf = io.StringIO()
        df.to_csv(buf, index=False)
        return buf.getvalue().encode("utf-8-sig"), "text/csv", "result.csv"
    buf = io.BytesIO()
    df.to_excel(buf, index=False, engine="openpyxl")
    return buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "result.xlsx"


@app.post("/api/columns")
async def get_columns(file: UploadFile = File(...)):
    content = await file.read()
    try:
        df = _read_table(file.filename, content)
    except Exception as exc:
        raise HTTPException(400, f"Не удалось прочитать файл: {exc}") from exc
    return {"columns": list(df.columns.astype(str))}


@app.post("/api/process")
async def process(
    file: UploadFile = File(...),
    from_column: str = Form(...),
    to_column: str = Form(...),
    result_column: str = Form("Расстояние, км"),
    geocoder_key: str = Form(...),
    city: str = Form(""),
):
    content = await file.read()
    try:
        df = _read_table(file.filename, content)
    except Exception as exc:
        raise HTTPException(400, f"Не удалось прочитать файл: {exc}") from exc

    if from_column not in df.columns or to_column not in df.columns:
        raise HTTPException(400, "Выбранные столбцы не найдены в таблице")

    city = city.strip()

    def with_city(address: str) -> str:
        return f"{city}, {address}" if city else address

    sem = asyncio.Semaphore(5)
    results: list[int | None] = [None] * len(df)
    errors: list[str] = []

    async with httpx.AsyncClient(timeout=20.0) as client:
        async def worker(i: int):
            address_from = str(df.iloc[i][from_column])
            address_to = str(df.iloc[i][to_column])
            if not address_from.strip() or not address_to.strip() or address_from == "nan" or address_to == "nan":
                errors.append(f"Строка {i + 2}: пустой адрес")
                return
            try:
                results[i] = await compute_distance(
                    client, geocoder_key, with_city(address_from), with_city(address_to), sem
                )
            except (YandexApiError, httpx.HTTPStatusError) as exc:
                errors.append(f"Строка {i + 2}: {exc}")

        await asyncio.gather(*(worker(i) for i in range(len(df))))

    df[result_column] = results

    body, media_type, out_name = _write_table(df, file.filename)
    errors_b64 = base64.b64encode(json.dumps(errors[:50], ensure_ascii=False).encode("utf-8")).decode("ascii")
    headers = {
        "Content-Disposition": f'attachment; filename="{out_name}"',
        "X-Errors-Count": str(len(errors)),
        "X-Errors-Detail": errors_b64,
    }
    return StreamingResponse(io.BytesIO(body), media_type=media_type, headers=headers)


app.mount("/", StaticFiles(directory="static", html=True), name="static")
