<script>
  import { onDestroy, onMount } from 'svelte';
  import {
  checkHealth,
  lookupMedicine,
  askMedicine,
  analyzeMedicine
} from '$lib/api.js';

  let medicineName = '';
  let selectedFiles = [];
  let previews = [];
  let ocrText = '';
  let ocrBusy = false;
  let ocrProgress = 0;
  let ocrMessage = '';
  let worker = null;
  let result = null;
  let busy = false;
  let error = '';
  let compositionText = '';
  let conditionsText = '';
  let allergiesText = '';
  let chatQuestion = '';
  let chatBusy = false;
  let chatMessages = [];
  let serviceState = 'checking';
  let activeTab = 'overview';
  let showHealth = false;
  let showChat = false;

  const tabs = [
    { id: 'overview', label: 'Overview' },
    { id: 'ingredients', label: 'Ingredients' },
    { id: 'how', label: 'How it works' },
    { id: 'precautions', label: 'Precautions' }
  ];

  onMount(() => {
    checkHealth().then((data) => serviceState = data.tavily_configured ? 'online' : 'limited').catch(() => serviceState = 'offline');
  });

  function setFiles(event) {
    previews.forEach((item) => URL.revokeObjectURL(item.url));
    const rawFiles = Array.from(event.currentTarget.files || []);
    const chosen = rawFiles.filter((f) => ['image/png', 'image/jpeg', 'image/webp'].includes(f.type));
    const tooLarge = chosen.filter((f) => f.size > 5 * 1024 * 1024);
    const files = chosen.filter((f) => f.size <= 5 * 1024 * 1024).slice(0, 4);
    selectedFiles = files;
    previews = files.map((file) => ({ name: file.name, url: URL.createObjectURL(file) }));
    ocrText = '';
    ocrMessage = '';
    error = rawFiles.length && !chosen.length ? 'Please choose PNG, JPG or WEBP photos.' : tooLarge.length ? 'Each photo must be 5 MB or smaller. Oversized photos were skipped.' : chosen.length > 4 ? 'Only the first four photos were selected.' : '';
  }

  function removeFile(index) {
    if (previews[index]?.url) URL.revokeObjectURL(previews[index].url);
    selectedFiles = selectedFiles.filter((_, i) => i !== index);
    previews = previews.filter((_, i) => i !== index);
  }

  async function scanImages() {
    if (!selectedFiles.length) return;
    ocrBusy = true;
    ocrProgress = 0;
    ocrMessage = 'Preparing the text scanner…';
    error = '';
    try {
      if (!worker) {
        const Tesseract = await import('tesseract.js');
        worker = await Tesseract.createWorker('eng', 1, {
          logger: (message) => {
            if (message.status) ocrMessage = message.status.replaceAll('_', ' ');
            if (typeof message.progress === 'number') ocrProgress = Math.round(message.progress * 100);
          }
        });
      }
      const parts = [];
      for (let i = 0; i < selectedFiles.length; i++) {
        ocrMessage = `Reading photo ${i + 1} of ${selectedFiles.length}…`;
        const { data } = await worker.recognize(selectedFiles[i]);
        if (data.text?.trim()) parts.push(data.text.trim());
      }
      ocrText = parts.join('\n\n--- Next photo ---\n\n');
      ocrProgress = 100;
      ocrMessage = ocrText ? 'Text extracted. Please check and correct it before searching.' : 'No readable text found. Try a sharper, well-lit photo or enter the name manually.';
      const firstLine = ocrText.split('\n').map((x) => x.trim()).find((x) => x.length > 2 && /[a-zA-Z]/.test(x));
      if (firstLine && !medicineName.trim()) medicineName = firstLine.slice(0, 100);
    } catch (err) {
      error = `OCR could not finish: ${err.message || 'unknown error'}. You can still type the medicine name manually.`;
      ocrMessage = 'Scan failed';
    } finally {
      ocrBusy = false;
    }
  }

  async function searchMedicine() {
    const name = medicineName.trim() || ocrText.trim().split('\n').find((x) => x.trim().length > 2) || '';
    if (name.length < 2) {
      error = 'Enter a medicine name or scan a medicine package first.';
      return;
    }
    medicineName = name;
    busy = true;
    error = '';
    result = null;
    chatMessages = [];
    compositionText = '';
    activeTab = 'overview';
    try {
      result = await lookupMedicine({ medicine_name: name });
      if (result.composition_candidates?.length) {
        compositionText = result.composition_candidates[0].text;
      }
    } catch (err) {
      error = err.message || 'Medicine search failed. Please try again.';
    } finally {
      busy = false;
    }
  }



async function researchIngredients() {
  if (!result || compositionText.trim().length < 3) {
    error = 'Enter or select the exact composition printed on the package.';
    return;
  }

  busy = true;
  error = '';

  try {
    result = await lookupMedicine({
      medicine_name: medicineName,
      confirmed_composition: compositionText.trim(),
      conditions: splitTags(conditionsText),
      allergies: splitTags(allergiesText)
    });

    activeTab = 'ingredients';
  } catch (err) {
    error = err.message || 'Ingredient research failed. Please try again.';
  } finally {
    busy = false;
  }
}

async function checkPrecautions() {
  if (!result || compositionText.trim().length < 3) {
    error = 'Please confirm the medicine composition first.';
    return;
  }

  busy = true;
  error = '';

  const conditions = splitTags(conditionsText);
  const allergies = splitTags(allergiesText);

  try {
    // Step 1: Retrieve medicine information using the existing lookup.
    const lookupResult = await lookupMedicine({
      medicine_name: medicineName,
      confirmed_composition: compositionText.trim(),
      conditions,
      allergies
    });

    // Preserve the lookup result even if the AI service is unavailable.
    result = lookupResult;

    // Step 2: Send the retrieved evidence to the LangChain Agent.
    const agentResult = await analyzeMedicine({
      medicine_name: medicineName,
      conditions,
      allergies,
      lookup_result: lookupResult
    });

    // Step 3: Keep the original lookup data and add the agent response.
    result = {
      ...lookupResult,
      agent_analysis: agentResult
    };

    activeTab = 'precautions';

  } catch (err) {
    error = err.message || 'Precaution analysis failed. Please try again.';
  } finally {
    busy = false;
  }
}

// Keep full source text in the API result for research and chat,
// but show a short, readable excerpt in the interface.
function sourceExcerpt(value, maxLength = 420) {
  if (!value) return 'No readable source excerpt is available.';

  const text = String(value)
    .replace(/<[^>]*>/g, ' ')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, ' ')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/^\s{0,3}#{1,6}\s+/gm, '')
    .replace(/^\s*[-*_]{3,}\s*$/gm, ' ')
    .replace(/^\s*[-*+]\s+/gm, '')
    .replace(/^\s*\d+[.)]\s+/gm, '')
    .replace(/\|/g, ' ')
    .replace(/[`*_>#]/g, '')
    .replace(/\s+/g, ' ')
    .trim();

  if (text.length <= maxLength) return text;

  const shortened = text.slice(0, maxLength);
  const lastSpace = shortened.lastIndexOf(' ');
  return `${shortened.slice(0, lastSpace > maxLength * 0.65 ? lastSpace : maxLength).trim()}…`;
}

function splitTags(value) {
  return value
    .split(',')
    .map(item => item.trim())
    .filter(Boolean)
    .slice(0, 12);
}

  async function sendQuestion() {
    const question = chatQuestion.trim();
    if (!question || chatBusy) return;

    const previousHistory = chatMessages.map((message) => ({
      role: message.role,
      content: message.content
    }));

    chatMessages = [...chatMessages, { role: 'user', content: question }];
    chatQuestion = '';
    chatBusy = true;
    error = '';

    try {
      const evidence = [];
      for (const source of result?.sources || []) evidence.push(source);
      for (const source of result?.combination_sources || []) evidence.push(source);
      for (const ingredient of result?.ingredients || []) {
        for (const source of ingredient.sources || []) evidence.push(source);
      }

      const response = await askMedicine({
        medicine_name: medicineName || 'general medicine information',
        question,
        evidence,
        history: previousHistory
      });

      chatMessages = [
        ...chatMessages,
        {
          role: 'assistant',
          content: response.answer || "I couldn't prepare a response just now.",
          sources: response.sources || [],
          grounded: Boolean(response.grounded)
        }
      ];
    } catch (err) {
      chatMessages = [
        ...chatMessages,
        {
          role: 'assistant',
          content: err.message || 'I could not retrieve a response. Please try again.',
          sources: [],
          grounded: false
        }
      ];
    } finally {
      chatBusy = false;
    }
  }

  // Convert a small, safe subset of Markdown to HTML for readable chat replies.
  // All model text is HTML-escaped before formatting to prevent injected markup.
  function formatMessage(value) {
    const escaped = String(value || '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#39;');

    return escaped
      .replace(/^### (.+)$/gm, '<h4>$1</h4>')
      .replace(/^## (.+)$/gm, '<h3>$1</h3>')
      .replace(/^# (.+)$/gm, '<h3>$1</h3>')
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/^[-*] (.+)$/gm, '<li>$1</li>')
      .replace(/(?:<li>.*<\/li>\n?)+/gs, (items) => `<ul>${items}</ul>`)
      .replace(/\n{2,}/g, '</p><p>')
      .replace(/\n/g, '<br>')
      .replace(/^(?!<h[34]>|<ul>|<li>)(.+)$/s, '<p>$1</p>');
  }

  function useCandidate(candidate) {
    compositionText = candidate.text;
  }

  function switchTab(id) {
    activeTab = id;
    if (id === 'precautions') showHealth = true;
  }

  onDestroy(() => {
    previews.forEach((item) => URL.revokeObjectURL(item.url));
    if (worker) worker.terminate();
  });
</script>

<svelte:head>
  <title>Medicine Buddy — Understand what's in your medicine</title>
  <meta name="description" content="Scan a medicine pack, review its composition and explore source-backed ingredient information." />
</svelte:head>

<div class="app-shell">
  <header class="topbar">
    <a class="brand" href="/" aria-label="Medicine Buddy home">
      <span class="brand-mark"><span></span><span></span></span>
      <span>medicine<span class="brand-light">buddy</span></span>
    </a>
    <nav class="topnav" aria-label="Main navigation">
      <a href="#how-it-works">How it works</a>
      <a href="#safety-note">Safety first</a>
    </nav>
    <div class="status-pill"><span class:online={serviceState === 'online'} class:offline={serviceState === 'offline'} class:limited={serviceState === 'limited'} class="status-dot"></span>{serviceState === 'online' ? 'Search ready' : serviceState === 'limited' ? 'Search key needed' : serviceState === 'offline' ? 'Connection issue' : 'Checking services'}</div>
  </header>

  <main>
    <section class="hero-section">
      <div class="hero-copy">
        <div class="eyebrow"><span class="eyebrow-line"></span> MEDICINE, MADE UNDERSTANDABLE</div>
        <h1>Know what goes<br />into <em>your medicine.</em></h1>
        <p class="hero-subtitle">A clearer look at the ingredients, what they do, and the information worth checking — all in one place.</p>
        <div class="hero-points">
          <span><i class="check-icon">✓</i> Ingredient explanations</span>
          <span><i class="check-icon">✓</i> Source-linked information</span>
          <span><i class="check-icon">✓</i> No account needed</span>
        </div>
      </div>
      <div class="hero-art" aria-hidden="true">
        <div class="art-orbit orbit-one"></div><div class="art-orbit orbit-two"></div>
        <div class="art-spark spark-one">✦</div><div class="art-spark spark-two">✧</div>
        <div class="art-card art-card-back"><div class="art-card-line"></div><div class="art-card-line short"></div><div class="art-mini-capsule"></div></div>
        <div class="art-card art-card-front">
          <div class="art-card-top"><span>MB</span><span>01 / 04</span></div>
          <div class="art-card-title">Your medicine,<br /><b>explained.</b></div>
          <div class="art-pill-row"><span class="pill-shape pill-one"></span><span class="pill-shape pill-two"></span><span class="pill-shape pill-three"></span></div>
          <div class="art-card-footer"><span>INGREDIENT GUIDE</span><span>✳</span></div>
        </div>
        <div class="art-float float-top"><span class="float-icon">✦</span><span><b>Clear answers</b><small>Backed by sources</small></span></div>
        <div class="art-float float-bottom"><span class="float-check">✓</span><span><b>Understand every part</b><small>One ingredient at a time</small></span></div>
      </div>
    </section>

    <section class="workspace" id="start">
      <div class="section-heading">
        <div><div class="eyebrow">LET'S GET STARTED</div><h2>Explore a medicine</h2></div>
        <p>Search by name or add photos of the packaging.</p>
      </div>
      <div class="workspace-grid">
        <div class="input-card search-card">
          <div class="card-heading"><span class="step-number">01</span><div><h3>Search by medicine name</h3><p>Enter a brand or generic name, including strength if known.</p></div></div>
          <label class="field-label" for="medicine-name">MEDICINE NAME</label>
          <div class="search-control"><span class="search-icon">⌕</span><input id="medicine-name" bind:value={medicineName} onkeydown={(e) => e.key === 'Enter' && searchMedicine()} placeholder="e.g. medicine brand or ingredient" /><button class="search-button" onclick={searchMedicine} disabled={busy}>Search <span>↗</span></button></div>
          <div class="input-hint">Tip: Include the strength and manufacturer if you know them.</div>
          <div class="or-divider"><span></span> OR SCAN A PACK <span></span></div>
          <div class="upload-zone">
            <input id="medicine-photos" type="file" accept="image/png,image/jpeg,image/webp" multiple capture="environment" onchange={setFiles} />
            <label for="medicine-photos" class="upload-label">
              <span class="upload-icon">＋</span><span><b>Choose medicine photos</b><small>Front, back or side · PNG, JPG or WEBP · up to 4 photos (5 MB each)</small></span><span class="browse-link">Browse files</span>
            </label>
          </div>
          {#if previews.length}
            <div class="preview-list">
              {#each previews as preview, i}
                <div class="preview-item"><img src={preview.url} alt="Medicine package preview" /><span>{preview.name}</span><button aria-label="Remove photo" onclick={() => removeFile(i)}>×</button></div>
              {/each}
            </div>
            <button class="scan-button" onclick={scanImages} disabled={ocrBusy}><span>⌗</span>{ocrBusy ? 'Reading photos…' : 'Scan selected photos'}</button>
          {/if}
          {#if ocrBusy || ocrMessage}
            <div class="ocr-status"><div class="ocr-status-top"><span>{ocrMessage}</span>{#if ocrBusy}<b>{ocrProgress}%</b>{/if}</div><div class="progress-track"><div class="progress-fill" style={`width:${ocrProgress}%`}></div></div></div>
          {/if}
          {#if ocrText}
            <label class="field-label ocr-label" for="ocr-text">CHECK AND CORRECT THE EXTRACTED TEXT</label>
            <textarea id="ocr-text" bind:value={ocrText} rows="5" placeholder="Extracted packaging text appears here"></textarea>
            <button class="text-button" onclick={() => { const line = ocrText.split('\n').find((x) => x.trim().length > 2); if (line) medicineName = line.trim(); }}>Use first text line as medicine name ↗</button>
          {/if}
          <div class="privacy-note"><span>◈</span> Photos are processed in your browser for text recognition. They are not uploaded to the medicine search API.</div>
        </div>

        <aside class="side-column">
          <div class="info-card how-card" id="how-it-works">
            <div class="small-icon mint-icon">✳</div><h3>A little clarity goes a long way.</h3><p>Medicine labels can be confusing. Medicine Buddy helps you explore the information behind the names — without pretending to replace a healthcare professional.</p>
            <a href="#safety-note" class="inline-link">Our safety promise <span>↗</span></a>
          </div>
          <div class="info-card scope-card">
            <div class="scope-top"><span class="small-icon lilac-icon">⌁</span><span class="scope-tag">INDIA FIRST</span></div>
            <h3>Designed for real-world packs</h3><p>Search supports medicine names and packaging text. Exact brand, strength and manufacturer details help improve matching.</p>
            <div class="scope-chips"><span>Tablets</span><span>Capsules</span><span>Liquids</span><span>Supplements</span></div>
          </div>
        </aside>
      </div>
    </section>

    {#if error}
      <div class="error-banner" role="alert"><span>!</span><div>{error}</div><button onclick={() => error = ''} aria-label="Dismiss">×</button></div>
    {/if}

    {#if busy}
      <section class="loading-panel"><div class="loader-orbit"><span></span></div><h3>Gathering medicine information</h3><p>Searching available references and checking the evidence. This can take a little while.</p><div class="loading-steps"><span class="active">Identify</span><i></i><span>Retrieve</span><i></i><span>Review</span></div></section>
    {/if}

    {#if result && !busy}
      <section class="results-section" id="results">
        <div class="results-topline"><div><div class="eyebrow">YOUR MEDICINE REPORT</div><h2>{result.medicine_name}</h2><p class="results-subtitle">Evidence retrieved for this search. Review product identity before relying on composition details.</p></div><button class="outline-button" onclick={() => { result = null; chatMessages = []; }}>＋ New search</button></div>
        <div class="identity-banner" class:identity-good={result.identity.level === 'moderate'} class:identity-warn={result.identity.level !== 'moderate'}><div class="identity-symbol">{result.identity.level === 'moderate' ? '✓' : '!'}</div><div><b>{result.identity.label}</b><p>{result.identity.note}</p></div><span class="identity-sources">{result.identity.matched_sources} matching source(s)</span></div>

        <div class="result-tabs" role="tablist" aria-label="Medicine information sections">
          {#each tabs as tab}<button role="tab" aria-selected={activeTab === tab.id} class:tab-active={activeTab === tab.id} onclick={() => switchTab(tab.id)}>{tab.label}</button>{/each}
        </div>

        {#if activeTab === 'overview'}
          <div class="result-grid">
            <div class="result-card composition-card"><div class="result-card-head"><div><span class="card-kicker">PRODUCT COMPOSITION</span><h3>What is inside?</h3></div><span class="result-card-icon">◈</span></div>
              {#if result.product_highlights?.what_it_does?.length}<div class="product-purpose"><b>Purpose mentioned by retrieved product sources</b>{#each result.product_highlights.what_it_does as item}<p>{item.text} <a href={item.source_url} target="_blank" rel="noopener noreferrer">Source ↗</a></p>{/each}<small>These excerpts may describe a different strength or market. Check the exact pack.</small></div>{/if}
              {#if result.composition_candidates?.length}
                <p class="muted-copy">Possible composition text was found in retrieved sources. It is not confirmed as the exact Indian pack yet.</p>
                {#each result.composition_candidates as candidate, i}<button class="candidate-line" onclick={() => useCandidate(candidate)}><span>{candidate.text}</span><small>Use this text ↗</small></button>{/each}
              {:else}<div class="empty-state"><span>⌕</span><b>No composition could be reliably extracted.</b><p>Check the package's composition panel and enter it below. The app will not guess ingredients.</p></div>{/if}
              <label class="field-label" for="composition">CONFIRM THE COMPOSITION FROM YOUR PACK</label>
              <textarea id="composition" bind:value={compositionText} rows="3" placeholder="Example format: Ingredient A 500 mg + Ingredient B 10 mg"></textarea>
              <button
  class="primary-action"
  onclick={researchIngredients}
  disabled={busy || compositionText.trim().length < 3}
>
  Research these ingredients →
</button>
              <p class="micro-note">Only research a composition you have checked against the actual packaging or a reliable product label.</p>
            </div>
            <div class="result-card sources-card"><div class="result-card-head"><div><span class="card-kicker">SOURCE CHECK</span><h3>Where this came from</h3></div><span class="result-card-icon">↗</span></div>
              {#if result.sources?.length}
                <div class="source-list">{#each result.sources as source}<a class="source-item" href={source.url} target="_blank" rel="noopener noreferrer"><span class="source-type">{source.source_type}</span><b>{source.title}</b><p>{sourceExcerpt(source.content, 190)}</p><span class="source-open">Open source ↗</span></a>{/each}</div>
              {:else}<div class="empty-state"><span>⌕</span><b>No live sources returned.</b><p>Check the search key or try again later. No medicine facts have been filled in from a sample database.</p></div>{/if}
            </div>
          </div>
        
{:else if activeTab === 'ingredients'}
  <div class="result-card wide-card">
    <div class="result-card-head">
      <div>
        <span class="card-kicker">INGREDIENT EXPLORER</span>
        <h3>Understand each ingredient</h3>
      </div>
      <span class="result-card-icon">✳</span>
    </div>

    <p class="muted-copy">
  Explore what each confirmed ingredient is, what it is used for,
  and how it works, using the available reference information.
</p>

    {#if result.ingredients?.length}
      <div class="ingredient-grid">
        {#each result.ingredients as ingredient, i}
          <article class="ingredient-card ingredient-profile">

            <div class="ingredient-number">0{i + 1}</div>
            <h4>{ingredient.name}</h4>

            <p class="ingredient-status">
              {ingredient.status === 'evidence_found'
                ? 'Reference material found'
                : ingredient.status === 'search_unavailable'
                  ? 'Search temporarily unavailable'
                  : 'Limited reference material'}
            </p>

            <div class="ingredient-profile-grid">

            </div>

            <section class="ingredient-purpose-block">
              <span class="ingredient-info-label">
                WHAT IT IS / WHAT IT DOES
              </span>

              {#if ingredient.highlights?.what_it_does?.length}
                {#each ingredient.highlights.what_it_does as item}
                  <p>
                    {item.text}
                    <a
                      href={item.source_url}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      Source ↗
                    </a>
                  </p>
                {/each}
              {:else}
                <p class="not-found">
                  A supported explanation was not found in the retrieved
                  references.
                </p>
              {/if}
            </section>

            <section class="ingredient-purpose-block">
              <span class="ingredient-info-label">
                HOW IT WORKS
              </span>

              {#if ingredient.highlights?.how_it_works?.length}
                {#each ingredient.highlights.how_it_works as item}
                  <p>
                    {item.text}
                    <a
                      href={item.source_url}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      Source ↗
                    </a>
                  </p>
                {/each}
              {:else}
                <p class="not-found">
                  Mechanism information was not found in the retrieved
                  references.
                </p>
              {/if}
            </section>

            {#if ingredient.chemical_info?.pubchem_url}
              <a
                class="chemical-source-link"
                href={ingredient.chemical_info.pubchem_url}
                target="_blank"
                rel="noopener noreferrer"
              >
                Chemical information: PubChem ↗
              </a>
            {/if}

            {#if ingredient.sources?.length}
              <details class="source-details full-sources">
                <summary>
                  View original reference excerpts
                  ({ingredient.sources.length})
                </summary>

                {#each ingredient.sources as source}
                  <div class="ingredient-evidence">
                    <span>{source.source_type}</span>
                    <b>{source.title}</b>
                    <p>{sourceExcerpt(source.content)}</p>

                    <a
                      href={source.url}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      Read original reference ↗
                    </a>
                  </div>
                {/each}
              </details>
            {:else}
              <p class="muted-copy">
                No supporting ingredient references were retrieved.
              </p>
            {/if}

          </article>
        {/each}
      </div>
    {:else}
      <div class="empty-state">
        <span>✳</span>
        <b>Composition confirmation is needed first.</b>
        <p>
          Return to Overview, check the exact ingredients on the
          packaging, and research them.
        </p>
        <button
          class="text-button"
          onclick={() => activeTab = 'overview'}
        >
          Back to composition ↗
        </button>
      </div>
    {/if}
  </div>

        {:else if activeTab === 'how'}
          <div class="result-card wide-card"><div class="result-card-head"><div><span class="card-kicker">TRANSPARENT PROCESS</span><h3>How this result was prepared</h3></div><span class="result-card-icon">⌁</span></div><div class="trace-list">{#each result.trace || [] as item, i}<div class="trace-row"><span>{String(i + 1).padStart(2, '0')}</span><p>{item}</p><i>✓</i></div>{/each}</div>{#if result.ingredients?.length > 1}<div class="notice-box"><b>Why these ingredients are combined</b>{#if result.combination_sources?.length}<p>These sources discuss the combination. Read the source passages and check whether they refer to the same formulation and strength.</p>{#each result.combination_sources as source}<div class="ingredient-evidence"><b>{source.title}</b><p>{sourceExcerpt(source.content)}</p><a href={source.url} target="_blank" rel="noopener noreferrer">Read reference ↗</a></div>{/each}{:else}<p>No reliable source explaining this exact combination was retrieved. The app will not guess why the ingredients were combined.</p>{/if}</div>{/if}<div class="notice-box"><b>Why we ask you to confirm the composition</b><p>Similar brand names can refer to different strengths, dosage forms or manufacturers. A text match alone cannot prove that the retrieved label belongs to your exact pack.</p></div></div>
        
{:else if activeTab === 'precautions'}
  <div class="result-card wide-card">

    <div class="result-card-head">
      <div>
        <span class="card-kicker">BASIC PRECAUTION REVIEW</span>
        <h3>Conditions & known allergies</h3>
      </div>
      <span class="result-card-icon">♡</span>
    </div>

    <p class="muted-copy">
      Review precaution information found in the retrieved medicine
      references. This is a limited text-screening aid, not a validated
      drug-interaction checker.
    </p>

    <div class="health-input-grid">
      <div>
        <label class="field-label" for="conditions">
          HEALTH CONDITIONS (COMMA SEPARATED)
        </label>
        <input
          id="conditions"
          bind:value={conditionsText}
          placeholder="e.g. diabetes, asthma"
        />
      </div>

      <div>
        <label class="field-label" for="allergies">
          KNOWN ALLERGIES (COMMA SEPARATED)
        </label>
        <input
          id="allergies"
          bind:value={allergiesText}
          placeholder="e.g. penicillin, lactose"
        />
      </div>
    </div>

    <button
      class="primary-action"
      onclick={checkPrecautions}
      disabled={busy || compositionText.trim().length < 3}
    >
      {busy ? 'Checking precautions…' : 'Check retrieved precautions →'}
    </button>

    {#if result.health_check}
      <div class="health-result health-result-clear">

        <b>
          {result.health_check.status === 'review'
            ? 'Review recommended'
            : result.health_check.status === 'not_run'
              ? 'Not checked yet'
              : 'No matching text found'}
        </b>

        <p>{result.health_check.message}</p>

        {#if result.health_check.matched_terms?.length}
          <div class="matched-term-list">
            <span>Matched terms:</span>
            {#each result.health_check.matched_terms as term}
              <b>{term}</b>
            {/each}
          </div>
        {/if}

      </div>
    {/if}

        {#if result.agent_analysis}
      <div class="precaution-evidence-section">
        <div class="precaution-section-heading">
          <span class="card-kicker">LANGCHAIN AI AGENT</span>
          <h3>AI-assisted medicine review</h3>
        </div>

        <div class="health-result">
          <div class="chat-message-content">
            {@html formatMessage(result.agent_analysis.answer)}
          </div>

          <p class="muted-copy">
            AI-generated analysis based on the retrieved references.
            This is educational information, not a validated medical
            interaction assessment.
          </p>

          {#if result.agent_analysis.provider}
            <p class="muted-copy">
              AI provider: {result.agent_analysis.provider}
            </p>
          {/if}

          {#if result.agent_analysis.sources?.length}
            <div class="chat-citations">
              <span>Supporting references</span>

              {#each result.agent_analysis.sources as source}
                {#if source.url}
                  <a
                    href={source.url}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    {source.title} ↗
                  </a>
                {/if}
              {/each}
            </div>
          {/if}
        </div>
      </div>
    {/if}

    <div class="precaution-evidence-section">
      <div class="precaution-section-heading">
        <span class="card-kicker">RETRIEVED MEDICINE INFORMATION</span>
        <h3>Precautions mentioned in references</h3>
      </div>

      {#if result.ingredients?.some(
        ingredient => ingredient.highlights?.precautions?.length
      )}

        {#each result.ingredients as ingredient}
          {#if ingredient.highlights?.precautions?.length}

            <article class="precaution-ingredient-group">
              <h4>{ingredient.name}</h4>

              {#each ingredient.highlights.precautions as item}
                <div class="precaution-evidence-item">
                  <p>{item.text}</p>

                  <a
                    href={item.source_url}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Read source reference ↗
                  </a>
                </div>
              {/each}
            </article>

          {/if}
        {/each}

      {:else}
        <div class="empty-state">
          <span>♡</span>
          <b>
            No precaution statements were found in the retrieved excerpts.
          </b>
          <p>
            This does not mean that no risks or precautions exist.
            Please check the official medicine leaflet or ask a pharmacist.
          </p>
        </div>
      {/if}
    </div>

  </div>
{/if}

        {#if result.notices?.length}<div class="notice-stack">{#each result.notices as notice}<p><span>i</span>{notice}</p>{/each}</div>{/if}
      </section>
    {/if}

        <div class="ask-card chat-card">
          <div class="ask-intro">
            <span class="ask-icon">✳</span>
            <div>
              <span class="card-kicker">ASK MEDICINE BUDDY</span>
              <h3>Let's talk about your medicine</h3>
              <p>Type naturally. I'll chat with you, answer medicine-related questions using available sources, and let you know if something is outside my scope.</p>
            </div>
          </div>

          <div class="chat-transcript" aria-live="polite">
            {#if !chatMessages.length}
              <div class="chat-welcome">
                <span class="chat-avatar">✳</span>
                <div class="chat-bubble assistant-bubble">
                  <b>Hi! I'm Medicine Buddy.</b>
                  <p>You can type any question in your own words, even before searching. I'll help with medicine topics and follow-ups, and politely tell you when a question is outside my medicine-related scope.</p>
                </div>
              </div>
            {/if}
            {#each chatMessages as message, i}
              <div class:from-user={message.role === 'user'} class:from-assistant={message.role === 'assistant'} class="chat-message" >
                {#if message.role === 'assistant'}<span class="chat-avatar">✳</span>{/if}
                <div class:assistant-bubble={message.role === 'assistant'} class:user-bubble={message.role === 'user'} class="chat-bubble">
                  <div class="chat-message-content">{@html formatMessage(message.content)}</div>
                  {#if message.sources?.length}
                    <div class="chat-citations">
                      <span>Sources</span>
                      {#each message.sources as source}
                        <a href={source.url} target="_blank" rel="noopener noreferrer">{source.title} ↗</a>
                      {/each}
                    </div>
                  {/if}
                </div>
              </div>
            {/each}
            {#if chatBusy}
              <div class="chat-message from-assistant">
                <span class="chat-avatar">✳</span>
                <div class="chat-bubble assistant-bubble typing-bubble"><span></span><span></span><span></span><small>Medicine Buddy is thinking…</small></div>
              </div>
            {/if}
          </div>

          <div class="ask-input-row chat-compose">
            <input bind:value={chatQuestion} onkeydown={(e) => e.key === 'Enter' && !e.shiftKey && sendQuestion()} placeholder="Message Medicine Buddy…" aria-label="Message Medicine Buddy" />
            <button onclick={sendQuestion} disabled={chatBusy || !chatQuestion.trim()} aria-label="Send message">Send ↗</button>
          </div>
          <p class="chat-footnote">Medicine Buddy provides educational information, not a diagnosis or a substitute for a healthcare professional.</p>
        </div>

    <section class="safety-section" id="safety-note"><div class="safety-icon">♡</div><div><div class="eyebrow">A NOTE ON YOUR HEALTH</div><h2>Helpful information.<br /><em>Not a substitute for care.</em></h2><p>Medicine Buddy is an educational tool. Source data can be incomplete, outdated or about a different market or formulation. Always verify the exact pack and ask a qualified pharmacist or doctor before making a medicine decision. Do not start, stop or change treatment based on this website.</p></div></section>
  </main>

  <footer class="footer"><a class="brand" href="/"><span class="brand-mark"><span></span><span></span></span><span>medicine<span class="brand-light">buddy</span></span></a><span>Understand more. Decide with care.</span><span>© 2026 Medicine Buddy · Educational use only</span></footer>
</div>


<style>
  .chat-card { overflow: hidden; }
  .chat-transcript {
    display: flex;
    flex-direction: column;
    gap: 18px;
    max-height: 520px;
    min-height: 150px;
    overflow-y: auto;
    padding: 20px 4px;
    margin: 18px 0;
    border-top: 1px solid #e7efeb;
    border-bottom: 1px solid #e7efeb;
    scroll-behavior: smooth;
  }
  .chat-welcome, .chat-message { display: flex; align-items: flex-start; gap: 10px; }
  .chat-message.from-user { justify-content: flex-end; }
  .chat-avatar {
    display: grid; place-items: center; flex: 0 0 30px; width: 30px; height: 30px;
    border-radius: 50%; background: #e4f4ed; color: #27634d; font-weight: 700;
  }
  .chat-bubble {
  max-width: min(88%, 680px);
  padding: 15px 18px;
  border-radius: 16px;
  font-size: 16px;
  line-height: 1.75;
  overflow-wrap: anywhere;
}

.chat-message-content {
  font-size: 16px;
  line-height: 1.75;
}

.chat-message-content :global(p) {
  font-size: 16px;
  margin: 0 0 12px;
}

.chat-message-content :global(h3),
.chat-message-content :global(h4) {
  margin: 14px 0 8px;
  font-size: 18px;
  line-height: 1.5;
}

.chat-message-content :global(ul) {
  margin: 10px 0;
  padding-left: 22px;
}

.chat-message-content :global(li) {
  font-size: 16px;
  line-height: 1.7;
  margin: 6px 0;
}
  .assistant-bubble { background: #f1f7f4; color: #263a36; border-top-left-radius: 5px; }
  .user-bubble { background: #28634f; color: #fff; border-top-right-radius: 5px; }
  .chat-message-content :global(p) { margin: 0 0 10px; }
  .chat-message-content :global(p:last-child) { margin-bottom: 0; }
  .chat-message-content :global(h3), .chat-message-content :global(h4) { margin: 12px 0 6px; font-size: 1rem; }
  .chat-message-content :global(ul) { margin: 8px 0; padding-left: 20px; }
  .chat-message-content :global(li) { margin: 4px 0; }
  .user-bubble .chat-message-content :global(p) { color: inherit; }
  .chat-citations { display: flex; flex-direction: column; gap: 5px; margin-top: 12px; padding-top: 10px; border-top: 1px solid #dce9e2; font-size: .82rem; }
  .chat-citations span { color: #62776d; font-weight: 700; }
  .chat-citations a { color: #27634d; text-decoration: underline; }
  .chat-compose {
  margin-top: 12px;
  align-items: center;
  gap: 10px;
  padding: 7px;
  border-radius: 14px;
}

.chat-compose input {
  min-width: 0;
  min-height: 48px;
  padding: 12px 14px;
  font-size: 16px !important;
  line-height: 1.5;
}

.chat-compose input::placeholder {
  font-size: 15px;
}

.chat-compose button {
  min-height: 46px;
  padding: 11px 20px;
  border-radius: 10px;
  font-size: 14px !important;
  font-weight: 700;
  white-space: nowrap;
}

.chat-citations {
  font-size: 14px;
  line-height: 1.55;
}

.ingredient-evidence p {
  font-size: 14px;
  line-height: 1.65;
  color: #61786b;
}
  .chat-footnote { margin: 10px 0 0; color: #74847d; font-size: .78rem; }
  .typing-bubble { display: flex; align-items: center; gap: 5px; }
  .typing-bubble > span { width: 6px; height: 6px; border-radius: 50%; background: #6b9582; animation: chatPulse 1s infinite ease-in-out; }
  .typing-bubble > span:nth-child(2) { animation-delay: .15s; }
  .typing-bubble > span:nth-child(3) { animation-delay: .3s; }
  .typing-bubble small { margin-left: 7px; color: #74847d; }
  @keyframes chatPulse { 0%, 80%, 100% { opacity: .35; transform: translateY(0); } 40% { opacity: 1; transform: translateY(-3px); } }
  @media (max-width: 640px) {
    .chat-bubble { max-width: 94%; padding: 12px 14px; }
    .chat-transcript { max-height: 440px; }
    .chat-compose { gap: 6px; padding: 5px; }
    .chat-compose input { min-height: 44px; padding: 10px; font-size: 15px !important; }
    .chat-compose button { min-height: 42px; padding: 9px 12px; font-size: 13px !important; }
  }


/* Readable medicine information */
.ingredient-profile {
  padding: 28px !important;
}

.ingredient-profile h4 {
  font-size: 22px !important;
  line-height: 1.4;
  color: #285744;
  margin-bottom: 10px;
}

.ingredient-profile p,
.ingredient-evidence p,
.precaution-evidence-item p {
  font-size: 16px !important;
  line-height: 1.85 !important;
  color: #526b60;
}

.ingredient-info-block {
  padding: 22px;
  border: 1px solid #dcebe3;
  border-radius: 15px;
  background: #f7fbf8;
}

.ingredient-info-label {
  display: block;
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 1.2px;
  color: #638575;
  margin-bottom: 12px;
}

.chemical-formula {
  font-size: 26px !important;
  font-weight: 800;
  color: #17664f !important;
}

.ingredient-purpose-block {
  padding: 24px;
  margin-top: 20px;
  border-left: 4px solid #64a887;
  border-radius: 12px;
  background: #f4faf6;
}

.ingredient-evidence {
  padding: 22px 0;
  border-top: 1px solid #e2eee7;
}

.ingredient-evidence b {
  font-size: 17px;
}

.ingredient-evidence a,
.precaution-evidence-item a {
  color: #267653;
  font-weight: 700;
  font-size: 14px;
}

.health-result-clear {
  padding: 24px;
  border-radius: 16px;
  margin: 22px 0;
}

.health-result-clear b {
  font-size: 18px;
}

.health-result-clear p {
  font-size: 16px;
  line-height: 1.8;
}

@media (max-width: 680px) {
  .ingredient-profile {
    padding: 18px !important;
  }

  .ingredient-profile-grid {
    grid-template-columns: 1fr !important;
  }
}

/* Ingredient information layout */

.ingredient-profile {
  padding: 28px !important;
}

.ingredient-profile h4 {
  font-size: 22px !important;
  line-height: 1.4;
  color: #285744;
  margin-bottom: 10px;
}

.ingredient-profile p,
.ingredient-evidence p,
.precaution-evidence-item p {
  font-size: 16px !important;
  line-height: 1.85 !important;
  color: #526b60;
}

.ingredient-profile-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  margin: 22px 0;
}

.ingredient-info-block {
  padding: 22px;
  border: 1px solid #dcebe3;
  border-radius: 15px;
  background: #f7fbf8;
}

.ingredient-info-label {
  display: block;
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 1.2px;
  color: #638575;
  margin-bottom: 12px;
}

.ingredient-info-block p {
  font-size: 18px !important;
  font-weight: 700;
  color: #285744;
}

.chemical-formula {
  font-size: 27px !important;
  font-weight: 800;
  color: #17664f !important;
}

.chemical-formula :global(sub) {
  font-size: 65%;
  vertical-align: sub;
}

.ingredient-purpose-block {
  padding: 24px;
  margin-top: 20px;
  border-left: 4px solid #64a887;
  border-radius: 12px;
  background: #f4faf6;
}

.ingredient-evidence {
  padding: 22px 0;
  border-top: 1px solid #e2eee7;
}

.ingredient-evidence b {
  font-size: 17px;
}

.ingredient-evidence a,
.precaution-evidence-item a,
.chemical-source-link {
  color: #267653;
  font-weight: 700;
  font-size: 14px;
}

.chemical-source-link {
  display: inline-block;
  margin: 12px 0;
}

/* Precaution section */

.health-result-clear {
  padding: 24px;
  border-radius: 16px;
  margin: 22px 0;
  border: 1px solid #dcebe3;
  background: #f5faf7;
}

.health-result-clear b {
  font-size: 18px;
  color: #285744;
}

.health-result-clear p {
  font-size: 16px;
  line-height: 1.8;
}

.precaution-evidence-section {
  margin-top: 30px;
}

.precaution-section-heading h3 {
  font-size: 21px;
  margin: 8px 0 18px;
}

.precaution-ingredient-group {
  padding: 22px;
  margin: 15px 0;
  border: 1px solid #dcebe3;
  border-radius: 15px;
}

.precaution-ingredient-group h4 {
  font-size: 18px;
  color: #285744;
  margin-bottom: 12px;
}

.precaution-evidence-item {
  padding: 15px 0;
  border-top: 1px solid #e8f0eb;
}

.precaution-evidence-item p {
  margin-bottom: 10px;
}

.matched-term-list {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-top: 15px;
}

.matched-term-list span {
  color: #62776d;
  font-size: 14px;
}

.matched-term-list b {
  padding: 6px 12px;
  border-radius: 20px;
  background: #e6f3eb;
  color: #27634d;
  font-size: 13px;
}

@media (max-width: 680px) {
  .ingredient-profile {
    padding: 18px !important;
  }

  .ingredient-profile-grid {
    grid-template-columns: 1fr;
  }
}

</style>
