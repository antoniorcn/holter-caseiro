from typing import Optional, Union
from fastapi import FastAPI, UploadFile, File, Request
from fastapi.responses import JSONResponse
from pathlib import Path
from datetime import datetime
import os

app = FastAPI(title="ECG Block Ingest")

BASE_DIR = Path("../ecg_data")
BASE_DIR.mkdir(parents=True, exist_ok=True)

HEADER = "#device=ESP32+AD8232\n#cols=t_ms,adc,lead_off\n"

def file_for_session_hour(session: str) -> Path:
    # arquivo por hora (UTC)
    now = datetime.utcnow()
    stamp = now.strftime("%Y%m%d_%H")  # ex: 20250826_22
    safe_session = "".join(c for c in session if c.isalnum() or c in ("-", "_"))
    return BASE_DIR / f"{safe_session}__{stamp}.csv"

def ensure_header(path: Path):
    if not path.exists() or path.stat().st_size == 0:
        with open(path, "wb") as f:
            f.write(HEADER.encode("utf-8"))

@app.post("/ingest")
async def ingest(request: Request, file: Union[UploadFile, None] = File(default=None)):
    """
    Recebe blocos CSV via:
      - POST text/csv (corpo puro)
      - ou multipart/form-data (campo 'file')

    Query params:
      session: id lógico do dispositivo/sessão
      fs, avg, block_ms, chunk: metadados (opcionais)
    """
    qp = request.query_params
    session = qp.get("session", "unknown")
    fs = qp.get("fs", "")
    avg = qp.get("avg", "")
    block_ms = qp.get("block_ms", "")
    chunk = qp.get("chunk", "")

    # corpo
    if file is not None:
        content = await file.read()
    else:
        content = await request.body()

    # arquivo por sessão+hora
    out_path = file_for_session_hour(session)
    ensure_header(out_path)

    # normaliza payload: remove cabeçalhos duplicados de bloco (#device/#cols)
    lines = content.split(b"\n")
    filtered = []
    for ln in lines:
        if ln.startswith(b"#device") or ln.startswith(b"#cols") or ln.strip() == b"":
            continue
        filtered.append(ln)
    # reconstroi com newline final
    payload = b"\n".join(filtered)
    if payload and payload[-1:] != b"\n":
        payload += b"\n"

    with open(out_path, "ab") as f:
        f.write(payload)

    return JSONResponse({
        "status": "ok",
        "session": session,
        "fs": fs,
        "avg": avg,
        "block_ms": block_ms,
        "chunk": chunk,
        "saved_file": str(out_path),
        "bytes_appended": len(payload),
    })

@app.get("/")
def root():
    return {"status": "running", "dir": str(BASE_DIR.resolve())}
