const client = agentd.fromPage({ clientName: 'inbox-triage-ui' });
const sessionKey = 'triage-manual-' + Date.now();

const timeInput = document.getElementById('triage-time');
const rulesInput = document.getElementById('triage-rules');
const saveBtn = document.getElementById('save-time');
const saveStatus = document.getElementById('save-status');

const runBtn = document.getElementById('run-now');
const runStatus = document.getElementById('run-status');
const toolActivity = document.getElementById('tool-activity');
const resultsDiv = document.getElementById('results');

// Load existing config
async function loadConfig() {
  try {
    const res = await client.request('config.get');
    const cfg = res && res.config && res.config['inbox-triage'] ? res.config['inbox-triage'] : null;
    if (cfg) {
      if (cfg.triage_time) timeInput.value = cfg.triage_time;
      if (cfg.triage_rules) rulesInput.value = cfg.triage_rules;
    }
  } catch (e) {
    console.error('Failed to load config', e);
  }
}

// Save config
saveBtn.addEventListener('click', async () => {
  try {
    saveBtn.disabled = true;
    await client.request('config.set', { 
      patch: {
        'inbox-triage': {
          triage_time: timeInput.value,
          triage_rules: rulesInput.value
        }
      }
    });
    saveStatus.textContent = 'Settings saved successfully.';
    setTimeout(() => { saveStatus.textContent = ''; }, 3000);
  } catch (e) {
    saveStatus.textContent = 'Error saving settings.';
  } finally {
    saveBtn.disabled = false;
  }
});

// Run manual triage
runBtn.addEventListener('click', async () => {
  runBtn.disabled = true;
  runStatus.textContent = 'Running triage...';
  toolActivity.textContent = '';
  resultsDiv.textContent = '';
  resultsDiv.style.display = 'block';

  const customRules = rulesInput.value.trim();
  const rulesPrompt = customRules 
    ? `\n\nApply these CUSTOM TRIAGE RULES when deciding what needs a reply: """${customRules}"""` 
    : '';

  try {
    await client.send({
      message: 'Run the inbox triage right now and report the results.' + rulesPrompt,
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
    case 'tool_execution_start':
      toolActivity.textContent = `Using tool: ${ev.toolName}...`;
      break;
    case 'tool_execution_end':
      toolActivity.textContent = '';
      break;
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

let booted = false;
client.onStatus((s) => {
  if (s === 'open') {
    // Once per page, not once per reconnect: a reconnect must not re-run boot work.
    if (!booted) {
      booted = true;
      void (async () => {
        // AGENTD:COMPONENTS — add_ui_component inserts after this line. Keep the marker.
        try {
          // Hosted sign-in. Renders NOTHING on a BYOK build, when this device is already connected, or
          // when a stored session still works — so it is safe to call unconditionally.
          await agentd.mountSignInGate()
        } catch (e) {
          // The daemon itself is unreachable. Not fatal: the chat surface reports that too, and blocking
          // the whole window on a status probe would hide the better message.
          console.warn('[sign-in]', (e && e.message) || e)
        }
        loadConfig();
      })();
    }
  }
});