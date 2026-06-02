// {{name}} — wires UI buttons to picolet IPC commands.
const resultEl = document.getElementById('result');

document.getElementById('btn-greet').addEventListener('click', async () => {
  resultEl.className = '';
  try {
    const msg = await window.picolet.invoke('greet', { name: 'World' });
    resultEl.textContent = msg;
  } catch (err) {
    resultEl.className = 'error';
    resultEl.textContent = err.name + ': ' + err.message;
  }
});

document.getElementById('btn-fail').addEventListener('click', async () => {
  resultEl.className = '';
  try {
    await window.picolet.invoke('fail_example');
    resultEl.textContent = 'no error (unexpected)';
  } catch (err) {
    resultEl.className = 'error';
    resultEl.textContent = err.name + ': ' + err.message;
  }
});
