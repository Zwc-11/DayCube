// The tools window: capture status and switches, doctor, calibration, GPU packs.
let packShown = null;

function receiverText(s) {
  if (!s.capture) return 'Capture is off';
  if (!s.receiver) return 'Starting…';
  if (s.receiver.running) return `Receiver running (started ${s.receiver.starts}×)`;
  return `Receiver stopped: ${s.receiver.last_exit || 'restarting'}`;
}

async function refresh() {
  const s = await invoke('tools_state');
  $('version').textContent = `v${s.version}`;
  $('receiver').textContent = receiverText(s);
  $('receiver').className = 'grow ' + (s.receiver && !s.receiver.running ? 'bad' : '');
  $('capture').checked = s.capture;
  $('autostart').checked = s.autostart;
  $('autostart').disabled = s.checkout;
  $('calibrate').disabled = !s.gaze_installed || s.calibrating;
  $('calibrate').textContent = s.calibrating ? 'Calibrating…' : 'Calibrate eye tracker';
  $('extension').textContent = s.extension;
  $('vault').textContent = s.vault || 'not chosen';
  for (const row of document.querySelectorAll('[data-pack]')) {
    const name = row.dataset.pack;
    const p = s[name];
    const state = row.querySelector('.state');
    const button = row.querySelector('button');
    state.textContent = p.installing ? 'installing…' : p.installed ? 'installed' : p.failed ? 'failed' : '';
    state.className = 'state ' + (p.installed ? 'good' : p.failed ? 'bad' : 'muted');
    button.disabled = p.installing || s.checkout;
    button.textContent = p.installed ? 'Reinstall' : 'Install';
    if (packShown === name) $('pack-out').textContent = (p.failed ? `${p.failed}\n` : '') + p.log;
  }
}

$('capture').onchange = (e) => invoke('set_capture', { on: e.target.checked }).catch(alert).finally(refresh);
$('autostart').onchange = (e) => invoke('set_autostart', { on: e.target.checked }).catch(alert).finally(refresh);
$('open').onclick = () => invoke('open_viewer');
$('calibrate').onclick = () => invoke('calibrate').catch(alert).finally(refresh);
$('doctor').onclick = async () => {
  $('doctor').disabled = true;
  $('doctor-out').textContent = 'Checking…';
  try {
    $('doctor-out').textContent = await invoke('run_doctor');
  } catch (error) {
    $('doctor-out').textContent = String(error);
  }
  $('doctor').disabled = false;
};
for (const row of document.querySelectorAll('[data-pack]')) {
  row.querySelector('button').onclick = () => {
    packShown = row.dataset.pack;
    invoke('install_pack', { name: row.dataset.pack }).catch((e) => { $('pack-out').textContent = String(e); }).finally(refresh);
  };
}
$('extension-open').onclick = () => invoke('open_folder', { which: 'extension' }).catch(alert);
$('vault-open').onclick = () => invoke('open_folder', { which: 'vault' }).catch(alert);
$('logs-open').onclick = () => invoke('open_folder', { which: 'logs' }).catch(alert);
refresh();
setInterval(refresh, 1500);
