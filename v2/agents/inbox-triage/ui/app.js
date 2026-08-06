const client = agentd.fromPage({ clientName: 'inbox-triage-ui' });
const sessionKey = 'triage-manual-' + Date.now();

const timeInput = document.getElementById('triage-time');
const saveBtn = document.getElementById('save-time');
const saveStatus = document.getElementById('save-status');

const runBtn = document.getElementById('run-now');
const runStatus = document.getElementById('run-status');
const resultsDiv = document.getElementById('results');

// Load existing config
async function loadConfig() {
  try {
    const cfg = await client.config.get('inbox-triage');
    if (cfg && cfg.triage_time) {
      timeInput.value = cfg.triage_time;
    }
  } catch (e) {
    console.error('Failed to load config', e);
  }
}

// Save config
saveBtn.addEventListener('click', async () => {
  try {
    saveBtn.disabled = true;
    await client.config.set('inbox-triage', { triage_time: timeInput.value });
    saveStatus.textContent = 'Time saved successfully.';
    setTimeout(() => { saveStatus.textContent = ''; }, 3000);
  } catch (e) {
    saveStatus.textContent = 'Error saving time.';
  } finally {
    saveBtn.disabled = false;
  }
});

// Run manual triage
runBtn.addEventListener('click', async () => {
  runBtn.disabled = true;
  runStatus.textContent = 'Running triage...';
  resultsDiv.textContent = '';
  resultsDiv.style.display = 'block';

  try {
    await client.send({
      message: 'Run the inbox triage right now and report the results.',
      sessionKey,
      agentId: 'inbox-triage'
    });
  } catch (e) {
    runStatus.textContent = 'Failed to start.';
    runBtn.disabled = false;
  }
});

// Handle run events
let rawMarkdown = '';

client.onRun(sessionKey, (payload) => {
  const ev = payload.event;
  
  switch (ev.type) {
    case 'message_update':
      if (ev.kind === 'text_delta') {
        rawMarkdown += ev.delta;
        if (typeof marked !== 'undefined') {
          resultsDiv.innerHTML = marked.parse(rawMarkdown);
        } else {
          resultsDiv.textContent = rawMarkdown;
        }
      }
      break;
    case 'agent_end':
      runStatus.textContent = 'Done.';
      runBtn.disabled = false;
      if (ev.error) {
        resultsDiv.innerHTML += '<br><br><strong>Error:</strong> ' + ev.error;
      }
      // Reset raw markdown buffer for next run
      rawMarkdown = '';
      break;
  }
});

client.onStatus((s) => {
  if (s === 'open') {
    loadConfig();
  }
});