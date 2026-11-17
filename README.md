# Medicine Buddy

**Medicine Buddy** is an AI-powered medicine information platform designed to help users understand medicines, explore their ingredients, review relevant precautions, and ask medicine-related questions.

The application combines a SvelteKit frontend, FastAPI backend, PostgreSQL database, evidence retrieval, and conversational AI to provide accessible, source-grounded medicine information.

## Features

### 1. Medicine Search
- Search for medicines using their brand or generic name.
- Retrieve medicine information from the connected database.
- Display available medicine details and relevant source references.
- Support medicine identification using uploaded packaging images and OCR.
- Allow users to review and correct extracted text before searching.

### 2. Ingredient Information
- Identify the ingredients associated with a medicine.
- Display information about what an ingredient is used for.
- Explain its general function and how it works.
- Provide relevant information and supporting source references.

### 3. Medicine Analysis
- Analyse available medicine information and ingredient evidence.
- Review user-provided health conditions and allergies against available evidence.
- Highlight relevant precautions and potential concerns.
- Display the evidence used to produce the analysis.

### 4. Conversational AI Assistant
- Ask questions about a medicine or its ingredients.
- Receive conversational responses using retrieved information.
- Ask follow-up questions using the conversation context.
- View relevant source references where available.
- Acknowledge when the available evidence is insufficient.

The conversational assistant and medicine analysis are separate features with different purposes.

### 5. Evidence-Based Retrieval
- Retrieve relevant information from the connected medicine database and external sources.
- Use search and retrieval to identify supporting information.
- Present source references to help users verify the information.
- Avoid presenting unsupported information as established medical facts.

## Technology Stack

| Component | Technology |
|---|---|
| Frontend | SvelteKit |
| Frontend styling | CSS |
| Backend | Python, FastAPI |
| Database | PostgreSQL (Neon) |
| AI agent | LangChain `create_agent` |
| LLM providers | Groq and Google Gemini |
| Web search | Tavily |
| OCR | Tesseract.js |
| Deployment | Vercel |
| Version control | Git and GitHub |

## System Architecture

Medicine Buddy follows a frontend-backend architecture.

1. **Frontend:** The SvelteKit application provides the user interface for medicine searches, image uploads, analysis, and conversational questions.
2. **Backend:** FastAPI handles API requests, medicine lookup, evidence retrieval, analysis, and agent interactions.
3. **Database:** PostgreSQL stores structured medicine and ingredient information.
4. **Retrieval layer:** Relevant information is retrieved from available database records and external sources.
5. **AI agent:** LangChain connects the conversational assistant to its evidence-retrieval tools and configured language model providers.
6. **Response:** The backend returns the retrieved information and generated response to the frontend for display.

## Project Structure

```text
medicine-buddy/
|
|-- backend/
|   |-- app/
|   |   |-- main.py
|   |   |-- agent.py
|   |
|   |-- data/
|   |-- database.py
|   |-- requirements.txt
|   |-- check_backend.py
|   |-- clean_dataset.py
|   |-- test_agent.py
|
|-- src/
|   |-- lib/
|   |   |-- api.js
|   |
|   |-- routes/
|   |   |-- +page.svelte
|   |   |-- +layout.svelte
|   |
|   |-- app.css
|   |-- app.html
|
|-- index.py
|-- package.json
|-- svelte.config.js
|-- vite.config.js
|-- vercel.json
|-- README.md
```

## Installation and Setup

### Prerequisites

- Python 3.10 or newer
- Node.js
- npm
- Git
- VS Code
- Internet connection

### 1. Clone the Repository

```bash
git clone https://github.com/sakshiSE/medicine_buddy.git
cd medicine_buddy
```

### 2. Set Up the Backend

Open a terminal and navigate to the backend directory:

```powershell
cd backend
```

Create and activate a virtual environment:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Install the Python dependencies:

```powershell
pip install -r requirements.txt
```

Configure the required environment variables using the environment configuration expected by the backend.

### 3. Configure Environment Variables

The application uses the following environment variables:

```env
DATABASE_URL=your_database_connection_string
GROQ_API_KEY=your_groq_api_key
GEMINI_API_KEY=your_gemini_api_key
TAVILY_API_KEY=your_tavily_api_key
```

Add the actual values through your local environment configuration and Vercel environment settings.

**Important:** Never commit API keys, database credentials, or private environment files to GitHub.

### 4. Start the Backend

Run this command from the `backend` directory:

```powershell
py -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

The backend will be available at:

```text
http://127.0.0.1:8000
```

Check the API health endpoint:

```text
http://127.0.0.1:8000/api/health
```

If the local backend exposes the health endpoint without the `/api` prefix, use the route configured in `main.py`.

### 5. Set Up the Frontend

Open a second terminal in the project root:

```powershell
npm install
```

Start the SvelteKit development server:

```powershell
npm run dev
```

Open the local URL displayed by the terminal, normally:

```text
http://localhost:5173
```

The frontend communicates with the backend through the configured API proxy.

## Deployment

The frontend and backend are deployed together through a single Vercel project connected to the GitHub repository.

Deployment configuration is maintained in the project's Vercel configuration and build files.

To deploy:

1. Push the latest project changes to the connected GitHub repository.
2. Open the project in Vercel.
3. Configure the required environment variables in the Vercel project settings.
4. Trigger a deployment.
5. Verify the deployed website and API health endpoint.
6. Test medicine search, analysis, and conversational chat after deployment.

The deployment must use the existing project build configuration. Do not create a separate backend deployment unless the architecture is deliberately changed.

## Safety and Limitations

- Medicine Buddy is an educational information platform, not a clinically validated medical device.
- It does not diagnose diseases or prescribe medicines.
- It does not replace advice from a qualified doctor or pharmacist.
- Medicine identification results should be checked against the actual packaging.
- OCR can make mistakes, so extracted text must be reviewed.
- Ingredient and precaution information depends on the available sources and their completeness.
- The absence of a warning in the retrieved information does not mean that a medicine is safe.
- AI-generated responses may be incomplete or incorrect. Important medical decisions should always be verified with a healthcare professional.

## Future Improvements

- Expand medicine and ingredient database coverage.
- Improve medicine identification from packaging images.
- Add more authoritative and region-specific medical references.
- Improve retrieval accuracy and source verification.
- Enhance conversational context handling.
- Add further validation and testing.

## Author

**Medicine Buddy**  
An academic project developed as part of the MSc Artificial Intelligence programme.

**Repository:** https://github.com/sakshiSE/medicine_buddy