# 🏥 MediVerse - Smart Hospital Management System

## Project Structure
```
MediVerse/
├── main.py                          # FastAPI app entry point
├── config/
│   └── settings.py                  # Centralized configuration
├── database/
│   ├── connection.py                # Unified DB pool
│   ├── schema.sql                   # Full SQL Server schema
│   └── seed_data.sql                # Test data (doctors, manager, patients)
├── models/                          # Pydantic models
│   ├── patient.py, doctor.py, auth.py
│   ├── appointment.py, consultation.py, manager.py
├── routers/                         # API route handlers
│   ├── auth_router.py               # /auth/* (login, refresh, me)
│   ├── patient_router.py            # /patients/* (profile, history)
│   ├── appointment_router.py        # /appointments/*, /queue/*
│   ├── doctor_router.py             # /doctor/* (dashboard)
│   ├── manager_router.py            # /manager/* (dashboard)
│   └── health_router.py             # /health
├── services/                        # Business logic
│   ├── face_recognition_service.py  # Face recognition (Term 1)
│   ├── chatbot_service.py           # AI chatbot + RAG (Term 1)
│   ├── auth_service.py              # JWT + bcrypt
│   ├── appointment_service.py       # Booking + queue management
│   └── doctor_service.py            # Doctor dashboard logic
├── middleware/
│   ├── rate_limiter.py              # Rate limiting
│   └── request_logger.py            # Request logging
├── utils/
│   └── helpers.py                   # Utility functions
├── frontend/                        # HTML pages
│   ├── doctor-dashboard.html
│   ├── manager-dashboard.html
│   └── waiting-room.html
├── requirements.txt
├── .env.example
└── .gitignore
```

## Setup

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure Environment
```bash
cp .env.example .env
# Edit .env with your database credentials and API keys
```

### 3. Setup Database
Run in SQL Server Management Studio:
```sql
-- First: Create tables and views
-- Open and execute: database/schema.sql

-- Second: Insert test data
-- Open and execute: database/seed_data.sql
```

### 4. Run
```bash
python main.py
```
Server starts at: `http://127.0.0.1:8004`
API Docs: `http://127.0.0.1:8004/docs`

## Test Accounts (from seed_data.sql)

| Role    | Email                | Password    |
|---------|---------------------|-------------|
| Manager | admin@mediverse.com | admin123    |
| Doctor  | ahmed@mediverse.com | doctor123   |
| Doctor  | fatma@mediverse.com | doctor123   |
| Doctor  | sara@mediverse.com  | doctor123   |

> ⚠️ **Note:** The seed_data passwords use a placeholder bcrypt hash.
> For production, generate real hashes using the `/auth/doctor/login` registration flow
> or run: `python -c "from passlib.context import CryptContext; print(CryptContext(schemes=['bcrypt']).hash('your-password'))"`

## API Endpoints Summary

### Patient (Existing - Term 1)
- `POST /check-face` - Face recognition login
- `GET /check-national-id/{id}` - National ID lookup
- `POST /register-patient` - Register new patient
- `POST /chatbot/consultation` - AI symptom analysis
- `GET /chatbot/models` - Available AI models
- `GET /chatbot/doctors/{specialty}` - Doctors by specialty

### Patient (New)
- `GET /patients/{id}` - Patient profile
- `PUT /patients/{id}` - Update profile
- `GET /patients/{id}/history` - Consultation history
- `GET /patients/{id}/last-visit` - Last visit info

### Authentication (New)
- `POST /auth/doctor/login` - Doctor login → JWT
- `POST /auth/manager/login` - Manager login → JWT
- `POST /auth/refresh` - Refresh token
- `GET /auth/me` - Current user info

### Appointments & Queue (New)
- `GET /appointments/specialties` - All specialties
- `GET /appointments/doctors?specialty=X` - Doctors for booking
- `GET /appointments/doctors/{id}/slots?date=YYYY-MM-DD` - Time slots
- `POST /appointments/book` - Book appointment
- `POST /queue/join` - Join waiting queue
- `GET /queue/status/{id}` - Queue position (poll every 10s)
- `GET /queue/notifications/{patient_id}` - Turn notifications

### Doctor Dashboard (New - JWT Required)
- `GET /doctor/profile` - Own profile
- `PUT /doctor/status` - Update availability
- `GET /doctor/queue` - Current queue
- `POST /doctor/queue/next` - Call next patient
- `POST /doctor/queue/{id}/complete` - Complete consultation
- `POST /doctor/notes` - Add diagnosis/prescription
- `GET /doctor/stats` - Daily statistics

### Manager Dashboard (New - JWT Required)
- `GET /manager/dashboard` - Overview stats
- `GET /manager/doctors` - All doctors
- `POST /manager/doctors` - Add doctor
- `GET /manager/patients` - All patients (paginated)
- `GET /manager/queue/live` - Live queue view
- `GET /manager/reports/daily` - Daily report
- `GET /manager/reports/ai-accuracy` - AI accuracy stats
