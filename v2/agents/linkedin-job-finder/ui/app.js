// This is a placeholder for your UI's JavaScript logic.
const client = agentd.fromPage({ clientName: 'linkedin-job-finder/ui' });

let booted = false;
client.onStatus((s) => {
  console.log('Client status:', s);
  // Once per page, not once per reconnect.
  if (s === 'open' && !booted) {
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
    })();
  }
});
client.onRun('default', (payload) => console.log('Run event:', payload));

console.log('LinkedIn Job Finder UI loaded.');