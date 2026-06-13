# MediVerse — Smart Medical UniVerse

**MediVerse** is an AI-powered backend for a modern hospital management system. It combines biometric patient identification, an AI medical consultation chatbot (RAG), real-time appointment & queue management, a pharmacy AI agent, and AI-based medical image analysis — all served through a single FastAPI application.

Built as a graduation project, MediVerse powers a multi-platform ecosystem: a **React** web dashboard (doctors & managers), a **Flutter** mobile app (patients), and this **FastAPI** backend on **MS SQL Server**.

---

## ✨ Key Features

### 🪪 Patient Identification & Records
- Face recognition check-in using **InsightFace (AuraFace)** embeddings
- National ID lookup
- Biometric patient registration (face embedding + medical profile)
- Full medical history, last visit summary, and chat session history

### 🤖 AI Medical Consultation Chatbot
- Retrieval-Augmented Generation (RAG) consultation pipeline
- Multi-LLM support via **OpenRouter** (GPT-4o / GPT-4o Mini, Claude 3.5 Sonnet, Claude 3 Haiku, Gemini Pro 1.5, Llama 3.1 70B, Mixtral 8x7B)
- Automatic severity assessment & specialty routing to available doctors
- Drug safety / allergy cross-checking
- Multilingual semantic search via `sentence-transformers`
- Conversation caching, metrics, and health checks

### 📅 Appointments & Live Queue
- Browse specialties, doctors, and available time slots
- Book / cancel appointments
- Join and track a live walk-in queue
- Automatic queue insertion when a scheduled appointment's time arrives (background job)
- **WebSocket** push updates (`/ws/patient/{id}`) for the Flutter app

### 👨‍⚕️ Doctor Dashboard
- Profile & online status management
- Queue control: call next patient, complete, mark no-show
- Patient lookup, clinical notes, consultation stats
- View own uploaded medical file analyses

### 🏢 Manager / Admin Dashboard
- System-wide dashboard & analytics
- Doctor CRUD (create, edit, activate/deactivate)
- Live queue monitoring across all doctors
- Reports: daily summary, AI diagnostic accuracy, patient flow, revenue, common diseases
- System logs, API request logs & statistics, device tracking

### 📁 Medical Files & AI Image Analysis
- Upload X-rays, lab results, and scan reports
- Automated analysis via vision-capable LLMs (**Qwen2.5-VL-72B**, with **Gemini 2.0 Flash** fallback) per file type

### 💊 Pharmacy AI Agent
- Symptom-based drug recommendations tailored to patient profile
- Prescription image analysis (multi-pass OCR → DB matching → safety analysis), with streaming support
- Drug-to-drug interaction checking
- Alternative medicine suggestions
- Semantic drug search powered by precomputed embeddings
- Conversational pharmacy chatbot

### 🔐 Authentication & Authorization
- JWT-based access & refresh tokens
- Role-based access: `doctor`, `manager`, `admin`, `super_admin`, `patient`

### ⚙️ Platform & Reliability
- Rate limiting middleware
- Structured request logging middleware
- `/health` and `/health/full` health-check endpoints
- Background cleanup of temporary upload files
- Static serving of medical files and a frontend bundle

---

## 🛠 Tech Stack

| Layer                  | Technology                                                                 |
|------------------------|-----------------------------------------------------------------------------|
| API Framework          | FastAPI, Uvicorn                                                            |
| Database               | Microsoft SQL Server (via `pyodbc` + SQLAlchemy)                            |
| Authentication         | JWT (`python-jose`), `bcrypt`                                               |
| Face Recognition       | InsightFace (AuraFace model)                                                |
| LLM Gateway            | OpenRouter (GPT-4o, Claude, Gemini, Llama, Mixtral, Qwen2.5-VL)             |
| Embeddings / RAG       | `sentence-transformers` (`paraphrase-multilingual-MiniLM-L12-v2`)           |
| Agentic Layer          | LangGraph, LangChain Core                                                   |
| Real-time              | WebSockets (FastAPI native)                                                 |
| Containerization       | Docker, Docker Compose                                                      |
| Validation             | Pydantic v2                                                                 |

---

## 📂 Project Structure

```
mediverse-backend/
├── main.py                     # App entrypoint, lifespan, legacy endpoints, WebSocket
├── config/
│   └── settings.py              # Centralized configuration (env-driven)
├── database/
│   ├── connection.py            # DB connection pooling (pyodbc + SQLAlchemy)
│   ├── schema.sql                # Full database schema
│   ├── seed_data.sql / demo_data.sql
│   └── migrate_doctor_fields.sql
├── middleware/
│   ├── rate_limiter.py
│   └── request_logger.py
├── models/                       # Pydantic schemas
│   ├── patient.py, doctor.py, appointment.py
│   ├── consultation.py, manager.py, auth.py
├── routers/                       # API route definitions
│   ├── auth_router.py
│   ├── patient_router.py
│   ├── appointment_router.py
│   ├── doctor_router.py
│   ├── manager_router.py
│   ├── medical_files_router.py
│   ├── pharmacy_router.py
│   └── health_router.py
├── services/                       # Business logic
│   ├── face_recognition_service.py
│   ├── chatbot_service.py          # RAG + multi-LLM consultation engine
│   ├── appointment_service.py
│   ├── doctor_service.py
│   ├── pharmacy_service.py          # Pharmacy AI agent
│   ├── prescription_service.py      # Prescription OCR pipeline
│   ├── medical_analysis_service.py  # Vision-LLM medical image analysis
│   └── auth_service.py
├── utils/helpers.py
├── test_all_apis.py
├── regenerate_embeddings.py
├── requirements.txt
├── Dockerfile
└── docker-compose.yml
```

---

## 🗄 Database Schema

MediVerse runs on **MS SQL Server**. Key tables defined in `database/schema.sql`:

| Table                   | Purpose                                              |
|-------------------------|-------------------------------------------------------|
| `Patients`               | Patient records & biometric data                     |
| `doctors`                | Doctor profiles, specialty, status, schedule         |
| `managers`               | Manager/admin accounts                               |
| `appointments`           | Booked appointments                                  |
| `appointment_queue`       | Live walk-in / scheduled queue state                 |
| `chat_sessions` / `chat_messages` | AI consultation chat history                |
| `patient_consultations`   | Consultation results & AI assessments               |
| `drug_interactions`        | Drug interaction reference data                     |
| `allergy_database`         | Allergy reference data                              |
| `doctor_notes`              | Clinical notes per patient                          |
| `medical_files`             | Uploaded files + AI analysis results               |
| `notifications`             | Patient/doctor notifications                       |
| `system_logs`               | Application logs                                   |
| `PatientAccessLog`           | Audit log of patient record access                |
| `api_logs`                    | Request-level API logs                            |

---

## 🚀 Getting Started

### Prerequisites
- Python **3.11+**
- Microsoft SQL Server (local, remote, or via Docker)
- ODBC Driver 17 for SQL Server
- An [OpenRouter](https://openrouter.ai) API key (for chatbot, pharmacy, and vision features)

### 1. Clone & install dependencies
```bash
git clone <repo-url>
cd mediverse-backend
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure environment variables
Copy `.env.example` to `.env` and fill in the values:

| Variable                          | Description                                          |
|------------------------------------|--------------------------------------------------------|
| `DATABASE_SERVER`                   | SQL Server host                                       |
| `DATABASE_NAME`                     | Database name                                         |
| `DATABASE_USER` / `DATABASE_PASSWORD` | SQL Server credentials                              |
| `DATABASE_DRIVER`                    | ODBC driver name (default: `ODBC Driver 17 for SQL Server`) |
| `JWT_SECRET_KEY`                       | Secret key for signing JWTs                          |
| `JWT_ALGORITHM`                        | JWT signing algorithm (default: `HS256`)            |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES`        | Access token lifetime                              |
| `JWT_REFRESH_TOKEN_EXPIRE_DAYS`           | Refresh token lifetime                            |
| `OPENROUTER_API_KEY`                       | API key for OpenRouter (LLM gateway)              |
| `OPENROUTER_MODEL`                          | Default chatbot model                            |
| `FACE_SIMILARITY_THRESHOLD`                  | Face match confidence threshold                  |
| `FACE_MODEL_PATH`                              | Path to AuraFace model files                   |
| `EMBED_MODEL_NAME`                              | Sentence-transformers model for RAG embeddings |
| `ENVIRONMENT`                                    | `dev` / `production`                          |
| `LOG_LEVEL`                                       | Logging level (e.g. `INFO`)                  |
| `RATE_LIMIT_CALLS` / `RATE_LIMIT_PERIOD`           | Rate-limit configuration                    |

### 3. Set up the database
Run the schema (and optionally seed/demo data) against your SQL Server instance:
```bash
sqlcmd -S <server> -d master -i database/schema.sql
sqlcmd -S <server> -d MediVerse_System -i database/seed_data.sql
```

### 4. Build pharmacy & RAG embeddings (first run)
```bash
python regenerate_embeddings.py
```

### 5. Run the API
```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8004
```
- Interactive docs: `http://localhost:8004/docs`
- ReDoc: `http://localhost:8004/redoc`

---

## 🐳 Running with Docker

The included `docker-compose.yml` spins up both the API and a SQL Server 2022 container:

```bash
docker-compose up --build
```

- API → `http://localhost:8000`
- SQL Server → `localhost:1433`

The API container automatically waits for the database to be healthy before starting.

---

## 📡 API Overview

> Full interactive documentation is available at `/docs` (Swagger UI) and `/redoc` once the server is running.

### Core
| Method | Endpoint                  | Description                          |
|--------|----------------------------|----------------------------------------|
| GET    | `/`                          | API info & endpoint index             |
| GET    | `/health`, `/health/full`     | Health checks                        |
| POST   | `/check-face`                  | Identify patient by face            |
| GET    | `/check-national-id/{id}`       | Identify patient by national ID    |
| POST   | `/register-patient`              | Register new patient (face + data)|

### Authentication (`/auth`)
| Method | Endpoint              | Description                  |
|--------|-------------------------|----------------------------------|
| POST   | `/auth/login`             | Generic login                  |
| POST   | `/auth/doctor/login`        | Doctor login                  |
| POST   | `/auth/manager/login`        | Manager/admin login           |
| POST   | `/auth/patient-login`         | Patient login                |
| POST   | `/auth/refresh`                 | Refresh access token        |
| GET    | `/auth/me`                       | Current user info          |
| POST   | `/auth/logout`                    | Logout                    |

### AI Consultation (`/chatbot`)
| Method | Endpoint                          | Description                      |
|--------|-------------------------------------|--------------------------------------|
| POST   | `/chatbot/consultation`               | Run AI consultation (RAG + LLM)    |
| GET    | `/chatbot/doctors/{specialty}`         | Available doctors per specialty   |
| GET    | `/chatbot/models`                       | List supported LLMs              |

### Patients
| Method | Endpoint                                       | Description                  |
|--------|---------------------------------------------------|----------------------------------|
| GET/PUT| `/patients/{id}`                                    | Get / update patient profile   |
| GET    | `/patients/{id}/history`                              | Full medical history          |
| GET    | `/patients/{id}/last-visit`                            | Last visit summary           |
| GET    | `/patients/{id}/chat-sessions`                           | Chat session list            |
| GET    | `/patients/{id}/chat-sessions/{session_id}`               | Chat session detail         |
| GET    | `/patients/{id}/appointments`                               | Patient's appointments      |

### Appointments & Queue
| Method | Endpoint                                | Description                       |
|--------|--------------------------------------------|----------------------------------------|
| GET    | `/appointments/specialties`                   | List specialties                     |
| GET    | `/appointments/doctors`                         | List doctors                        |
| GET    | `/appointments/doctors/{id}/slots`               | Available time slots               |
| POST   | `/appointments/book`                              | Book an appointment               |
| GET/PUT| `/appointments/{id}`, `/appointments/{id}/cancel`  | Get / cancel appointment          |
| POST   | `/queue/join`                                       | Join the live queue              |
| GET    | `/queue/status/{queue_id}`                           | Queue status                    |
| GET    | `/queue/patient/{id}/active`                          | Patient's active queue entry   |
| PUT    | `/queue/{queue_id}/cancel`                             | Leave the queue                |
| GET    | `/queue/notifications/{patient_id}`                     | Queue notifications           |

### Doctor Dashboard (`/doctor`)
| Method | Endpoint                          | Description                       |
|--------|--------------------------------------|----------------------------------------|
| GET/PUT| `/doctor/profile`                      | View / update profile              |
| PUT    | `/doctor/status`                         | Set online/offline status         |
| GET    | `/doctor/queue`                            | Current queue                    |
| POST   | `/doctor/queue/next`                         | Call next patient               |
| POST   | `/doctor/queue/{id}/complete`                  | Mark consultation complete     |
| POST   | `/doctor/queue/{id}/no-show`                     | Mark patient as no-show       |
| GET    | `/doctor/patient/{id}`                            | View patient details         |
| POST   | `/doctor/notes`                                     | Add clinical note           |
| GET    | `/doctor/stats`                                       | Consultation statistics    |
| GET    | `/doctor/my-uploads`                                    | Own uploaded file analyses|

### Manager Dashboard (`/manager`)
| Method | Endpoint                                  | Description                       |
|--------|----------------------------------------------|----------------------------------------|
| GET    | `/manager/dashboard`                            | Overview dashboard                  |
| GET/POST/PUT| `/manager/doctors`, `/manager/doctors/{id}` | Manage doctors                     |
| PUT    | `/manager/doctors/{id}/activate`                  | Activate/deactivate doctor        |
| GET    | `/manager/patients`                                 | List all patients                |
| GET    | `/manager/queue/live`                                 | Live queue across all doctors  |
| GET    | `/manager/reports/daily`                                | Daily summary report          |
| GET    | `/manager/reports/ai-accuracy`                            | AI diagnostic accuracy       |
| GET    | `/manager/reports/patient-flow`                              | Patient flow analytics      |
| GET    | `/manager/reports/today-revenue`                               | Today's revenue            |
| GET    | `/manager/reports/common-diseases`                                | Common diagnoses report   |
| GET    | `/manager/logs`, `/manager/api-logs*`                                | System & API logs        |

### Medical Files (`/medical-files`)
| Method | Endpoint                          | Description                       |
|--------|--------------------------------------|----------------------------------------|
| POST   | `/medical-files/upload`                | Upload a medical file                |
| POST   | `/medical-files/analyze`                 | AI-analyze a medical file (vision LLM)|
| GET    | `/medical-files/patient/{id}`              | List files for a patient            |
| GET/DELETE| `/medical-files/{file_id}`               | Get / delete a file                 |

### Pharmacy Agent (`/pharmacy`)
| Method | Endpoint                                | Description                       |
|--------|--------------------------------------------|----------------------------------------|
| POST   | `/pharmacy/recommend`                          | Symptom-based drug recommendation    |
| POST   | `/pharmacy/prescription/analyze`                | Analyze a prescription image        |
| POST   | `/pharmacy/prescription/analyze-stream`           | Streaming prescription analysis    |
| POST   | `/pharmacy/alternative`                              | Suggest alternative medicines     |
| POST   | `/pharmacy/drug/search`                                | Semantic drug search             |
| POST   | `/pharmacy/drug/interactions`                            | Check drug interactions          |
| POST   | `/pharmacy/embeddings/build`, GET `/pharmacy/embeddings/status` | Manage drug embeddings index |
| POST   | `/pharmacy/chat`                                              | Pharmacy chatbot               |

### WebSocket
| Endpoint                       | Description                                              |
|----------------------------------|--------------------------------------------------------------|
| `ws://<host>/ws/patient/{patient_id}` | Real-time queue position & status updates for the Flutter app |

---

## 🧪 Testing

A test script covering all major endpoints is included:
```bash
python test_all_apis.py
```

---

## 👥 Team

MediVerse was developed as a graduation project as part of a full-stack AI-powered hospital management ecosystem (FastAPI backend, React web dashboard, and Flutter mobile app).

---

## 📄 License

This project was developed for academic purposes as part of a graduation project.
