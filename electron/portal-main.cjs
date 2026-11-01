// Electron loads the package entry itself; always start the client here.
const { start } = require('./portal.cjs');
const smokeVisible = process.argv.includes('--smoke-test-visible');
const smoke = process.argv.includes('--smoke-test') || smokeVisible;
const ready = start({ profile: process.env.SHIXUE_CLIENT_PROFILE, show: !smoke || smokeVisible });
if (smoke && ready) ready.then(() => setTimeout(() => require('electron').app.quit(), 300));
