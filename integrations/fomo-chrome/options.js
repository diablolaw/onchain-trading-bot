document.getElementById('save').addEventListener('click', async () => {
  const key = document.getElementById('key').value.trim();
  if (!/^[a-f0-9]{64}$/.test(key)) {document.getElementById('status').textContent = 'Expected the 64-character local pairing key.'; return;}
  await chrome.storage.local.set({bridgeKey: key});
  document.getElementById('key').value = '';
  document.getElementById('status').textContent = 'Saved. Reload FOMO to observe its session requests.';
});
