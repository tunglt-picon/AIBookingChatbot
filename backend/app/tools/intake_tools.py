"""
Intake tools – persist booking consultation intake records to the database.
"""

import json
import logging

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


@tool
async def save_consult_intake(
    patient_user_id: int,
    session_id: int,
    symptoms: str,
    ai_diagnosis: str,
    category_code: str | None = None,
    needs_visit: bool = True,
) -> str:
    """
    Save a booking consult intake (symptoms + intake summary) to the database.

    Args:
        patient_user_id: Patient user id.
        session_id: Chat session id.
        symptoms: Free-text symptom summary.
        ai_diagnosis: Short intake note (not medical advice).
        category_code: CAT-01 | CAT-02 | CAT-03 | CAT-04 | CAT-05.
        needs_visit: DB compat — luôn True (mọi BN đều hướng tới booking).

    Returns:
        JSON string containing the new intake id.
    """
    from app.database import async_session_factory
    from app.models.reservation import BookingConsultIntake

    logger.info(
        "[tool] save_consult_intake session_id=%s patient=%s needs_visit=%s category=%s",
        session_id, patient_user_id, needs_visit, category_code,
    )
    async with async_session_factory() as db:
        intake = BookingConsultIntake(
            patient_user_id=patient_user_id,
            session_id=session_id,
            symptoms=symptoms,
            ai_diagnosis=ai_diagnosis,
            needs_visit=needs_visit,
            dental_case_code=category_code,
        )
        db.add(intake)
        await db.commit()
        await db.refresh(intake)

        return json.dumps({
            "intake_id": intake.id,
            "needs_visit": intake.needs_visit,
            "symptoms": intake.symptoms,
        }, ensure_ascii=False)
