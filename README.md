# Offline Guru

### An Offline AI-Powered Adaptive Learning Management System

> **Bringing AI-powered education to classrooms with limited or no
> internet connectivity.**

Offline Guru is an AI-powered, locally hosted learning platform designed
for schools and learning environments where reliable internet access is
unavailable, expensive, or difficult to maintain. It combines classroom
speech translation, document-based learning, AI-assisted tutoring,
assessments, and student progress insights in one system that can run on
local hardware.

The long-term vision is to make a teacher's classroom more accessible
and adaptive: students can follow lessons in their preferred language,
ask questions about approved learning materials, practise through
quizzes, and track their progress --- while teachers manage lessons and
monitor learning from a central dashboard.

------------------------------------------------------------------------

## Table of Contents

-   [The Problem](#the-problem)
-   [Our Solution](#our-solution)
-   [Key Features](#key-features)
-   [How It Works](#how-it-works)
-   [System Architecture](#system-architecture)
-   [Technology Stack](#technology-stack)
-   [Offline-First Design](#offline-first-design)
-   [Project Status](#project-status)
-   [Getting Started](#getting-started)
-   [Configuration and Hardware](#configuration-and-hardware)
-   [Limitations](#limitations)
-   [Roadmap](#roadmap)
-   [Privacy and Security](#privacy-and-security)
-   [Contributing](#contributing)
-   [License](#license)

------------------------------------------------------------------------

## The Problem

Many students face barriers that prevent them from getting the most out
of classroom teaching:

-   **Language barriers:** Students may struggle to understand lessons
    delivered in a language they are not comfortable with.
-   **Limited connectivity:** Rural and under-resourced schools may not
    have dependable internet access.
-   **One-size-fits-all teaching:** A single lesson pace does not work
    equally well for every student.
-   **Limited learning support:** Students may not have access to an
    always-available tutor or explanations outside class.
-   **Lack of timely feedback:** Teachers can find it difficult to
    identify learning gaps and monitor individual progress in large
    classrooms.
-   **Scattered learning materials:** Useful explanations and answers
    may be disconnected from the teacher's actual lesson documents.

## Our Solution

Offline Guru is designed as a **local-first AI learning environment**. A
teacher's computer hosts the application and, where supported by the
operating system and hardware, creates a local wireless network for
student devices. Students connect through a browser on the same local
network; internet access is not required for the local classroom
workflow.

The platform brings together:

1.  Live classroom speech recognition and translation.
2.  AI Tutor and learning assistance grounded in uploaded course
    materials.
3.  Document intelligence for lesson files and classroom resources.
4.  Quiz and assessment workflows.
5.  Adaptive learning and mastery tracking.
6.  Teacher-facing classroom and learning analytics.

The system is intended to work without a cloud AI dependency during
classroom use. Models and required assets must be downloaded and
prepared beforehand.

------------------------------------------------------------------------

## Key Features

### 1. Live Classroom Translation

-   Captures the teacher's speech through a microphone.
-   Converts speech into text using local speech recognition.
-   Translates recognized text into a student's selected language.
-   Delivers translated captions to connected student devices in real
    time or near real time.
-   Supports a translation pipeline designed for English-to-Bangla and
    Bangla-to-English use cases.
-   Can provide locally generated speech output through text-to-speech
    where configured.

**Important:** Synthesized translated speech is not the same as
voice-preserving speech-to-speech translation. The current documented
pipeline generates speech from translated text; it does not reproduce
the teacher's original voice.

### 2. AI Tutor

-   Provides explanations and learning assistance.
-   Can use uploaded lesson documents as supporting context.
-   Helps students revisit concepts covered in class.
-   Is intended to keep answers aligned with the teacher's learning
    materials.

### 3. Document Intelligence and RAG

-   Supports ingestion of learning documents such as PDF, PPTX, TXT, and
    Markdown.
-   Extracts text and retains page or slide references when available.
-   Provides document-grounded retrieval for learning questions.
-   Can connect lesson content to summaries, explanations, and
    assessment generation.

**Current implementation note:** The documented retrieval foundation
uses deterministic hash-based lexical indexing and term-overlap
retrieval. It is not a neural embedding-based vector database.
Embedding-based semantic retrieval is a possible future improvement, not
a capability to assume is already active.

### 4. AI Quiz Generation and Assessment

-   Intended to generate questions from lesson materials and classroom
    content.
-   Supports the broader goal of checking student understanding.
-   Can be extended to provide feedback and identify topics that need
    revision.

### 5. Adaptive Learning and Mastery Tracking

-   Intended to use assessment results and learning activity to identify
    strengths and knowledge gaps.
-   Can support personalized practice and recommendations.
-   Provides the foundation for tracking progress over time.

### 6. OCR and Image-Based Learning

-   Planned capability for extracting educational text from images or
    scanned learning materials.
-   Can make printed or photographed resources usable in document-based
    learning workflows.
-   OCR availability depends on integrating and testing a suitable local
    OCR engine.

### 7. Student Analytics and Progress Reports

-   Intended to summarize quiz performance, learning activity, and
    topic-level progress.
-   Can help teachers identify students or topics that may need
    additional support.
-   PDF progress-report and learning-summary workflows have been part of
    the broader product direction; verify the current repository
    implementation before treating every report view as
    production-ready.

### 8. Teacher Dashboard

The teacher-facing interface is intended to bring classroom operations
together, including: - Starting and managing classroom sessions. -
Connecting student devices. - Managing learning documents. - Accessing
quizzes and lesson summaries. - Reviewing student progress and
analytics. - Monitoring classroom and system status.

### 9. Local Classroom Network

-   The teacher computer can act as the local application host.
-   Students connect using a browser on the same local network.
-   Session joining can use a classroom code or equivalent join flow.
-   WebSocket communication supports live classroom updates, with REST
    endpoints available for supported application operations and
    recovery flows.

------------------------------------------------------------------------

## How It Works

### Live translation workflow

``` text
Teacher Microphone
       |
       v
Audio Capture
       |
       v
Voice Activity Detection (VAD)
       |
       v
Speech Recognition
(Faster-Whisper)
       |
       v
Machine Translation
(Marian / NLLB, depending on configuration)
       |
       +----------------------+
       |                      |
       v                      v
Translated Captions       Text-to-Speech
       |                  (Piper, if enabled)
       |                      |
       +----------+-----------+
                  |
                  v
        Local WebSocket Server
                  |
                  v
       Connected Student Devices
```

### Document-based learning workflow

``` text
Teacher Uploads Learning Material
              |
              v
     Document Text Extraction
              |
              v
    Local Document Index / Storage
              |
              v
       Student Learning Question
              |
              v
     Relevant Passage Retrieval
              |
              v
    Context-Grounded AI Response
```

Quiz generation, adaptive recommendations, and analytics can build on
document content and recorded learning activity as those workflows are
implemented and validated.

------------------------------------------------------------------------

## System Architecture

Offline Guru follows a local client-server architecture.

  -----------------------------------------------------------------------
  Component                           Responsibility
  ----------------------------------- -----------------------------------
  Teacher device                      Hosts the application, local
                                      models, and classroom session

  Student devices                     Access learning features through a
                                      web browser

  FastAPI backend                     REST APIs, session management, and
                                      application services

  WebSocket layer                     Live classroom communication and
                                      updates

  Speech pipeline                     Audio capture, VAD, speech
                                      recognition, and translation

  Translation service                 Converts recognized text between
                                      configured languages

  TTS service                         Generates speech from translated
                                      text when enabled

  Document service                    Extracts, stores, indexes, and
                                      retrieves learning content

  SQLite                              Stores application and learning
                                      data

  Local filesystem                    Stores uploaded documents, model
                                      assets, and generated files
  -----------------------------------------------------------------------

### Communication

-   REST APIs handle supported application operations.
-   WebSockets support live classroom communication.
-   Student devices communicate with the teacher-hosted server over the
    local network.

------------------------------------------------------------------------

## Technology Stack

The following reflects the documented project foundation. Exact versions
and optional components should be checked against the current dependency
files in the repository.

  -----------------------------------------------------------------------
  Layer                               Technology
  ----------------------------------- -----------------------------------
  Backend                             Python, FastAPI

  ASGI server                         Uvicorn

  Real-time communication             WebSocket

  Speech recognition                  Faster-Whisper

  Speech processing                   Voice Activity Detection (VAD)

  Machine translation                 Marian / NLLB, depending on
                                      configuration

  Text-to-speech                      Piper TTS

  Data storage                        SQLite

  Document processing                 pypdf, Python standard library
                                      ZIP/XML processing

  Frontend                            Existing web-based teacher and
                                      student interfaces

  Local deployment                    Ubuntu/Linux, local network or
                                      teacher-hosted hotspot
  -----------------------------------------------------------------------

### AI and retrieval approach

-   Local inference is used for the documented speech and translation
    pipeline.
-   Document retrieval currently has a lexical/hash-based foundation.
-   Neural embeddings and a vector database should only be listed as
    active technologies after they have been integrated and verified.

------------------------------------------------------------------------

## Offline-First Design

Offline Guru is designed to keep classroom learning available without
depending on an external cloud service.

**Local operation:** 1. Install the application and dependencies on the
teacher computer. 2. Download and prepare the required AI models before
the class. 3. Start the local backend. 4. Enable a compatible local
wireless network or hotspot. 5. Let students connect to the teacher
computer's network. 6. Open the classroom interface from student
browsers.

A router is not mandatory if the teacher computer can create and share a
compatible hotspot. However, hotspot support depends on the operating
system, wireless adapter, driver, and network configuration.

**Offline does not mean hardware-free:** speech recognition,
translation, and speech synthesis still require enough CPU, memory, and
storage to run the selected models.

------------------------------------------------------------------------

## Project Status

Offline Guru has a working technical foundation, but the full adaptive
LMS vision should not be confused with a fully validated production
release.

  -----------------------------------------------------------------------
  Area                                Documented status
  ----------------------------------- -----------------------------------
  FastAPI application and local       Implemented foundation
  server                              

  Local classroom session and student Implemented foundation
  connection flow                     

  WebSocket communication and REST    Implemented foundation
  recovery paths                      

  Audio capture and VAD pipeline      Implemented; real classroom capture
                                      still needs broader testing

  Faster-Whisper speech recognition   Integrated in documented pipeline

  Machine translation                 Integrated in documented pipeline;
                                      quality and latency need
                                      device-specific evaluation

  Piper speech output                 Integrated as translated-text TTS;
                                      not teacher-voice preservation

  Document upload and text extraction Implemented foundation for PDF,
                                      PPTX, TXT, and Markdown

  Page/slide-aware document retrieval Implemented lexical retrieval
                                      foundation

  Embedding-based semantic RAG        Not verified as implemented

  AI Tutor                            Product capability / integration
                                      status requires repository
                                      verification

  Quiz generation and assessment      Product capability / integration
                                      status requires repository
                                      verification

  Adaptive learning and mastery       Planned or requires end-to-end
  tracking                            verification

  OCR and image learning              Planned or requires integration
                                      verification

  Student analytics and teacher       UI/product direction exists; verify
  reports                             data-backed workflows

  30-student classroom performance    Not yet proven through a complete
                                      real-device classroom test
  -----------------------------------------------------------------------

**Known validation gap:** Previous testing established that important
local application and model workflows could run, but did not prove
reliable performance with approximately 30 real student devices in a
live classroom. Wi-Fi access-point behavior, multiple physical phones,
real speakers, sustained audio capture, and end-to-end latency require
field testing.

------------------------------------------------------------------------

## Getting Started

The exact commands may vary as the repository evolves. The following is
a general setup outline; use the project's current setup scripts and
dependency files as the source of truth.

### Prerequisites

-   Ubuntu or another supported Linux environment.
-   Python version compatible with the current project dependencies.
-   Microphone and working audio input configuration for live
    translation.
-   Sufficient RAM, CPU capacity, and storage for the selected local
    models.
-   Local Wi-Fi/hotspot capability for connecting student devices.
-   Model files downloaded and prepared before offline operation.

### 1. Clone the repository

``` bash
git clone <YOUR_REPOSITORY_URL>
cd <YOUR_PROJECT_DIRECTORY>
```

Replace the placeholders with the actual repository URL and directory
name.

### 2. Create a virtual environment

``` bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

Use the repository's dependency file if available:

``` bash
pip install -r requirements.txt
```

If the project uses another dependency manager, follow its lockfile and
setup instructions instead.

### 4. Configure local models

Download the required speech recognition, translation, and TTS model
assets while internet access is available. Configure their local paths
using the project's supported configuration mechanism.

Do not assume that a model is available offline merely because its
Python package is installed.

### 5. Start the application

Use the actual application entry point documented in the repository. For
a FastAPI application, a development launch may look like:

``` bash
uvicorn <MODULE>:<APP_OBJECT> --host 0.0.0.0 --port 8000
```

Replace the placeholders with the real module and FastAPI application
object. Do not expose the development server to untrusted networks.

### 6. Connect student devices

-   Connect student devices to the same local network as the teacher
    computer.
-   Open the teacher-provided local address in a browser.
-   Join the classroom using the supported session code or joining flow.

The server address depends on the teacher computer's active network
configuration; do not assume a fixed IP address.

------------------------------------------------------------------------

## Configuration and Hardware

### Hardware considerations

  -----------------------------------------------------------------------
  Environment                         Expected use
  ----------------------------------- -----------------------------------
  CPU-only laptop                     Possible for lightweight local
                                      models, with latency trade-offs

  8--16 GB RAM                        More practical for experimentation,
                                      depending on model sizes and
                                      concurrent services

  Dedicated GPU                       Can improve inference speed when
                                      supported, but is not a requirement
                                      of the offline-first concept

  Teacher microphone                  Required for live speech input

  Student phones/laptops              Browser-based classroom access

  Local Wi-Fi adapter                 Required for a teacher-hosted
                                      hotspot setup
  -----------------------------------------------------------------------

Actual performance depends on the CPU, RAM available to the application,
model size, language pair, audio chunking, and number of connected
students. Benchmark the complete pipeline on the target hardware before
promising a specific translation delay or classroom capacity.

### Suggested evaluation metrics

-   Speech recognition accuracy (WER).
-   Translation quality assessed by bilingual human reviewers.
-   End-to-end speech-to-caption latency.
-   End-to-end translated-audio latency.
-   Audio queue drops and processing backlog.
-   Memory and CPU utilization.
-   WebSocket stability under concurrent student connections.
-   Document retrieval relevance and citation/page accuracy.
-   Quiz correctness and usefulness of generated feedback.

------------------------------------------------------------------------

## Limitations

-   Low-end CPU-only hardware may introduce noticeable speech and
    translation delays.
-   Streaming speech recognition can produce revisions as more audio
    arrives.
-   Translation quality varies by language pair, model, and subject
    vocabulary.
-   Generated TTS audio may lag behind translated captions.
-   Translated TTS does not preserve the teacher's original voice.
-   Local hotspot availability depends on hardware and operating-system
    support.
-   A successful local test does not establish reliable performance for
    a full classroom.
-   Lexical document retrieval may miss semantically related passages
    that do not share important terms.
-   Adaptive learning, OCR, analytics, and other advanced LMS
    capabilities require end-to-end implementation and validation before
    being presented as production-ready.

------------------------------------------------------------------------

## Roadmap

### Phase 1 --- Reliable Offline Classroom

-   [x] Local FastAPI application foundation
-   [x] Classroom session and student connection foundation
-   [x] WebSocket-based live communication foundation
-   [x] Speech recognition, translation, and translated-text TTS
    pipeline foundation
-   [ ] Validate teacher-hosted hotspot on target hardware
-   [ ] Test with real student phones and sustained classroom sessions
-   [ ] Measure latency, accuracy, and resource usage

### Phase 2 --- Document Intelligence and AI Tutor

-   [x] Basic document ingestion foundation
-   [x] PDF/PPTX/TXT/Markdown text extraction foundation
-   [x] Lexical document retrieval foundation
-   [ ] Validate grounded answers across varied lesson materials
-   [ ] Improve retrieval quality and answer evaluation
-   [ ] Complete the student-facing AI Tutor workflow

### Phase 3 --- Assessment and Adaptive Learning

-   [ ] Complete lesson-based quiz generation
-   [ ] Implement assessment and feedback workflows
-   [ ] Track topic-level mastery over time
-   [ ] Add evidence-based personalized practice recommendations

### Phase 4 --- Rich Learning Inputs and Analytics

-   [ ] Integrate and validate local OCR
-   [ ] Complete data-backed student analytics
-   [ ] Complete teacher progress reports
-   [ ] Validate learning summaries and classroom insights

### Phase 5 --- Classroom-Scale Validation

-   [ ] Test with approximately 30 concurrent student devices
-   [ ] Run long-duration classroom sessions
-   [ ] Evaluate weak-hardware performance
-   [ ] Improve reliability, recovery, accessibility, and usability

------------------------------------------------------------------------

## Privacy and Security

Offline Guru's local-first approach is intended to reduce dependence on
external services and keep classroom data within the local environment.

Recommended deployment practices: - Keep the classroom network
restricted to trusted participants. - Use session-specific joining
credentials or tokens. - Validate authorization on teacher and student
endpoints. - Avoid exposing the local server to public networks. - Store
only the student data required for learning and reporting. - Protect
uploaded documents and local model assets with appropriate filesystem
permissions. - Define retention and deletion policies for classroom
recordings, transcripts, and learning records.

Local hosting alone does not automatically guarantee security.
Authentication, authorization, input validation, network isolation, and
secure data handling must be tested.

------------------------------------------------------------------------

## Contributing

Contributions are welcome.

1.  Fork the repository.
2.  Create a feature branch.
3.  Make focused changes with tests.
4.  Run the relevant test suite.
5.  Submit a pull request describing the change and its validation.

For AI-related contributions, include evaluation details where possible:
model name and version, hardware, language pair, test data, latency, and
quality measurements.

------------------------------------------------------------------------

## License

Add the project's actual license before distributing this repository. Do
not claim an open-source license until a license has been selected and
included.

------------------------------------------------------------------------

## Vision

**Offline Guru aims to make quality, language-accessible, adaptive
learning possible even where internet connectivity is not.**

A teacher should be able to deliver a lesson once, while every student
gets a better opportunity to understand, practise, ask questions, and
make progress --- supported by AI running locally in the classroom.
