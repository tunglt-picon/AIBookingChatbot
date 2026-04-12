"""
Thêm cột dental_case_code vào booking_consult_intakes (PostgreSQL).
Chạy một lần sau khi cập nhật model:

    cd backend && python scripts/add_dental_case_column.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from app.database import engine


async def main() -> None:
    ddl = text(
        "ALTER TABLE booking_consult_intakes "
        "ADD COLUMN IF NOT EXISTS dental_case_code VARCHAR NULL"
    )
    async with engine.begin() as conn:
        await conn.execute(ddl)
    print("OK: cột dental_case_code đã có trên booking_consult_intakes.")


if __name__ == "__main__":
    asyncio.run(main())
