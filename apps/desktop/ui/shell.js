// Shared by the app's own pages: Tauri's IPC (withGlobalTauri) and small DOM helpers.
const invoke = (command, args) => window.__TAURI__.core.invoke(command, args);
const $ = (id) => document.getElementById(id);
const show = (id, on) => { $(id).hidden = !on; };
