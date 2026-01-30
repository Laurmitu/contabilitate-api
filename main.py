import os
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text

DATABASE_URL = os.getenv("DATABASE_URL", "")

app = FastAPI(title="Contabilitate API", version="0.2.0")

# CORS: pentru început, permitem orice (la producție restrângem pe domeniul web)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

if not DATABASE_URL:
    # Nu aruncăm aici ca să putem vedea /health chiar și fără DB,
    # dar rutele DB vor da eroare clară.
    engine = None
else:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)


# ---------- Models ----------
class CompanyCreate(BaseModel):
    name: str = Field(..., min_length=1)
    cui: str = Field(..., min_length=2)
    invoice_series: str = Field(..., min_length=1, max_length=10)


# ---------- Helpers ----------
def _require_db():
    if not DATABASE_URL or engine is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL env var is missing")
    return engine


# ---------- Basic ----------
@app.get("/")
def root():
    return {"status": "ok", "service": "contabilitate-api"}


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/db-test")
def db_test():
    _require_db()
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"db": "connected"}


# ---------- Admin: init + seed ----------
@app.post("/admin/init-db")
def admin_init_db():
    """
    Creează tabelele minime pentru:
    - companii (multi-firmă)
    - clienți
    - facturi
    - linii factură
    """
    _require_db()

    schema_sql = """
    create table if not exists companies (
      id bigserial primary key,
      name text not null,
      cui text not null unique,
      invoice_series text not null,
      created_at timestamptz not null default now()
    );

    create table if not exists customers (
      id bigserial primary key,
      company_id bigint not null references companies(id) on delete cascade,
      name text not null,
      cui text,
      address text,
      created_at timestamptz not null default now()
    );

    create table if not exists invoices (
      id bigserial primary key,
      company_id bigint not null references companies(id) on delete cascade,
      number bigint not null,
      series text not null,
      issue_date date not null default current_date,
      customer_id bigint references customers(id),
      currency text not null default 'RON',
      total numeric(12,2) not null default 0,
      created_at timestamptz not null default now(),
      unique(company_id, series, number)
    );

    create table if not exists invoice_lines (
      id bigserial primary key,
      invoice_id bigint not null references invoices(id) on delete cascade,
      name text not null,
      qty numeric(12,3) not null default 1,
      unit text default 'buc',
      unit_price numeric(12,2) not null default 0,
      vat_rate numeric(5,2) not null default 19,
      line_total numeric(12,2) not null default 0
    );
    """

    with engine.begin() as conn:
        conn.execute(text(schema_sql))

    return {"ok": True, "message": "DB initialized"}


@app.post("/admin/seed")
def admin_seed():
    """
    Inserează firmele tale de start (dacă nu există).
    """
    _require_db()

    seed_sql = """
    insert into companies (name, cui, invoice_series)
    values
      ('ROSIPROD SRL', 'RO9608452', 'ROS'),
      ('SILAI CEREAL COMPANY SRL', 'RO45698419', 'SCC'),
      ('OUAI DOLOSMANU', 'RO41291160', 'OD')
    on conflict (cui) do nothing;
    """

    with engine.begin() as conn:
        conn.execute(text(seed_sql))

    return {"ok": True, "message": "Seed completed"}


# ---------- Companies ----------
@app.get("/companies")
def list_companies() -> List[Dict[str, Any]]:
    _require_db()
    with engine.connect() as conn:
        rows = conn.execute(
            text("select id, name, cui, invoice_series, created_at from companies order by id asc")
        ).mappings().all()
    return [dict(r) for r in rows]


@app.post("/companies")
def create_company(payload: CompanyCreate):
    _require_db()

    with engine.begin() as conn:
        # Normalizăm CUI (să fie RO... dacă userul pune fără)
        cui = payload.cui.strip()
        name = payload.name.strip()
        series = payload.invoice_series.strip().upper()

        # Insert
        try:
            conn.execute(
                text(
                    """
                    insert into companies (name, cui, invoice_series)
                    values (:name, :cui, :series)
                    """
                ),
                {"name": name, "cui": cui, "series": series},
            )
        except Exception as e:
            # De obicei conflict pe cui unique
            raise HTTPException(status_code=400, detail=f"Could not create company: {str(e)}")

    return {"ok": True}
