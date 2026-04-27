CREATE TABLE IF NOT EXISTS patients (
    id BIGSERIAL PRIMARY KEY,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    date_of_birth DATE NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS appointments (
    id BIGSERIAL PRIMARY KEY,
    patient_id BIGINT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    appointment_date DATE NOT NULL,
    appointment_time TIME NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (appointment_date, appointment_time)
);

INSERT INTO patients (first_name, last_name, date_of_birth)
SELECT 'Pau', 'Test', DATE '1996-09-02'
WHERE NOT EXISTS (
    SELECT 1
    FROM patients
    WHERE lower(first_name) = 'pau'
      AND lower(last_name) = 'test'
      AND date_of_birth = DATE '1996-09-02'
);
