// This is a placeholder for your UI's JavaScript logic.
const client = agentd.fromPage({ clientName: 'linkedin-job-finder/ui' });

client.onStatus((s) => console.log('Client status:', s));
client.onRun('default', (payload) => console.log('Run event:', payload));

console.log('LinkedIn Job Finder UI loaded.');