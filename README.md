#Offline Guru 🎓

An Offline-First AI Classroom Learning Assistant

Learn • Understand • Practice — Without the Internet

Offline Guru is an offline-first AI-powered classroom learning platform designed to help students understand lectures in their preferred language, learn from teachers' slides, and practice through AI-generated quizzes.

The teacher's laptop acts as the local server and, where supported, the Wi-Fi hotspot. Students connect using their phones, tablets, or laptops through a web browser. The essential classroom workflow is designed to operate without an external Internet connection.

«Project status: In development. The architecture and roadmap below describe the intended system. Features must be individually implemented and tested before being considered operational.»

---

#📌 Table of Contents

- "Problem Statement" (#-problem-statement)
- "Our Solution" (#-our-solution)
- "Key Features" (#-key-features)
- "How It Works" (#-how-it-works)
- "System Architecture" (#-system-architecture)
- "AI Agents" (#-ai-agents)
- "Local RAG System" (#-local-rag-system)
- "Technology Stack" (#-technology-stack)
- "Project Structure" (#-project-structure)
- "Hardware Requirements" (#-hardware-requirements)
- "Installation and Setup" (#-installation-and-setup)
- "Testing and Verification" (#-testing-and-verification)
- "Privacy and Offline Design" (#-privacy-and-offline-design)
- "Development Roadmap" (#-development-roadmap)
- "Limitations" (#-limitations)
- "Contributing" (#-contributing)
- "License" (#-license)

#🎯 Problem Statement

Students can struggle to understand lectures when the teacher speaks a language they do not understand well. In rural schools and resource-constrained classrooms, reliable Internet access may also be unavailable.

Teachers often prepare PDF or PowerPoint slides, but students may need more than translated text. They also need explanations, summaries, practice questions, and feedback based on the actual lesson.

Traditional cloud-based AI learning systems may be unsuitable when connectivity is unreliable or student data must remain on local devices.

💡 Our Solution

Offline Guru combines local speech recognition, machine translation, retrieval-augmented generation (RAG), and educational AI workflows in one classroom platform.

Teachers can upload their lecture slides and start a live class. The system transcribes the lecture, provides a translated version to connected students, retrieves relevant information from the uploaded slides, and generates learning materials grounded in the slides and lecture transcript.

The goal is to make AI-assisted learning available even when the classroom has no Internet connection.

✨ Key Features

Live Classroom Translation

- Capture the teacher's speech through a microphone.
- Transcribe speech using a locally running speech-recognition model.
- Translate lecture segments into the selected student language.
- Deliver translated text through the local network.
- Generate speech audio where a suitable local TTS voice is available.

Teacher Slide Management

- Upload PDF and PowerPoint lecture slides.
- Extract text and preserve slide references.
- Organize materials by subject, topic, or class session.
- Build a searchable local knowledge base.

Retrieval-Augmented Generation (RAG)

- Retrieve relevant slide content for lecture topics and student questions.
- Supply retrieved evidence to the local language model.
- Keep references to source documents and slide numbers where available.
- Reduce unsupported answers by grounding responses in retrieved materials.

AI-Powered Learning

- Generate lesson summaries and key points.
- Create multiple-choice and short-answer questions.
- Generate answer keys and explanations.
- Review generated questions against the available source material.
- Evaluate student responses using answer keys or grading rubrics.
- Recommend additional practice based on identified learning gaps.

Classroom Management

- Teacher dashboard for managing sessions.
- Student web interface accessible through the local network.
- Live connection and service status.
- Quiz review and approval controls.
- Session records and student progress, subject to the implemented privacy controls.

Offline-First Operation

- Local inference instead of mandatory cloud AI APIs.
- Local document storage and vector retrieval.
- Local backend and student communication.
- No external Internet connection required during the intended core classroom workflow once all dependencies and models are installed.

---

🔄 How It Works

flowchart TD
    A[Teacher uploads PDF/PPTX slides] --> B[Extract text and slide metadata]
    B --> C[Chunk documents and generate embeddings]
    C --> D[Local vector index]

    E[Teacher microphone] --> F[Local speech recognition]
    F --> G[Lecture transcript]
    G --> H[Live translation]
    H --> I[Local TTS]
    H --> J[Student web interface]
    I --> J

    G --> K[Context orchestration]
    D --> L[Retrieve relevant slide content]
    L --> K

    K --> M[Summary generation]
    K --> N[Quiz generation]
    N --> O[Question and answer validation]
    O --> P[Teacher review and approval]
    P --> Q[Student quiz]
    Q --> R[Assessment and feedback]

Typical Classroom Workflow

1. The teacher prepares the classroom and uploads the lecture slides.
2. Offline Guru extracts slide content and builds or loads the local RAG index.
3. Students connect to the teacher's local Wi-Fi network.
4. The teacher starts the live session and speaks into the microphone.
5. The speech-recognition component produces a transcript.
6. The translation component generates translated text, which is sent to students.
7. The context workflow retrieves relevant slide content and combines it with available lecture transcript segments.
8. The summary and quiz components prepare learning materials.
9. The teacher reviews and approves the generated quiz.
10. Students complete the quiz and receive scores and feedback.

Live translation should operate independently of slower summary and quiz generation tasks.

---

🏗️ System Architecture

Offline Guru is designed as a modular local application.

Layer| Responsibility
Frontend| Teacher dashboard and student learning interface
Backend| API endpoints, sessions, validation, and task coordination
Real-time communication| WebSocket delivery of transcripts and translations
Speech pipeline| Audio processing and speech recognition
Translation and TTS| Local text translation and speech generation
Document pipeline| Slide parsing, chunking, and metadata extraction
RAG layer| Embeddings, indexing, and contextual retrieval
Education workflows| Summary, quiz, assessment, and feedback
Storage| Local database, uploaded documents, and vector index
Test infrastructure| Unit, integration, offline, end-to-end, and load tests

Local Network Model

The intended deployment uses the teacher's laptop as both the application server and, where the operating system supports it, the Wi-Fi hotspot.

Students connect to the local network and open the application in a browser. The server's address must be discovered from the actual network configuration rather than hardcoded.

A router and Internet connection are not intended to be necessary for the core classroom workflow.

---

🤖 AI Agents

The education workflow uses specialized components coordinated by an orchestrator. These components may share the same local language model with different instructions and output schemas.

Component| Input| Responsibility| Output
Slide Processing| PDF/PPTX files| Extract text and source metadata| Indexed content
Speech Recognition| Teacher audio| Convert speech into text| Transcript segments
Translation| Transcript segments| Translate the lecture| Translated text
Context Agent| Transcript + retrieved slides| Connect lecture explanations with slide content| Grounded context
Summary Agent| Lecture context| Identify key points and concepts| Lesson summary
Quiz Agent| Slides + lecture context| Generate educational questions| Quiz draft and answer key
Validation Module| Quiz draft + sources| Check structure, grounding, and answer consistency| Validated or flagged draft
Assessment Module| Student answers + rubric| Evaluate responses| Score and evidence
Feedback Agent| Assessment results| Explain mistakes and recommend practice| Feedback and exercises
Orchestrator| Session events and task state| Coordinate component execution| Workflow decisions

Important: Speech recognition, text-to-speech, and RAG retrieval are specialized services or pipelines; they do not inherently need to be autonomous agents. The initial implementation should use simple, testable Python services before introducing a more complex multi-agent framework.

📚 Local RAG System

RAG stands for Retrieval-Augmented Generation.

Instead of relying only on a language model's existing knowledge, Offline Guru retrieves relevant content from the teacher's uploaded materials and supplies it as context for generation.

Document Indexing Pipeline

1. Accept a supported PDF or PPTX file.
2. Extract text and available slide/page metadata.
3. Split the text into manageable chunks.
4. Generate embeddings using a locally available embedding model.
5. Store vectors in a local index such as FAISS.
6. Store document identifiers, source locations, and related metadata.
7. Retrieve relevant chunks for a lecture topic or student question.

Slide and Lecture Context

The context workflow combines two primary sources:

- Slide context: definitions, diagrams with extractable text, key points, and teacher-provided materials.
- Lecture context: explanations, examples, and additional details from the teacher's spoken lecture.

The quiz generator should use both sources when relevant and retain source references for teacher review.

If the sources conflict or contain insufficient evidence, the system should flag uncertainty instead of inventing a definitive answer.

Scanned slides and diagrams may require OCR or additional vision-processing capabilities; ordinary text extraction alone cannot reliably interpret every visual element.

---

🛠️ Technology Stack

The following is the proposed starting stack. Actual choices may change after hardware benchmarking and repository inspection.

Area| Candidate technology
Programming language| Python
Backend API| FastAPI
Real-time communication| WebSockets
Speech recognition| Faster-Whisper
Local language model runtime| Ollama
Embeddings| A locally installed multilingual embedding model
Vector search| FAISS
Document parsing| PyMuPDF and python-pptx
Text-to-speech| A locally runnable TTS engine with verified Bangla support
Database| SQLite
Frontend| React and TypeScript
Styling| Tailwind CSS
Unit and integration testing| pytest
API testing| FastAPI test client or HTTPX
Load testing| A suitable WebSocket/API load-testing tool

All models, Python packages, frontend assets, and required voice files must be installed or downloaded before an offline classroom session.

Tool selection does not guarantee real-time performance, Bangla voice quality, or support for 30 students. These must be verified on the target hardware.

---

📁 Proposed Project Structure

offline-guru/
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── api/
│   │   ├── websocket/
│   │   ├── services/
│   │   │   ├── asr.py
│   │   │   ├── translation.py
│   │   │   ├── tts.py
│   │   │   └── slide_parser.py
│   │   ├── rag/
│   │   │   ├── chunking.py
│   │   │   ├── embeddings.py
│   │   │   ├── indexing.py
│   │   │   └── retrieval.py
│   │   ├── agents/
│   │   │   ├── context_agent.py
│   │   │   ├── summary_agent.py
│   │   │   ├── quiz_agent.py
│   │   │   ├── validation.py
│   │   │   ├── assessment.py
│   │   │   └── feedback_agent.py
│   │   ├── orchestrator/
│   │   ├── database/
│   │   └── config.py
│   ├── tests/
│   │   ├── unit/
│   │   ├── integration/
│   │   ├── e2e/
│   │   ├── offline/
│   │   └── load/
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── pages/
│   │   ├── components/
│   │   ├── teacher/
│   │   └── student/
│   └── package.json
├── data/
│   ├── uploads/
│   ├── indexes/
│   └── test-fixtures/
├── scripts/
├── docs/
├── README.md
└── TEST_REPORT.md

This is a proposed layout, not a claim that every listed file already exists.

---

💻 Hardware Requirements

Initial development target


- Python 3.10 or another version supported by the selected dependencies.
- Approximately 8–16 GB RAM as an initial experimentation range.
- Sufficient disk space for models, dependencies, uploaded slides, and indexes.
- A working microphone and speaker/headphones.
- Wi-Fi hardware and drivers that support the intended hotspot configuration.

A GPU is helpful for some models but is not universally required. Performance will depend on the selected model, quantization, CPU, GPU, and simultaneous workloads.

For classrooms with multiple students, the system should generally generate shared translation output once and broadcast it when students use the same language. Personalized languages and individualized audio may require additional inference or audio-generation work.

---


⚠️ Limitations

The following limitations must be evaluated rather than assumed away:

- Speech recognition can make errors, especially with accents, noise, and technical terms.
- Translation may change meaning or mishandle specialized vocabulary.
- Bangla TTS quality and model availability depend on the selected engine.
- Extracted text may not capture diagrams, images, or scanned slides.
- RAG can retrieve irrelevant passages and does not guarantee factual answers.
- AI-generated quizzes can contain ambiguous questions or incorrect answer keys.
- Running multiple models concurrently can increase memory use and latency.
- Thirty simulated connections do not guarantee reliable operation with thirty physical devices.
- Wi-Fi hotspot availability depends on hardware and operating-system support.

---

📄 License

Choose and add an explicit project license before publishing or redistributing the code. Verify the licenses of every model, dataset, voice, and dependency separately; an open-source software license does not automatically grant unrestricted rights to those assets.

---

🌱 Vision

Offline Guru aims to make AI-assisted classroom learning more accessible in places where reliable Internet access is unavailable.

By combining live translation, teacher-provided learning materials, local retrieval, and educational AI workflows, the project seeks to help students understand lectures and practice what they learn without depending on cloud services.

Offline Guru — Learn anywhere, even offline.