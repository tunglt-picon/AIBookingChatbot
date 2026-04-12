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
    needs_visit: bool,
    dental_case_code: str | None = None,
) -> str:
    """
    Save a booking consult intake (symptoms text + intake summary fields) to the database.

    Args:
        patient_user_id: Patient user id.
        session_id: Chat session id.
        symptoms: Free-text symptom / reason-for-visit summary.
        ai_diagnosis: Short intake note (must not be used as medical advice).
        needs_visit: Whether the flow recommends an in-person visit.

    Returns:
        JSON string containing the new intake id.
    """
    from app.database import async_session_factory
    from app.models.reservation import BookingConsultIntake

    logger.info(
        "[tool] save_consult_intake session_id=%s patient_user_id=%s needs_visit=%s",
        session_id,
        patient_user_id,
        needs_visit,
    )
    async with async_session_factory() as db:
        intake = BookingConsultIntake(
            patient_user_id=patient_user_id,
            session_id=session_id,
            symptoms=symptoms,
            ai_diagnosis=ai_diagnosis,
            needs_visit=needs_visit,
            dental_case_code=dental_case_code,
        )
        db.add(intake)
        await db.commit()
        await db.refresh(intake)

        return json.dumps({
            "intake_id": intake.id,
            "needs_visit": intake.needs_visit,
            "symptoms": intake.symptoms,
        }, ensure_ascii=False)
