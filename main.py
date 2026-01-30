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
import os
from datetime import date
from typing import List, Optional

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

DATABASE_URL = os.getenv("DATABASE_URL", "")

app = FastAPI(title="Contabilitate API", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # la producție: doar domeniul tău web
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)


# -----------------------------
# DB helpers
# -----------------------------
def db_exec(sql: str, params: dict | None = None):
    with engine.begin() as conn:
        return conn.execute(text(sql), params or {})


def ensure_db_url():
    if not DATABASE_URL:
        raise HTTPException(status_code=500, detail="Missing DATABASE_URL env var")


# -----------------------------
# Schemas (Pydantic)
# -----------------------------
class ClientCreate(BaseModel):
    company_id: int
    name: str
    cui: Optional[str] = None
    reg_com: Optional[str] = None
    address: Optional[str] = None
    vat_payer: bool = False


class InvoiceLineCreate(BaseModel):
    description: str
    unit: str = "buc"
    qty: float = Field(gt=0)
    price: float = Field(ge=0)
    vat_rate: float = Field(ge=0, le=100, default=0)


class InvoiceCreate(BaseModel):
    company_id: int
    client_id: int
    issue_date: date = Field(default_factory=date.today)
    due_date: Optional[date] = None
    currency: str = "RON"
    notes: Optional[str] = None
    lines: List[InvoiceLineCreate]


# -----------------------------
# Basic
# -----------------------------
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


# -----------------------------
# Admin: init / seed
# -----------------------------
@app.post("/admin/init-db")
def admin_init_db():
    ensure_db_url()

    # Companies (deja îl ai; îl păstrez)
    db_exec("""
    CREATE TABLE IF NOT EXISTS companies (
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        cui TEXT NOT NULL,
        invoice_series TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """)

    # Clients
    db_exec("""
    CREATE TABLE IF NOT EXISTS clients (
        id SERIAL PRIMARY KEY,
        company_id INT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        cui TEXT,
        reg_com TEXT,
        address TEXT,
        vat_payer BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """)

    # Invoices
    db_exec("""
    CREATE TABLE IF NOT EXISTS invoices (
        id SERIAL PRIMARY KEY,
        company_id INT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
        client_id INT NOT NULL REFERENCES clients(id) ON DELETE RESTRICT,
        series TEXT NOT NULL,
        year INT NOT NULL,
        number INT NOT NULL,
        issue_date DATE NOT NULL,
        due_date DATE,
        currency TEXT NOT NULL DEFAULT 'RON',
        notes TEXT,
        subtotal NUMERIC(14,2) NOT NULL DEFAULT 0,
        vat_total NUMERIC(14,2) NOT NULL DEFAULT 0,
        total NUMERIC(14,2) NOT NULL DEFAULT 0,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE(company_id, series, year, number)
    );
    """)

    # Invoice lines
    db_exec("""
    CREATE TABLE IF NOT EXISTS invoice_lines (
        id SERIAL PRIMARY KEY,
        invoice_id INT NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
        line_no INT NOT NULL,
        description TEXT NOT NULL,
        unit TEXT NOT NULL DEFAULT 'buc',
        qty NUMERIC(14,3) NOT NULL,
        price NUMERIC(14,4) NOT NULL,
        vat_rate NUMERIC(5,2) NOT NULL DEFAULT 0,
        line_subtotal NUMERIC(14,2) NOT NULL,
        line_vat NUMERIC(14,2) NOT NULL,
        line_total NUMERIC(14,2) NOT NULL
    );
    """)

    return {"ok": True, "message": "DB initialized"}


@app.post("/admin/seed")
def admin_seed():
    ensure_db_url()

    # Inserăm firme doar dacă nu există deja
    db_exec("""
    INSERT INTO companies (name, cui, invoice_series)
    VALUES
      ('ROSIPROD SRL', 'RO9608452', 'ROS'),
      ('SILAI CEREAL COMPANY SRL', 'RO45698419', 'SCC'),
      ('OUAI DOLOSMANU', 'RO41291160', 'OD')
    ON CONFLICT DO NOTHING;
    """)
    return {"ok": True, "message": "Seed inserted"}


# -----------------------------
# Companies
# -----------------------------
@app.get("/companies")
def list_companies():
    rows = db_exec("SELECT id, name, cui, invoice_series, created_at FROM companies ORDER BY id").mappings().all()
    return list(rows)


# -----------------------------
# Clients
# -----------------------------
@app.get("/clients")
def list_clients(company_id: int = Query(...)):
    rows = db_exec(
        "SELECT * FROM clients WHERE company_id=:cid ORDER BY id",
        {"cid": company_id},
    ).mappings().all()
    return list(rows)

@app.post("/clients")
def create_client(payload: ClientCreate):
    try:
        row = db_exec("""
            INSERT INTO clients (company_id, name, cui, reg_com, address, vat_payer)
            VALUES (:company_id, :name, :cui, :reg_com, :address, :vat_payer)
            RETURNING *;
        """, payload.model_dump()).mappings().first()
        return dict(row)
    except IntegrityError:
        raise HTTPException(status_code=400, detail="Could not create client")


# -----------------------------
# Invoices
# -----------------------------
def next_invoice_number(conn, company_id: int, series: str, year: int) -> int:
    # blocare logică per (company, series, year) ca să nu dubleze numerele
    # advisory lock: cheie derivată numeric
    key = (company_id * 1000000) + (year * 100) + (sum(ord(c) for c in series) % 100)
    conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": key})

    r = conn.execute(
        text("""
            SELECT COALESCE(MAX(number), 0) AS max_no
            FROM invoices
            WHERE company_id=:company_id AND series=:series AND year=:year
        """),
        {"company_id": company_id, "series": series, "year": year},
    ).mappings().first()
    return int(r["max_no"]) + 1


@app.get("/invoices")
def list_invoices(company_id: int = Query(...)):
    rows = db_exec("""
        SELECT i.id, i.company_id, i.client_id, i.series, i.year, i.number,
               i.issue_date, i.due_date, i.currency, i.subtotal, i.vat_total, i.total,
               c.name AS client_name
        FROM invoices i
        JOIN clients c ON c.id=i.client_id
        WHERE i.company_id=:cid
        ORDER BY i.year DESC, i.number DESC;
    """, {"cid": company_id}).mappings().all()
    return list(rows)


@app.get("/invoices/{invoice_id}")
def get_invoice(invoice_id: int):
    inv = db_exec("""
        SELECT i.*, c.name AS client_name, c.cui AS client_cui, c.address AS client_address
        FROM invoices i
        JOIN clients c ON c.id=i.client_id
        WHERE i.id=:id
    """, {"id": invoice_id}).mappings().first()

    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")

    lines = db_exec("""
        SELECT * FROM invoice_lines
        WHERE invoice_id=:id
        ORDER BY line_no
    """, {"id": invoice_id}).mappings().all()

    return {"invoice": dict(inv), "lines": [dict(x) for x in lines]}


@app.post("/invoices")
def create_invoice(payload: InvoiceCreate):
    ensure_db_url()

    with engine.begin() as conn:
        # serie preluată din companies
        comp = conn.execute(
            text("SELECT invoice_series FROM companies WHERE id=:id"),
            {"id": payload.company_id},
        ).mappings().first()
        if not comp:
            raise HTTPException(status_code=400, detail="Invalid company_id")

        series = comp["invoice_series"]
        year = payload.issue_date.year
        number = next_invoice_number(conn, payload.company_id, series, year)

        # calcule
        subtotal = 0.0
        vat_total = 0.0
        computed_lines = []
        for idx, ln in enumerate(payload.lines, start=1):
            line_sub = float(ln.qty) * float(ln.price)
            line_vat = line_sub * float(ln.vat_rate) / 100.0
            line_tot = line_sub + line_vat
            subtotal += line_sub
            vat_total += line_vat
            computed_lines.append((idx, ln, line_sub, line_vat, line_tot))

        total = subtotal + vat_total

        inv = conn.execute(text("""
            INSERT INTO invoices (company_id, client_id, series, year, number, issue_date, due_date, currency, notes,
                                 subtotal, vat_total, total)
            VALUES (:company_id, :client_id, :series, :year, :number, :issue_date, :due_date, :currency, :notes,
                    :subtotal, :vat_total, :total)
            RETURNING id, company_id, client_id, series, year, number, issue_date, due_date, currency, subtotal, vat_total, total;
        """), {
            "company_id": payload.company_id,
            "client_id": payload.client_id,
            "series": series,
            "year": year,
            "number": number,
            "issue_date": payload.issue_date,
            "due_date": payload.due_date,
            "currency": payload.currency,
            "notes": payload.notes,
            "subtotal": round(subtotal, 2),
            "vat_total": round(vat_total, 2),
            "total": round(total, 2),
        }).mappings().first()

        for (line_no, ln, line_sub, line_vat, line_tot) in computed_lines:
            conn.execute(text("""
                INSERT INTO invoice_lines (invoice_id, line_no, description, unit, qty, price, vat_rate,
                                           line_subtotal, line_vat, line_total)
                VALUES (:invoice_id, :line_no, :description, :unit, :qty, :price, :vat_rate,
                        :line_subtotal, :line_vat, :line_total)
            """), {
                "invoice_id": inv["id"],
                "line_no": line_no,
                "description": ln.description,
                "unit": ln.unit,
                "qty": ln.qty,
                "price": ln.price,
                "vat_rate": ln.vat_rate,
                "line_subtotal": round(line_sub, 2),
                "line_vat": round(line_vat, 2),
                "line_total": round(line_tot, 2),
            })

        return {"ok": True, "invoice": dict(inv)}
