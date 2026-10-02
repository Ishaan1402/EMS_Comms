# 🏥 Asclepius EMT System

An AI-powered emergency triage system that enables EMTs to record patient conversations, automatically classify risk levels, prioritize cases by urgency, and instantly notify doctors for faster emergency response.

## 🐍 Python/FastAPI Backend Available

This repository now includes a **Python/FastAPI backend** as an alternative to the Node/Express backend. Both backends are functionally identical and maintain full API compatibility.

- **Node/Express** (original): `npm start` or `node server.js`
- **Python/FastAPI** (new): `python3 main.py` or `./start-python.sh`

See [PYTHON_BACKEND.md](PYTHON_BACKEND.md) for complete Python backend documentation.

---

## ✨ Features

### For EMTs/Paramedics
- **Audio Recording**: Record patient conversations directly from mobile devices
- **Patient Information**: Add context and details about the emergency
- **AI Processing**: Automatic transcription and medical summary generation
- **Real-time Updates**: Track processing status and urgency levels

### For Doctors
- **Instant Notifications**: Receive SMS/email alerts for new cases
- **AI Summaries**: Get structured patient information and urgency assessment
- **Response System**: Provide medical guidance and instructions
- **Case Management**: View and manage all incoming emergency cases

### Technical Features
- **Secure Authentication**: JWT-based user management with role-based access
- **Real-time Processing**: OpenAI Whisper for transcription + GPT-4 for analysis
- **Multi-channel Notifications**: SMS (Twilio) + Email (SendGrid) integration
- **Responsive Design**: Mobile-first interface for field use
- **HIPAA Compliant**: Secure data handling and storage
- **SQLite Database**: Simple, file-based database for easy deployment

## 🏗️ Architecture

```
EMT Phone → Audio Recording → Backend API → OpenAI Whisper → GPT-4 Analysis → Doctor Notification
    ↓              ↓              ↓              ↓              ↓              ↓
Web Interface → File Upload → SQLite Database → Transcription → Medical Summary → SMS/Email
```

## 🚀 Quick Start

### Prerequisites
- Node.js 16+ 
- OpenAI API key
- Twilio account (for SMS)
- SendGrid account (for email)

### 1. Clone & Install
```bash
git clone <repository-url>
cd shealthcare
npm install
cd client && npm install
```

### 2. Environment Setup
```bash
cp env.example .env
# Edit .env with your API keys
```

### 3. Start Development
```bash
# Terminal 1: Backend
npm run dev

# Terminal 2: Frontend
cd client && npm start
```

### 4. Access the App
- **Frontend**: http://localhost:3000
- **Backend**: http://localhost:5000
- **Demo Accounts**: See login page for test credentials
- **Database**: Automatically created as `shealthcare.db`

## 🔧 Configuration

### Environment Variables
```env
# JWT Secret
JWT_SECRET=your-super-secret-jwt-key-here

# OpenAI API
OPENAI_API_KEY=your-openai-api-key-here

# Twilio (SMS)
TWILIO_ACCOUNT_SID=your-twilio-account-sid
TWILIO_AUTH_TOKEN=your-twilio-auth-token
TWILIO_PHONE_NUMBER=+1234567890

# SendGrid (Email)
SENDGRID_API_KEY=your-sendgrid-api-key
SENDGRID_FROM_EMAIL=noreply@shealthcare.com
```

## 📱 Usage

### EMT Workflow
1. **Login** with EMT credentials
2. **Record** patient conversation using microphone
3. **Add** patient information and context
4. **Upload** for AI processing
5. **Monitor** processing status and results

### Doctor Workflow
1. **Login** with doctor credentials
2. **Receive** emergency notifications
3. **Review** AI-generated patient summaries
4. **Respond** with medical guidance
5. **Track** case status and history

## 🛠️ API Endpoints

### Authentication
- `POST /api/auth/register` - User registration
- `POST /api/auth/login` - User login
- `GET /api/auth/profile` - Get user profile

### Recordings
- `POST /api/recordings/upload` - Upload audio recording
- `GET /api/recordings/my-recordings` - Get EMT's recordings
- `GET /api/recordings/:id` - Get specific recording

### Doctors
- `GET /api/doctors/notifications` - Get doctor notifications
- `GET /api/doctors/recording/:id` - Get recording details
- `PATCH /api/doctors/notifications/:id/read` - Mark notification as read
- `POST /api/doctors/recordings/:id/respond` - Send medical response

### Notifications
- `POST /api/notifications/send` - Send notification to doctor
- `GET /api/notifications/status/:id` - Get notification status

## 🔒 Security Features

- **JWT Authentication** with secure token management
- **Role-based Access Control** (EMT vs Doctor)
- **Input Validation** and sanitization
- **CORS Protection** and security headers
- **File Upload Security** with type validation
- **SQLite Query Protection** against injection attacks

## 🚀 Deployment

### Heroku
```bash
# Backend
heroku create your-app-name
git push heroku main

# Frontend
cd client
npm run build
```

### Docker
```bash
docker-compose up -d
```

### Local Development
```bash
# Simple startup
chmod +x start.sh
./start.sh
```

## 📊 Performance

- **Audio Processing**: ~30-60 seconds for typical recordings
- **LLM Analysis**: ~10-20 seconds for medical summary
- **Notification Delivery**: <5 seconds for SMS/email
- **Database Queries**: Fast with SQLite indexing

## 🔮 Future Enhancements

- **Mobile Apps**: Native iOS/Android applications
- **Real-time Chat**: Live communication between EMTs and doctors
- **Image Support**: Photo uploads for visual assessment
- **GPS Integration**: Location-based doctor matching
- **Advanced AI**: Predictive analytics and triage scoring
- **Hospital Integration**: EMR system connectivity
- **PostgreSQL Migration**: For production scaling

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 🆘 Support

For support and questions:
- Create an issue in the repository
- Contact the development team
- Check the documentation

---

**Built with ❤️ for emergency medical professionals** 
## 🎯 **New Risk Classification Features**

### **AI-Powered Risk Assessment**
- **Risk Score (0-100)**: Automated calculation based on symptoms, vital signs, and urgency
- **Priority Level (1-5)**: Intelligent classification from critical to non-urgent
- **Smart Triage**: Cases automatically ordered from highest to lowest risk
- **Real-time Prioritization**: Doctors see most critical cases first

### **Enhanced Medical Analysis**
- **Chief Complaint**: Primary reason for emergency call
- **Vital Signs**: Comprehensive vital sign analysis
- **Symptom Assessment**: Detailed symptom categorization
- **Recommended Actions**: AI-suggested immediate interventions
- **Critical Information**: Key details for medical decision-making

### **Priority Levels**
1. **Critical (Priority 1)**: Immediate life threat, <5 minutes
2. **High (Priority 2)**: Urgent, <30 minutes
3. **Medium (Priority 3)**: Moderate urgency, <2 hours
4. **Low (Priority 4)**: Routine, <24 hours
5. **Non-urgent (Priority 5)**: Scheduled care

