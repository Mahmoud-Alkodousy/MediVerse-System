# 🧠 MediVerse: Intelligent Hospital Management System

> **MediVerse** is an **AI-driven hospital management platform** that unifies patient tracking, predictive analytics, and real-time monitoring to enhance healthcare efficiency and safety.

---

## 📘 Overview

**MediVerse** is an integrated smart system designed to **digitally transform hospital operations**.  
It covers every stage of the patient journey — from admission to discharge — while leveraging **AI, IoT, and Deep Learning** to support medical teams with real-time insights.

The system automatically recognizes patients using **facial recognition cameras**, generates **electronic medical records (EMR)**, and retrieves their historical data, including medical tests and medications.

Additionally, MediVerse employs **biometric sensors** (e.g., ECG, PPG) integrated with **LSTM deep learning models** to analyze vital signs and **predict critical conditions early** before they occur.

---

## 🚀 Key Features

- 🧍‍♂️ **AI-Based Patient Identification** – Using facial recognition for secure and fast check-in.  
- 💾 **Electronic Medical Records (EMR)** – Centralized digital storage of patient data and medical history.  
- 🫀 **Real-Time Biometric Monitoring** – Continuous ECG and PPG signal analysis.  
- 🤖 **Predictive Health Analytics** – LSTM models for early detection of critical conditions.  
- 📊 **Interactive Dashboard** – Live insights for management and medical staff.  
- ⚙️ **Smart Resource Management** – Tracking hospital resources, costs, and workloads in real time.  
- 🔔 **Alerts & Notifications** – Automated alerts for abnormal conditions or urgent cases.

---

## 🧩 System Architecture

**MediVerse** integrates multiple technologies:
- **Frontend:** Streamlit / React (Dashboard)
- **Backend:** FastAPI / Flask
- **Database:** PostgreSQL / Supabase
- **Machine Learning:** PyTorch, Scikit-learn
- **Deep Learning:** LSTM Models (Vital Sign Prediction)
- **Computer Vision:** OpenCV, Face Recognition
- **IoT Integration:** ECG & PPG Sensors
- **Cloud:** Supabase, Render, or Azure App Service

---

## 🏗️ Database Design

The database follows a modular structure:
- `patients` → personal info & medical history  
- `records` → EMR data (tests, diagnosis, medications)  
- `vitals` → real-time biometric readings  
- `alerts` → predictive model outputs  
- `staff` → doctors, nurses, admin roles  
- `resources` → hospital equipment, rooms, beds  

*(You can include your ERD image here)*  
```markdown
![ERD](assets/erd.png)
