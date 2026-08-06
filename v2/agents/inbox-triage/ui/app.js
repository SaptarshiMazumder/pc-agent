const client = agentd.fromPage({ clientName: 'inbox-triage-ui/1' });
const logEl = document.getElementById('log');

function log(msg) {
    logEl.textContent = msg;
}

client.onStatus((s) => {
    if (s === 'connecting') log('Connecting to agent...');
    if (s === 'open') log('Connected. Ready.');
    if (s === 'closed') log('Disconnected.');
});

let currentMessage = '';

// Listen to all events for this agent so we don't accidentally filter out the right one
client.onAgent('inbox-triage', (payload) => {
    console.log("Agent event:", JSON.stringify(payload));

    if (payload.type === 'tool_execution_start') {
        const toolLabel = payload.toolName || 'tool';
        log(`[Agent is working] Using ${toolLabel}...`);
    } else if (payload.type === 'tool_execution_end') {
        log(`[Agent is working] Finished using tool.`);
    } else if (payload.type === 'message_delta') {
        const text = payload.delta || payload.text || payload.content || '';
        currentMessage += text;
        log(currentMessage);
    } else if (payload.type === 'message_end') {
        const text = agentd.resultText(payload.result);
        currentMessage = '';
        if (text) {
            log(text);
        }
    } else if (payload.type === 'agent_end') {
        if (currentMessage) {
            log(currentMessage);
        }
        currentMessage = ''; 
    }
});

document.getElementById('run-now-btn').addEventListener('click', async () => {
    log('Sending request to run inbox triage...\nWaiting for agent to start...');
    currentMessage = '';
    const activeSessionKey = 'ui-' + Date.now();
    try {
        await client.send({
            message: "Trigger inbox triage now. Check my recent emails, skip the junk/newsletters, and report what actually needs a reply.",
            sessionKey: activeSessionKey,
            agentId: 'inbox-triage'
        });
    } catch (e) {
        log('Error: ' + e.message);
    }
});

document.getElementById('save-schedule-btn').addEventListener('click', async () => {
    const time = document.getElementById('schedule-time').value;
    log('Updating schedule to ' + time + '...\nWaiting for agent...');
    currentMessage = '';
    const activeSessionKey = 'ui-' + Date.now();
    try {
        await client.send({
            message: `Set the daily schedule for the inbox triage to ${time}. Please set up a cron job for this if not already set.`,
            sessionKey: activeSessionKey,
            agentId: 'inbox-triage'
        });
    } catch (e) {
        log('Error: ' + e.message);
    }
});