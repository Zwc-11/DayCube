// The startup page: progress, the first-run vault choice, and why a start failed.
let filled = false;

async function poll() {
  let state;
  try {
    state = await invoke('startup_state');
  } catch (error) {
    state = { phase: 'failed', message: String(error), log: '' };
  }
  show('starting', state.phase === 'starting' || state.phase === 'ready');
  show('vault', state.phase === 'needs_vault');
  show('failed', state.phase === 'failed');
  if (state.phase === 'starting') $('step').textContent = state.step;
  if (state.phase === 'ready') $('step').textContent = 'Opening the viewer';
  if (state.phase === 'needs_vault' && !filled) {
    filled = true;
    $('vault-path').value = state.suggested || state.new_default;
    $('vault-hint').textContent = state.suggested
      ? 'Found your vault. DayCube will use it as it is.'
      : 'No vault found. DayCube will create one here.';
  }
  if (state.phase === 'failed') {
    $('message').textContent = state.message;
    $('log').textContent = state.log;
  }
  setTimeout(poll, 400);
}

$('browse').onclick = async () => {
  const folder = await invoke('pick_folder');
  if (folder) $('vault-path').value = folder;
};
$('use').onclick = async () => {
  show('vault-error', false);
  try {
    await invoke('use_vault', { path: $('vault-path').value });
  } catch (error) {
    $('vault-error').textContent = String(error);
    show('vault-error', true);
  }
};
$('retry').onclick = () => invoke('retry');
$('logs').onclick = () => invoke('open_folder', { which: 'logs' }).catch(() => {});
$('tools').onclick = () => invoke('open_tools');
poll();
