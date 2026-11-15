# Medicine Buddy

A fresh India-first medicine information website. This project is intentionally **not** a rebrand of the supplied MedLens reference ZIP. It uses the reference only for broad layout inspiration.

## What this build does

- Search a medicine by brand or generic name.
- Upload up to four packaging photos (front/back/side) and run OCR only after clicking **Scan selected photos**.
- Correct the OCR text manually before searching.
- Search live sources through Tavily (free plan) and supplementary public US label sources (openFDA and DailyMed).
- Extract possible composition passages from retrieved source-page text. The user must confirm the composition against the actual pack before ingredient research.
- Search each user-confirmed ingredient separately and show source excerpts with links.
- Provide a basic condition/allergy text screen only against retrieved ingredient excerpts. It never declares a medicine safe.
- Provide an evidence-retrieval Q&A. It returns relevant source passages and does not invent a generative answer when evidence is missing.
- Show the retrieval steps and source types for viva explanation.

## Important scope and honesty

1. There is no hardcoded medicine catalogue or sample medicine content.
2. The app does **not** promise coverage of every medicine. India's ABDM Drug Registry is a major source, but its open API access and onboarding requirements must be confirmed before integrating it as a production source. This build does not pretend to have authenticated registry access.
3. Tavily is a search/discovery layer, not a medical authority. The app ranks government/regulator and recognised health sources above general websites, but source snippets can still be incomplete. Verify the exact brand, strength, manufacturer and dosage form.
4. openFDA and DailyMed are US label sources. They are supplementary references and are clearly labelled as not India-specific.
5. No paid LLM is included. The ingredient highlights and Q&A path are extractive and source-grounded, not a free-form LLM. Quick highlights select sentences from retrieved source snippets; they are not rewritten by an LLM. This avoids surprise costs and reduces unsupported generated medical claims. A true generative model can be added later only if a genuinely no-cost deployment option is confirmed.
6. No database is required in this version. A small in-memory cache reduces repeat API calls while a server instance stays warm. It resets on cold starts. Neon is deliberately omitted because it is not needed for the first deploy and can introduce cold-start delays and another account configuration.
7. Condition/allergy entries are used only for the current request; there is no login, profile or persistent health-data database. Packaging images are processed in the browser and are not sent to the backend.
8. This is an educational prototype, not a clinically validated medical device or a substitute for a pharmacist/doctor.

## Requirements

- Node.js 20.19+ or 22.12+
- Python 3.10 or newer (Python 3.11 is a good choice)
- VS Code
- Internet connection for live medicine/source search and the first OCR engine download

## 1. Open the project

1. Extract `Medicine_Buddy.zip`.
2. Open the extracted `medicine-buddy` folder in VS Code.
3. Open **Terminal → New Terminal**.

## 2. Set up the Python backend (Terminal 1)

Windows PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

If PowerShell blocks activation, run this once in that terminal:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

Mac/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

### Add the free search key

1. Create an account at <https://app.tavily.com/>.
2. Create/copy an API key from its dashboard.
3. Open the root `.env` file and paste it after `TAVILY_API_KEY=`.
4. Do **not** enable pay-as-you-go or add a paid plan. The free Researcher plan currently lists 1,000 credits/month and says requests stop when the allowance is exhausted unless pay-as-you-go is enabled. Check the current dashboard terms before using it.
5. Each Tavily basic search costs 1 credit. A lookup uses one product search, one separate search per confirmed ingredient, and one extra search for combination rationale when there are multiple ingredients. The backend reuses product results from its warm-instance cache for six hours where possible. The free allowance is not unlimited; a cold start may cause the product search to run again.

Example:

```env
TAVILY_API_KEY=tvly-your-own-key-here
```

Keep `.env` private. Never paste your key into frontend code or commit it to GitHub.

Start the backend:

```powershell
uvicorn backend.app.main:app --reload --port 8000
```

Check <http://127.0.0.1:8000/health>. You should see JSON with `"ok": true`.

## 3. Set up the SvelteKit website (Terminal 2)

Open a second VS Code terminal in the project root:

```powershell
npm install
npm run dev
```

Open the local URL shown by Vite, normally <http://localhost:5173>.

The Vite development server forwards `/api/*` requests to the Python backend at `http://127.0.0.1:8000`.

## 4. Use the website

1. Enter a medicine name, preferably including strength and manufacturer, or upload up to four photos.
2. For photos, click **Scan selected photos**. The OCR engine is imported and downloaded only at that moment. The first scan can take longer; later scans reuse the worker while the page remains open. OCR is English-first and works best with sharp, well-lit PNG/JPG/WEBP images (up to 5 MB each).
3. Check and correct the OCR text. OCR is not product identification.
4. Search the medicine.
5. Review the identity status and sources. Do not assume a possible match is the exact product.
6. Confirm the composition from the actual pack. Select a source candidate only after checking it, or type the composition yourself.
7. Click **Research these ingredients**. Each confirmed ingredient is searched separately.
8. Review the source excerpts, precautions and evidence-based Q&A.

## 5. Deploy to Vercel

The SvelteKit frontend is pre-rendered to static files and copied into `public/`, which Vercel serves through its CDN. The Python FastAPI app handles only the `/api/*` endpoints. Both deploy from one repository, so there is no separate backend host that could sleep.

1. Push the extracted project folder to a **private** GitHub repository (do not include `.env`, `.venv` or `node_modules`).
2. Sign in to <https://vercel.com/> and import that repository.
3. Keep the project root as `./`.
4. Keep the auto-detected **FastAPI** framework preset. The `vercel.json` file runs `npm install && npm run build`. The build script copies SvelteKit's `build/` output into `public/`; Vercel serves those static files from its CDN, while FastAPI handles API requests. Leave Output Directory blank.
5. In Vercel → Project → Settings → Environment Variables, add:
   - Name: `TAVILY_API_KEY`
   - Value: your Tavily key
   - Environments: Production, Preview (and Development if desired)
6. Do not add `DATABASE_URL`; this version does not need Neon.
7. Deploy. Test `https://YOUR-APP.vercel.app/api/health` first, then open the home page and test medicine lookup.

Vercel announced Python as a generally available runtime on 15 October 2026. Function duration, bandwidth and build usage are still subject to the plan limits. If deployment fails, inspect the Vercel build log and current FastAPI runtime documentation before changing dependencies.

## 6. Quality checks before submission

From the project root:

```powershell
npm run check
npm run build
```

In a terminal with the virtual environment activated:

```powershell
python -m compileall backend index.py
python backend/check_backend.py
```

Also test manually:

- Home page opens without downloading OCR.
- No Tavily key: the website still opens and reports limited live discovery.
- Invalid/unknown medicine: no invented composition appears.
- Ambiguous medicine: the identity status remains a candidate/unverified state.
- Composition is not researched until the user confirms it.
- Multiple images are accepted and OCR progress is visible.
- OCR failure does not prevent manual name entry.
- Tavily rate limit or unavailable source does not crash the website.
- Allergy/condition screen never returns a definitive safe verdict.
- Q&A says it lacks evidence when no matching source passage is found.

## Folder structure

```text
medicine-buddy/
├── index.py                  # Vercel FastAPI entry point
├── scripts/publish-static.mjs # Copies the static site into Vercel public/
├── public/ (generated during build; served by Vercel CDN)
├── backend/
│   ├── app/
│   │   └── main.py           # FastAPI, search, source ranking, retrieval
│   └── check_backend.py      # offline backend checks
├── src/
│   ├── lib/api.js            # frontend API client
│   ├── routes/+layout.svelte
│   ├── routes/+page.svelte   # website and interactions
│   ├── app.css               # design system
│   └── app.html
├── .env.example
├── package.json
├── requirements.txt
├── svelte.config.js          # Static SvelteKit build
├── vercel.json
└── vite.config.js
```
