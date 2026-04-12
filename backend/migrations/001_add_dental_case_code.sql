-- booking_consult_intakes: phân loại lý do khám cho đặt lịch (CAVITY | IMPLANT | ...)
ALTER TABLE booking_consult_intakes
    ADD COLUMN IF NOT EXISTS dental_case_code VARCHAR NULL;
