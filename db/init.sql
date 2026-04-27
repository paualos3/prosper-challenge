-- Create patients table
CREATE TABLE IF NOT EXISTS patients (
    id BIGSERIAL PRIMARY KEY,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    date_of_birth DATE NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Create appointments table
CREATE TABLE IF NOT EXISTS appointments (
    id BIGSERIAL PRIMARY KEY,
    patient_id BIGINT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    appointment_date DATE NOT NULL,
    appointment_time TIME NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (appointment_date, appointment_time)
);

-- Insert base demo patient (Pau Test) if not exists
INSERT INTO patients (first_name, last_name, date_of_birth)
SELECT 'Pau', 'Test', DATE '1996-09-02'
WHERE NOT EXISTS (
    SELECT 1
    FROM patients
    WHERE lower(first_name) = 'pau'
      AND lower(last_name) = 'test'
      AND date_of_birth = DATE '1996-09-02'
);

-- Insert additional demo patients if not exist
INSERT INTO patients (first_name, last_name, date_of_birth)
SELECT v.first_name, v.last_name, v.dob
FROM (
    VALUES
        ('Maria', 'Garcia', DATE '1985-03-14'),
        ('John', 'Doe', DATE '1990-07-22'),
        ('Lucia', 'Martinez', DATE '2001-11-05'),
        ('Carlos', 'Sanchez', DATE '1978-01-30'),
        ('Anna', 'Lopez', DATE '1995-06-18')
) AS v(first_name, last_name, dob)
WHERE NOT EXISTS (
    SELECT 1
    FROM patients p
    WHERE lower(p.first_name) = lower(v.first_name)
      AND lower(p.last_name) = lower(v.last_name)
      AND p.date_of_birth = v.dob
);

-- Insert multiple appointments (past and future) for Pau Test
WITH target_patient AS (
    SELECT id
    FROM patients
    WHERE lower(first_name) = 'pau'
      AND lower(last_name) = 'test'
    LIMIT 1
)
INSERT INTO appointments (patient_id, appointment_date, appointment_time)
SELECT tp.id, v.app_date, v.app_time
FROM target_patient tp
CROSS JOIN (
    VALUES
        (CURRENT_DATE - INTERVAL '10 days', TIME '09:00'), -- past
        (CURRENT_DATE - INTERVAL '2 days',  TIME '15:30'), -- recent past
        (CURRENT_DATE + INTERVAL '3 days',  TIME '11:00'), -- near future
        (CURRENT_DATE + INTERVAL '15 days', TIME '08:45')  -- future
) AS v(app_date, app_time)
WHERE NOT EXISTS (
    SELECT 1
    FROM appointments a
    WHERE a.appointment_date = v.app_date
      AND a.appointment_time = v.app_time
);

-- Insert upcoming appointments for Maria Garcia
WITH another_patient AS (
    SELECT id
    FROM patients
    WHERE lower(first_name) = 'maria'
      AND lower(last_name) = 'garcia'
    LIMIT 1
)
INSERT INTO appointments (patient_id, appointment_date, appointment_time)
SELECT ap.id, v.app_date, v.app_time
FROM another_patient ap
CROSS JOIN (
    VALUES
        (CURRENT_DATE + INTERVAL '1 day', TIME '10:15'),
        (CURRENT_DATE + INTERVAL '7 days', TIME '14:00')
) AS v(app_date, app_time)
WHERE NOT EXISTS (
    SELECT 1
    FROM appointments a
    WHERE a.appointment_date = v.app_date
      AND a.appointment_time = v.app_time
);