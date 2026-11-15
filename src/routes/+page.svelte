<script>
  import { onDestroy, onMount } from 'svelte';
  import { checkHealth, lookupMedicine, askMedicine } from '$lib/api.js';

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
  let chatAnswer = null;
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
    chatAnswer = null;
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

  function splitTags(value) {
    return value.split(',').map((x) => x.trim()).filter(Boolean).slice(0, 12);
  }

  async function sendQuestion() {
    if (!result || !chatQuestion.trim()) return;
    chatBusy = true;
    error = '';
    try {
      const evidence = [];
      for (const source of result.sources || []) evidence.push(source);
      for (const source of result.combination_sources || []) evidence.push(source);
      for (const ingredient of result.ingredients || []) {
        for (const source of ingredient.sources || []) evidence.push(source);
      }
      chatAnswer = await askMedicine({ medicine_name: medicineName, question: chatQuestion.trim(), evidence });
    } catch (err) {
      error = err.message || 'Could not retrieve an answer.';
    } finally {
      chatBusy = false;
    }
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
        <div class="results-topline"><div><div class="eyebrow">YOUR MEDICINE REPORT</div><h2>{result.medicine_name}</h2><p class="results-subtitle">Evidence retrieved for this search. Review product identity before relying on composition details.</p></div><button class="outline-button" onclick={() => { result = null; chatAnswer = null; }}>＋ New search</button></div>
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
              <button class="primary-action" onclick={researchIngredients} disabled={busy || compositionText.trim().length < 3}>Research these ingredients <span>→</span></button>
              <p class="micro-note">Only research a composition you have checked against the actual packaging or a reliable product label.</p>
            </div>
            <div class="result-card sources-card"><div class="result-card-head"><div><span class="card-kicker">SOURCE CHECK</span><h3>Where this came from</h3></div><span class="result-card-icon">↗</span></div>
              {#if result.sources?.length}
                <div class="source-list">{#each result.sources as source}<a class="source-item" href={source.url} target="_blank" rel="noopener noreferrer"><span class="source-type">{source.source_type}</span><b>{source.title}</b><p>{source.content.slice(0, 190)}{source.content.length > 190 ? '…' : ''}</p><span class="source-open">Open source ↗</span></a>{/each}</div>
              {:else}<div class="empty-state"><span>⌕</span><b>No live sources returned.</b><p>Check the search key or try again later. No medicine facts have been filled in from a sample database.</p></div>{/if}
            </div>
          </div>
        {:else if activeTab === 'ingredients'}
          <div class="result-card wide-card"><div class="result-card-head"><div><span class="card-kicker">INGREDIENT EXPLORER</span><h3>Ingredient-by-ingredient research</h3></div><span class="result-card-icon">✳</span></div><p class="muted-copy">Quick highlights are selected from retrieved source text. They are not rewritten by a generative model; open the detailed excerpts to check the original context.</p>
            {#if result.ingredients?.length}
              <div class="ingredient-grid">{#each result.ingredients as ingredient, i}<article class="ingredient-card"><div class="ingredient-number">0{i + 1}</div><h4>{ingredient.name}</h4><p class="ingredient-status">{ingredient.status === 'evidence_found' ? 'Reference material found' : ingredient.status === 'search_unavailable' ? 'Search temporarily unavailable' : 'No source material found'}</p>{#if ingredient.sources?.length}<div class="evidence-level">Evidence level: {ingredient.highlights?.evidence_level || 'not assessed'}</div><div class="quick-highlights"><div><b>What it is used for</b>{#if ingredient.highlights?.what_it_does?.length}{#each ingredient.highlights.what_it_does as item}<p>{item.text} <a href={item.source_url} target="_blank" rel="noopener noreferrer">Source ↗</a></p>{/each}{:else}<p class="not-found">Not stated in the retrieved excerpts.</p>{/if}</div><div><b>How it works</b>{#if ingredient.highlights?.how_it_works?.length}{#each ingredient.highlights.how_it_works as item}<p>{item.text} <a href={item.source_url} target="_blank" rel="noopener noreferrer">Source ↗</a></p>{/each}{:else}<p class="not-found">Mechanism details were not found in the retrieved excerpts.</p>{/if}</div><div><b>Precautions mentioned</b>{#if ingredient.highlights?.precautions?.length}{#each ingredient.highlights.precautions as item}<p>{item.text} <a href={item.source_url} target="_blank" rel="noopener noreferrer">Source ↗</a></p>{/each}{:else}<p class="not-found">No precaution statement was found in these excerpts. This does not mean there are no risks.</p>{/if}</div></div><details class="source-details full-sources"><summary>Open detailed source excerpts ({ingredient.sources.length})</summary>{#each ingredient.sources as source}<div class="ingredient-evidence"><span>{source.source_type}</span><b>{source.title}</b><p>{source.content}</p><a href={source.url} target="_blank" rel="noopener noreferrer">Read reference ↗</a></div>{/each}</details>{:else}<p class="muted-copy">No supporting ingredient information was retrieved. We won't invent an explanation.</p>{/if}</article>{/each}</div>
            {:else}<div class="empty-state"><span>✳</span><b>Composition confirmation is needed first.</b><p>Return to Overview, check the exact ingredients on the packaging, and start ingredient research.</p><button class="text-button" onclick={() => activeTab = 'overview'}>Back to composition ↗</button></div>{/if}
          </div>
        {:else if activeTab === 'how'}
          <div class="result-card wide-card"><div class="result-card-head"><div><span class="card-kicker">TRANSPARENT PROCESS</span><h3>How this result was prepared</h3></div><span class="result-card-icon">⌁</span></div><div class="trace-list">{#each result.trace || [] as item, i}<div class="trace-row"><span>{String(i + 1).padStart(2, '0')}</span><p>{item}</p><i>✓</i></div>{/each}</div>{#if result.ingredients?.length > 1}<div class="notice-box"><b>Why these ingredients are combined</b>{#if result.combination_sources?.length}<p>These sources discuss the combination. Read the source passages and check whether they refer to the same formulation and strength.</p>{#each result.combination_sources as source}<div class="ingredient-evidence"><b>{source.title}</b><p>{source.content}</p><a href={source.url} target="_blank" rel="noopener noreferrer">Read reference ↗</a></div>{/each}{:else}<p>No reliable source explaining this exact combination was retrieved. The app will not guess why the ingredients were combined.</p>{/if}</div>{/if}<div class="notice-box"><b>Why we ask you to confirm the composition</b><p>Similar brand names can refer to different strengths, dosage forms or manufacturers. A text match alone cannot prove that the retrieved label belongs to your exact pack.</p></div></div>
        {:else if activeTab === 'precautions'}
          <div class="result-card wide-card"><div class="result-card-head"><div><span class="card-kicker">BASIC PRECAUTION REVIEW</span><h3>Conditions & known allergies</h3></div><span class="result-card-icon">♡</span></div><p class="muted-copy">This is a limited text-screening aid, not a drug-interaction checker. It cannot establish whether a medicine is safe for you.</p><div class="health-input-grid"><div><label class="field-label" for="conditions">HEALTH CONDITIONS (COMMA SEPARATED)</label><input id="conditions" bind:value={conditionsText} placeholder="e.g. diabetes, asthma" /></div><div><label class="field-label" for="allergies">KNOWN ALLERGIES (COMMA SEPARATED)</label><input id="allergies" bind:value={allergiesText} placeholder="e.g. penicillin, lactose" /></div></div><button class="primary-action" onclick={researchIngredients} disabled={busy || !compositionText.trim()}>Check retrieved precautions <span>→</span></button>{#if result.health_check}<div class="health-result"><b>{result.health_check.status === 'review' ? 'Review recommended' : result.health_check.status === 'not_run' ? 'Not checked yet' : 'No text match found'}</b><p>{result.health_check.message}</p>{#if result.health_check.matched_terms?.length}<small>Matched terms: {result.health_check.matched_terms.join(', ')}</small>{/if}</div>{/if}</div>
        {/if}

        <div class="ask-card"><div class="ask-intro"><span class="ask-icon">✳</span><div><span class="card-kicker">ASK MEDICINE BUDDY</span><h3>Have a question about this result?</h3><p>Answers are retrieved from the sources shown above. If evidence is missing, the assistant will say so.</p></div></div><div class="ask-input-row"><input bind:value={chatQuestion} onkeydown={(e) => e.key === 'Enter' && sendQuestion()} placeholder="Ask about the ingredients or the source information…" /><button onclick={sendQuestion} disabled={chatBusy || !chatQuestion.trim()}>{chatBusy ? 'Searching…' : 'Ask ↗'}</button></div>{#if chatAnswer}<div class="chat-answer"><b>{chatAnswer.grounded ? 'Relevant source passages' : 'Not enough evidence'}</b><p>{chatAnswer.answer}</p>{#each chatAnswer.sources || [] as source}<a href={source.url} target="_blank" rel="noopener noreferrer">{source.title} ↗</a>{/each}</div>{/if}</div>
        {#if result.notices?.length}<div class="notice-stack">{#each result.notices as notice}<p><span>i</span>{notice}</p>{/each}</div>{/if}
      </section>
    {/if}

    <section class="safety-section" id="safety-note"><div class="safety-icon">♡</div><div><div class="eyebrow">A NOTE ON YOUR HEALTH</div><h2>Helpful information.<br /><em>Not a substitute for care.</em></h2><p>Medicine Buddy is an educational tool. Source data can be incomplete, outdated or about a different market or formulation. Always verify the exact pack and ask a qualified pharmacist or doctor before making a medicine decision. Do not start, stop or change treatment based on this website.</p></div></section>
  </main>

  <footer class="footer"><a class="brand" href="/"><span class="brand-mark"><span></span><span></span></span><span>medicine<span class="brand-light">buddy</span></span></a><span>Understand more. Decide with care.</span><span>© 2026 Medicine Buddy · Educational use only</span></footer>
</div>
