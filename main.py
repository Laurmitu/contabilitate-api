import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, text

DATABASE_URL = os.getenv("DATABASE_URL", "")

app = FastAPI(title="Contabilitate API", version="0.1.0")

# CORS: pentru început, permitem orice (la producție restrângem pe domeniul web)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
)

@app.get("/")
def root():
    return {"status": "ok", "service": "contabilitate-api"}

@app.get("/health")
def health():
    return {"ok": True}

@app.get("/db-test")
def db_test():
    if not DATABASE_URL:
        return {"db": "missing DATABASE_URL env var"}

    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"db": "connected"}
