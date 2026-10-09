#Offline Guru - A (No Router, No Internet)

### Live Translation Classroom - Teacher speaks in English, Students get Bangla instantly (0.5s delay)

> No internet in rural schools? No problem. One laptop can teach 30 students live in Bangla, without a router.

---

### 1. What is it?

- **No Router Needed**
- **No Internet Needed**
- Teacher's laptop creates a Hotspot -> It becomes both Router + Server
- Students just connect to that WiFi -> Get Live Bangla Text + Voice

Teacher Mic (English) -> Whisper Live -> Gemma 2B (Simple Bangla) -> WebSocket -> Student Laptops (Bangla Text + Voice)

### 2. How it Works (Architecture)

**Teacher Laptop (Server - 192.168.137.1)**
- `Faster-Whisper (Streaming)` - Listens every 1 sec and converts to text
- `Ollama Gemma 2B (Streaming)` - Translates live, word-by-word to simple Bangla
    - Prompt: "You are a live translator. Translate to simple, motherly Bangla. Don't wait for full sentence. Make it easy for a 10-year-old."
- `FastAPI + WebSocket` - Sends translated text to all clients without internet

**Student Laptop (Client)**
- Just a Browser - `http://192.168.137.1:8000/student`
- Connects via WebSocket, shows typewriter effect + plays Bangla voice via Piper TTS (offline)

### 3. Setup - Start Class in 5 Minutes

**Step 1: On Teacher Laptop**
```bash
# Turn On Hotspot
Settings > Network > Mobile Hotspot > On
Name: OptiLearn-Live
Password: 12345678
Keep Mobile Data OFF - hotspot will still work

# Check IP
# Open CMD and type:
ipconfig
# Your IP will be: 192.168.137.1

# Install & Run Server
pip install faster-whisper fastapi uvicorn websockets
ollama pull gemma2:2b
python server.py
*Step 2: On Student Laptop*
1. Connect to WiFi: OptiLearn-Live / 12345678
2. Open Browser: http://192.168.137.1:8000/student
3. Done! As soon as teacher speaks, you will see/hear Bangla.
### 4. Key Features

- *LIVE:* 0.5s delay, teacher doesn't need to stop
- *100% OFFLINE:* No data goes outside, fully private
- *3-in-1:* Translates + Speaks + Shows Text
- *Doubt Button:* Student can press "Didn't understand" privately, only teacher sees it. No shame.
- *Auto Notes:* Entire class is auto-saved as Bangla notes for later

### 5. Folder Structure
OptiLearn-A/
├── server.py
├── transcriber.py
├── translator.py
├── static/
│   ├── teacher.html
│   └── student.html
└── README.md
### 6. Demo Plan (For Judges)
1. Prove no internet (try to open YouTube - it will fail)
2. Teacher says: "Today we will learn about water cycle"
3. Student laptop instantly shows & speaks: "Aj amra pani chokro sikhbo..."
4. Show the 0.5s delay - that's the magic


---
Built for Rural Schools. Zero Internet. Zero Cost.

