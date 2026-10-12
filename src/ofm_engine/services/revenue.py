"""Revenue CSV import (OnlyFans / Infloww / Supercreator exports) with column mapping."""

from __future__ import annotations

import csv
import io
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..hashing import sha256
from ..models import Creator, RevenueRecord
from ..tenant_config import RevenueMapping
from . import jobs
from .kpis import invalidate
from .live import record_activity

MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 50_000


@dataclass
class ImportResult:
    imported: int = 0
    duplicates: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)
    revenue_cents: int = 0
    subs: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {"imported": self.imported, "skipped_duplicates": self.duplicates, "errors": self.errors[:50],
                "error_count": len(self.errors), "revenue_cents": self.revenue_cents, "subs": self.subs}


class ImportError_(ValueError):
    pass


def _money(raw: str) -> float:
    v = (raw or "").strip().replace("$", "").replace(",", "").replace("USD", "").strip()
    if v.startswith("(") and v.endswith(")"):
        v = "-" + v[1:-1]
    return float(v) if v else 0.0


def _strip_formula(v: str) -> str:
    # never echo spreadsheet formulas back (CSV injection)
    return v.lstrip("=+-@\t\r") if v[:1] in ("=", "+", "-", "@", "\t", "\r") else v


def import_csv(db: Session, tenant_id: uuid.UUID, content: bytes, mapping: RevenueMapping,
               source_file: str = "upload.csv") -> ImportResult:
    if len(content) > MAX_BYTES:
        raise ImportError_("file too large (max 5 MB)")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ImportError_("file must be UTF-8 CSV") from exc
    reader = csv.DictReader(io.StringIO(text))
    headers = [h.strip() for h in (reader.fieldnames or [])]
    reader.fieldnames = headers
    for required in (mapping.date, mapping.gross):
        if required not in headers:
            raise ImportError_(f"column {required!r} not found; columns: {', '.join(headers)[:300]}")
    creators = {c.slug.lower(): c.id for c in db.scalars(select(Creator).where(
        Creator.tenant_id == tenant_id, Creator.deleted_at.is_(None))).all()}
    creators.update({c.display_name.lower(): c.id for c in db.scalars(select(Creator).where(
        Creator.tenant_id == tenant_id, Creator.deleted_at.is_(None))).all()})
    existing = set(db.scalars(select(RevenueRecord.row_hash).where(RevenueRecord.tenant_id == tenant_id)).all())
    res = ImportResult()
    for i, row in enumerate(reader, start=2):
        if i - 1 > MAX_ROWS:
            res.errors.append({"row": i, "message": "row limit reached"})
            break
        try:
            day = datetime.strptime((row.get(mapping.date) or "").strip(), mapping.date_format).date()
            gross = _money(row.get(mapping.gross, ""))
            net_raw = row.get(mapping.net) if mapping.net in headers else None
            net = _money(net_raw) if net_raw not in (None, "") else round(gross * mapping.net_ratio_if_missing, 2)
            subs_raw = (row.get(mapping.new_subs) or "0").strip() if mapping.new_subs in headers else "0"
            subs = int(float(subs_raw or 0))
        except (ValueError, TypeError) as exc:
            res.errors.append({"row": i, "message": str(exc)[:200]})
            continue
        creator_key = _strip_formula((row.get(mapping.creator) or "").strip()) if mapping.creator in headers else ""
        campaign = _strip_formula((row.get(mapping.campaign) or "").strip())[:120] if mapping.campaign in headers else ""
        row_hash = sha256(f"{tenant_id}|{day}|{creator_key.lower()}|{campaign}|{gross}|{net}|{subs}")
        if row_hash in existing:
            res.duplicates += 1
            continue
        existing.add(row_hash)
        db.add(RevenueRecord(tenant_id=tenant_id, creator_id=creators.get(creator_key.lower()), day=day,
                             campaign=campaign, gross=gross, net=net, new_subs=subs, source_file=source_file[:200],
                             row_hash=row_hash))
        res.imported += 1
        res.revenue_cents += int(round(gross * 100))
        res.subs += subs
    db.flush()
    if res.imported:
        payload = {"type": "revenue_import", "imported": res.imported, "revenue_cents": res.revenue_cents,
                   "subs": res.subs}
        record_activity(db, tenant_id, "revenue_import", payload)
        jobs.emit_event(db, tenant_id, "revenue.imported", payload, uuid.uuid4().hex)
        invalidate(tenant_id)
    return res
