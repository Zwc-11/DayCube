//! DayCube for Windows: one app that runs the vault API (with the viewer on the same loopback
//! origin), the capture receiver and its eye tracker, shows the viewer in a window, and sits in the
//! tray. See docs/desktop.md.

mod autostart;
mod config;
mod http;
mod layout;
mod packs;
mod process;
mod selftest;
mod services;
mod tray;

use serde::Serialize;
use std::ffi::OsString;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::time::Duration;
use tauri::webview::NewWindowResponse;
use tauri::{AppHandle, Manager, RunEvent, Url, WebviewUrl, WebviewWindowBuilder, WindowEvent};
use tauri_plugin_opener::OpenerExt;

use config::Config;
use layout::Layout;
use packs::{PackState, Packs};
use services::{Service, Spec};

pub const RECEIVER_PORT: u16 = 7700; // the Opera extension's host permission names this port
const API_PORT: u16 = 7701; // the port viewer.cmd uses; another is chosen when it is taken
const MAIN: &str = "main";
const STARTUP: &str = "startup";
const TOOLS: &str = "tools";

#[derive(Clone, Debug, Default, PartialEq)]
pub struct Args {
    pub background: bool,
    pub self_test: bool,
    pub report: Option<PathBuf>,
    pub vault: Option<PathBuf>,
    pub hold_s: u64,
    pub no_capture: bool,
    pub tools: bool,
}

pub fn parse_args(argv: impl IntoIterator<Item = String>) -> Args {
    let mut args = Args::default();
    let mut it = argv.into_iter();
    while let Some(arg) = it.next() {
        match arg.as_str() {
            autostart::BACKGROUND => args.background = true,
            "--self-test" => args.self_test = true,
            "--no-capture" => args.no_capture = true,
            "--tools" => args.tools = true,
            "--report" => args.report = it.next().map(PathBuf::from),
            "--vault" => args.vault = it.next().map(PathBuf::from),
            "--hold" => args.hold_s = it.next().and_then(|s| s.parse().ok()).unwrap_or(0),
            _ => {}
        }
    }
    args
}

/// What the startup page shows.
#[derive(Clone, Debug, Serialize)]
#[serde(tag = "phase", rename_all = "snake_case")]
pub enum Phase {
    Starting { step: String },
    NeedsVault { suggested: Option<String>, new_default: String, error: Option<String> },
    Failed { message: String, log: String },
    Ready { url: String },
}

pub struct Shell {
    layout: Layout,
    exe: PathBuf,
    args: Args,
    config_path: PathBuf,
    config: Mutex<Config>,
    phase: Mutex<Phase>,
    token: Mutex<Option<String>>,
    api: Mutex<Option<Service>>,
    receiver: Mutex<Option<Service>>,
    packs: Packs,
    calibration: Mutex<Option<std::process::Child>>,
    starting: AtomicBool,
}

impl Shell {
    fn vault(&self) -> Option<PathBuf> {
        self.args.vault.clone().or_else(|| self.config.lock().unwrap().vault.clone())
    }

    fn logs(&self) -> Option<PathBuf> {
        self.vault().map(|v| v.join("logs"))
    }

    fn set_phase(&self, phase: Phase) {
        self.note(&format!("{phase:?}"));
        *self.phase.lock().unwrap() = phase;
    }

    /// A line in vault/logs/desktop.log (and stderr): what the app itself did, for diagnosis.
    fn note(&self, line: &str) {
        eprintln!("DayCube: {line}");
        if let Some(logs) = self.logs() {
            use std::io::Write;
            let _ = std::fs::create_dir_all(&logs);
            if let Ok(mut file) = std::fs::OpenOptions::new().create(true).append(true).open(logs.join("desktop.log")) {
                let _ = writeln!(file, "[{}] {line}", services::now());
            }
        }
    }

    fn capture_wanted(&self) -> bool {
        self.config.lock().unwrap().capture && !self.args.no_capture
    }

    fn python_spec(&self, name: &'static str, args: &[&str], vault: &Path, log: &str) -> Spec {
        Spec {
            name,
            program: self.layout.core.clone(),
            args: args.iter().map(OsString::from).collect(),
            env: self.layout.python_env(vault),
            cwd: self.layout.src.clone(),
            log: vault.join("logs").join(log),
        }
    }

    fn receiver_spec(&self, vault: &Path) -> Spec {
        self.python_spec(
            "capture receiver",
            &["-m", "capture", "run", "--receive", "--gaze"],
            vault,
            "desktop-receiver.log",
        )
    }

    /// End every service the app started.
    fn shutdown(&self) {
        if let Some(mut service) = self.receiver.lock().unwrap().take() {
            service.stop();
        }
        if let Some(mut service) = self.api.lock().unwrap().take() {
            service.stop();
        }
        if let Some(mut child) = self.calibration.lock().unwrap().take() {
            let _ = child.kill();
        }
    }
}

/// The script the viewer window runs before the page's own: the vault token, on our origin only.
pub fn bootstrap(origin: &str, token: &str, version: &str) -> String {
    let json = |s: &str| serde_json::to_string(s).unwrap();
    format!(
        "(function(){{if(window.location.origin!=={o})return;\
         Object.defineProperty(window,'__DAYCUBE_DESKTOP__',{{value:Object.freeze({{token:{t},version:{v}}}),writable:false,configurable:false}});}})();",
        o = json(origin),
        t = json(token),
        v = json(version)
    )
}

pub fn same_origin(url: &Url, origin: &str) -> bool {
    url.origin().ascii_serialization() == origin
}

fn open_external(app: &AppHandle, url: &Url) {
    if matches!(url.scheme(), "http" | "https" | "mailto") {
        let _ = app.opener().open_url(url.as_str(), None::<&str>);
    }
}

fn show_window(app: &AppHandle, label: &str, page: &str, title: &str, size: (f64, f64)) {
    if let Some(window) = app.get_webview_window(label) {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
        return;
    }
    let built = WebviewWindowBuilder::new(app, label, WebviewUrl::App(page.into()))
        .title(title)
        .inner_size(size.0, size.1)
        .min_inner_size(480.0, 360.0)
        .center()
        .build();
    if let Err(error) = built {
        eprintln!("DayCube: could not open the {label} window: {error}");
    }
}

pub fn show_startup(app: &AppHandle) {
    show_window(app, STARTUP, "index.html", "DayCube", (620.0, 460.0));
}

pub fn show_tools(app: &AppHandle) {
    show_window(app, TOOLS, "tools.html", "DayCube tools", (760.0, 720.0));
}

/// The viewer, or the startup page while the viewer is not ready.
pub fn show_main(app: &AppHandle) {
    match app.get_webview_window(MAIN) {
        Some(window) => {
            let _ = window.unminimize();
            let _ = window.show();
            let _ = window.set_focus();
        }
        None => show_startup(app),
    }
}

fn open_main(app: &AppHandle, port: u16, token: &str, visible: bool) -> tauri::Result<()> {
    if let Some(old) = app.get_webview_window(MAIN) {
        old.destroy()?; // its script carries the old port and token
    }
    let origin = format!("http://127.0.0.1:{port}");
    let url: Url = format!("{origin}/").parse().expect("loopback URL");
    let (nav_app, nav_origin) = (app.clone(), origin.clone());
    let (new_app, new_origin) = (app.clone(), origin.clone());
    WebviewWindowBuilder::new(app, MAIN, WebviewUrl::External(url))
        .title("DayCube")
        .inner_size(1440.0, 900.0)
        .min_inner_size(900.0, 600.0)
        .center()
        .visible(visible)
        .initialization_script(bootstrap(&origin, token, env!("CARGO_PKG_VERSION")))
        .on_navigation(move |url| {
            if same_origin(url, &nav_origin) || url.as_str() == "about:blank" {
                return true;
            }
            open_external(&nav_app, url);
            false
        })
        .on_new_window(move |url, _| {
            if same_origin(&url, &new_origin) {
                if let Some(main) = new_app.get_webview_window(MAIN) {
                    let _ = main.navigate(url);
                }
            } else {
                open_external(&new_app, &url);
            }
            NewWindowResponse::Deny
        })
        .build()?;
    Ok(())
}

/// Start (or restart) everything, off the main thread. The startup page follows ``Shell::phase``.
pub fn start(app: &AppHandle) {
    let shell = app.state::<Shell>();
    if shell.starting.swap(true, Ordering::SeqCst) {
        return;
    }
    let app = app.clone();
    std::thread::spawn(move || {
        let shell = app.state::<Shell>();
        let outcome = startup(&app, &shell);
        shell.starting.store(false, Ordering::SeqCst);
        if let Err(message) = outcome {
            let log = shell.logs().map(|l| services::tail(&l.join("desktop-api.log"), 6000)).unwrap_or_default();
            shell.set_phase(Phase::Failed { message: message.clone(), log });
            if shell.args.self_test {
                finish_self_test(&app, &shell, selftest::Report { problems: vec![message], ..Default::default() });
            } else {
                show_startup(&app);
            }
        }
    });
}

fn startup(app: &AppHandle, shell: &Shell) -> Result<(), String> {
    let Some(vault) = shell.vault() else {
        let candidates = config::vault_candidates();
        shell.set_phase(Phase::NeedsVault {
            suggested: config::existing_vault(&candidates).map(|p| p.display().to_string()),
            new_default: config::default_new_vault().display().to_string(),
            error: None,
        });
        show_startup(app);
        return Ok(());
    };
    let problems = shell.layout.problems();
    if !problems.is_empty() {
        return Err(format!("This DayCube install is incomplete: {}. Reinstall DayCube.", problems.join("; ")));
    }
    std::fs::create_dir_all(vault.join("logs"))
        .map_err(|e| format!("cannot use the vault folder {}: {e}", vault.display()))?;

    // 1. The API, which also serves the viewer. Restarting means starting it again.
    shell.set_phase(Phase::Starting { step: "Opening your vault".into() });
    if let Some(mut old) = shell.api.lock().unwrap().take() {
        old.stop();
    }
    let preferred = shell.config.lock().unwrap().port.unwrap_or(0);
    let port = http::choose_port(&[preferred, API_PORT]).map_err(|e| format!("no free loopback port: {e}"))?;
    let port_arg = port.to_string();
    let mut spec = shell.python_spec(
        "vault API",
        &[
            "-m",
            "uvicorn",
            "ingest.desktop:create_desktop_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            &port_arg,
            "--log-level",
            "warning",
            "--no-access-log",
        ],
        &vault,
        "desktop-api.log",
    );
    spec.env.push(("DAYCUBE_VIEWER_DIST".into(), shell.layout.viewer.clone().into()));
    if shell.args.self_test {
        spec.env.push(("DAYCUBE_DESKTOP_SELFTEST".into(), "1".into()));
    }
    let api = Service::start(spec);
    let ready = services::wait_ready(&api, Duration::from_secs(180), 3, || {
        http::get(port, "/api/health", Duration::from_secs(2)).is_ok_and(|r| r.status == 200)
    });
    *shell.api.lock().unwrap() = Some(api);
    ready?;
    {
        let mut config = shell.config.lock().unwrap();
        if config.port != Some(port) && !shell.args.self_test {
            config.port = Some(port);
            let _ = config.save(&shell.config_path);
        }
    }
    let token = std::fs::read_to_string(vault.join(".token"))
        .map(|t| t.trim().to_string())
        .map_err(|e| format!("the vault has no token ({e})"))?;
    *shell.token.lock().unwrap() = Some(token.clone());

    // 2. Capture: the receiver, which starts the eye tracker and the GPU workers it finds.
    if shell.capture_wanted() {
        shell.set_phase(Phase::Starting { step: "Starting capture".into() });
        start_receiver(shell, &vault);
    }

    // 3. Start at sign-in, the same entry a checkout's install-autostart writes.
    if !shell.layout.checkout && !shell.args.self_test {
        let wanted = shell.config.lock().unwrap().autostart;
        if let Err(error) = autostart::apply(wanted, &shell.exe) {
            eprintln!("DayCube: could not update the sign-in entry: {error}");
        }
    }

    // 4. The viewer.
    shell.set_phase(Phase::Starting { step: "Opening the viewer".into() });
    open_main(app, port, &token, !shell.args.background)
        .map_err(|e| format!("could not open the viewer window: {e}"))?;
    shell.note("viewer window built");
    shell.set_phase(Phase::Ready { url: format!("http://127.0.0.1:{port}/") });
    if let Some(window) = app.get_webview_window(STARTUP) {
        let _ = window.close();
    }
    if shell.args.self_test {
        let (app, receiver_required) = (app.clone(), shell.capture_wanted());
        std::thread::spawn(move || {
            let probe_app = app.clone();
            let probe = move || {
                if let Some(window) = probe_app.get_webview_window(MAIN) {
                    let _ = window.eval(selftest::PROBE);
                }
            };
            let report = selftest::run(port, receiver_required, Duration::from_secs(240), probe);
            app.state::<Shell>().note(&format!("self-test finished: pass={}", report.pass));
            finish_self_test(&app, &app.state::<Shell>(), report);
        });
    }
    Ok(())
}

/// Stop any receiver on 127.0.0.1:7700 (a checkout's, or ours), then start the app's.
fn start_receiver(shell: &Shell, vault: &Path) {
    if let Some(mut old) = shell.receiver.lock().unwrap().take() {
        old.stop();
    }
    if http::get(RECEIVER_PORT, "/status", Duration::from_secs(2)).is_ok() {
        // Started outside the app (capture.cmd, a checkout's sign-in entry): the app takes over.
        shell.note("a receiver started outside the app answers on 127.0.0.1:7700: stopping it");
        let stopper = shell.python_spec("receiver stop", &["-m", "capture", "stop"], vault, "desktop-stop.log");
        let outcome = run_to_end(&stopper, Duration::from_secs(90));
        shell.note(&format!("capture stop: {}", outcome.unwrap_or_else(|e| e).trim()));
    }
    *shell.receiver.lock().unwrap() = Some(Service::start(shell.receiver_spec(vault)));
}

static SELF_TEST_DONE: AtomicBool = AtomicBool::new(false);

/// A self-test must end with a report. If the app stalls anywhere, this ends it after
/// ``limit`` with a report naming the last step reached (exit code 2).
fn self_test_watchdog(app: AppHandle, limit: Duration) {
    std::thread::spawn(move || {
        std::thread::sleep(limit);
        if SELF_TEST_DONE.swap(true, Ordering::SeqCst) {
            return;
        }
        let shell = app.state::<Shell>();
        let phase = format!("{:?}", shell.phase.lock().unwrap());
        let report = selftest::Report {
            problems: vec![format!("stalled after {} s at: {phase}", limit.as_secs())],
            ..Default::default()
        };
        shell.note(&report.problems[0]);
        write_report(&shell, &report);
        shell.shutdown();
        std::process::exit(2);
    });
}

fn write_report(shell: &Shell, report: &selftest::Report) {
    let path = shell.args.report.clone().or_else(|| shell.logs().map(|l| l.join("selftest.json")));
    if let Some(path) = path {
        let _ = std::fs::write(&path, serde_json::to_vec_pretty(report).unwrap_or_default());
    }
    println!("{}", serde_json::to_string(report).unwrap_or_default());
}

fn finish_self_test(app: &AppHandle, shell: &Shell, report: selftest::Report) {
    if SELF_TEST_DONE.swap(true, Ordering::SeqCst) {
        return; // the watchdog already reported
    }
    write_report(shell, &report);
    std::thread::sleep(Duration::from_secs(shell.args.hold_s));
    shell.shutdown();
    app.exit(if report.pass { 0 } else { 1 });
}

/// Run a Python command to the end (or ``limit``) and return what it printed (doctor, receiver
/// stop). Its output goes to ``spec.log``, replaced each run, which keeps the last run for diagnosis.
fn run_to_end(spec: &Spec, limit: Duration) -> Result<String, String> {
    let fail = |e: std::io::Error| format!("{}: {e}", spec.log.display());
    let log = std::fs::File::create(&spec.log).map_err(fail)?;
    let mut command = spec.command();
    command.stdin(std::process::Stdio::null()).stdout(log.try_clone().map_err(fail)?).stderr(log);
    let mut tree =
        process::Tree::spawn(&mut command).map_err(|e| format!("could not start {}: {e}", spec.program.display()))?;
    let until = std::time::Instant::now() + limit;
    while matches!(tree.try_wait(), Ok(None)) && std::time::Instant::now() < until {
        std::thread::sleep(Duration::from_millis(100));
    }
    tree.kill(); // anything it left running, or itself after the limit
    std::fs::read_to_string(&spec.log).map_err(fail)
}

// ---- Commands for the app's own pages (startup, tools). The viewer window has no IPC. ----

#[tauri::command]
fn startup_state(shell: tauri::State<Shell>) -> Phase {
    shell.phase.lock().unwrap().clone()
}

#[tauri::command]
fn use_vault(app: AppHandle, shell: tauri::State<Shell>, path: String) -> Result<(), String> {
    let path = PathBuf::from(path.trim());
    config::usable_vault(&path)?;
    std::fs::create_dir_all(&path).map_err(|e| format!("cannot create {}: {e}", path.display()))?;
    {
        let mut config = shell.config.lock().unwrap();
        config.vault = Some(path);
        config.save(&shell.config_path).map_err(|e| format!("cannot save the app's settings: {e}"))?;
    }
    start(&app);
    Ok(())
}

#[tauri::command]
async fn pick_folder(app: AppHandle) -> Option<String> {
    use tauri_plugin_dialog::DialogExt;
    tauri::async_runtime::spawn_blocking(move || {
        app.dialog().file().set_title("Your DayCube vault").blocking_pick_folder()
    })
    .await
    .ok()
    .flatten()
    .and_then(|p| p.into_path().ok())
    .map(|p| p.display().to_string())
}

#[tauri::command]
fn retry(app: AppHandle) {
    start(&app);
}

#[tauri::command]
fn open_viewer(app: AppHandle) {
    show_main(&app);
}

#[tauri::command]
fn open_tools(app: AppHandle) {
    show_tools(&app);
}

#[derive(Serialize)]
struct ToolsState {
    version: &'static str,
    vault: Option<String>,
    capture: bool,
    autostart: bool,
    checkout: bool,
    extension: String,
    receiver: Option<services::State>,
    speech: PackState,
    recon: PackState,
    calibrating: bool,
    gaze_installed: bool,
}

#[tauri::command]
fn tools_state(shell: tauri::State<Shell>) -> ToolsState {
    let config = shell.config.lock().unwrap().clone();
    let calibrating = shell.calibration.lock().unwrap().as_mut().is_some_and(|c| matches!(c.try_wait(), Ok(None)));
    ToolsState {
        version: env!("CARGO_PKG_VERSION"),
        vault: shell.vault().map(|v| v.display().to_string()),
        capture: config.capture,
        autostart: config.autostart,
        checkout: shell.layout.checkout,
        extension: shell.layout.extension.display().to_string(),
        receiver: shell.receiver.lock().unwrap().as_ref().map(Service::state),
        speech: shell.packs.state(&shell.layout, "speech"),
        recon: shell.packs.state(&shell.layout, "recon"),
        calibrating,
        gaze_installed: shell.layout.gaze.is_some(),
    }
}

#[tauri::command]
async fn run_doctor(app: AppHandle) -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let shell = app.state::<Shell>();
        let vault = shell.vault().ok_or("choose a vault first")?;
        let spec = shell.python_spec("doctor", &["-m", "capture", "doctor"], &vault, "desktop-doctor.log");
        run_to_end(&spec, Duration::from_secs(180))
    })
    .await
    .map_err(|e| e.to_string())?
}

#[tauri::command]
fn calibrate(shell: tauri::State<Shell>) -> Result<(), String> {
    let vault = shell.vault().ok_or("choose a vault first")?;
    let gaze = shell.layout.gaze.clone().ok_or("the eye tracker is not installed")?;
    let mut running = shell.calibration.lock().unwrap();
    if running.as_mut().is_some_and(|c| matches!(c.try_wait(), Ok(None))) {
        return Err("calibration is already open".into());
    }
    let log = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(vault.join("logs").join("calibrate.log"))
        .map_err(|e| e.to_string())?;
    let spec = Spec {
        name: "calibration",
        program: gaze,
        args: ["-m", "gaze", "calibrate", "--vault"].iter().map(OsString::from).chain([vault.clone().into()]).collect(),
        env: shell.layout.python_env(&vault),
        cwd: shell.layout.src.clone(),
        log: vault.join("logs").join("calibrate.log"),
    };
    let child = spec
        .command()
        .stdin(std::process::Stdio::null())
        .stdout(log.try_clone().map_err(|e| e.to_string())?)
        .stderr(log)
        .spawn()
        .map_err(|e| format!("could not start calibration: {e}"))?;
    *running = Some(child);
    Ok(())
}

fn restart_receiver(app: &AppHandle) {
    let shell = app.state::<Shell>();
    if let (true, Some(vault)) = (shell.capture_wanted(), shell.vault()) {
        start_receiver(&shell, &vault);
    }
}

#[tauri::command]
fn install_pack(app: AppHandle, shell: tauri::State<Shell>, name: String) -> Result<(), String> {
    let after = app.clone();
    // The receiver looks for the packs when it starts: restart it once one is in.
    shell.packs.install(&shell.layout, &name, move || {
        std::thread::spawn(move || restart_receiver(&after));
    })
}

#[tauri::command]
fn set_capture(app: AppHandle, shell: tauri::State<Shell>, on: bool) -> Result<(), String> {
    {
        let mut config = shell.config.lock().unwrap();
        config.capture = on;
        config.save(&shell.config_path).map_err(|e| e.to_string())?;
    }
    if on {
        std::thread::spawn(move || restart_receiver(&app));
    } else if let Some(mut service) = shell.receiver.lock().unwrap().take() {
        service.stop();
    }
    Ok(())
}

#[tauri::command]
fn set_autostart(app: AppHandle, on: bool) -> Result<(), String> {
    apply_autostart(&app, on)
}

pub fn apply_autostart(app: &AppHandle, on: bool) -> Result<(), String> {
    let shell = app.state::<Shell>();
    {
        let mut config = shell.config.lock().unwrap();
        config.autostart = on;
        config.save(&shell.config_path).map_err(|e| e.to_string())?;
    }
    if !shell.layout.checkout {
        autostart::apply(on, &shell.exe).map_err(|e| e.to_string())?;
    }
    tray::refresh(app);
    Ok(())
}

#[tauri::command]
fn open_folder(app: AppHandle, shell: tauri::State<Shell>, which: String) -> Result<(), String> {
    let path = match which.as_str() {
        "logs" => shell.logs(),
        "vault" => shell.vault(),
        "extension" => Some(shell.layout.extension.clone()),
        "packs" => Some(shell.layout.packs.clone()),
        _ => None,
    }
    .ok_or("nothing to open")?;
    let _ = std::fs::create_dir_all(&path);
    app.opener().open_path(path.display().to_string(), None::<&str>).map_err(|e| e.to_string())
}

pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, argv, _| {
            let args = parse_args(argv.into_iter().skip(1));
            if args.tools {
                show_tools(app);
            } else if !args.background {
                show_main(app);
            }
        }))
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .setup(|app| {
            let args = parse_args(std::env::args().skip(1));
            let data = app.path().app_local_data_dir()?;
            // DAYCUBE_DEV_ROOT: a repository checkout's environments. DAYCUBE_RESOURCES: a bundle
            // assembled by scripts/bundle-runtimes.py, run without installing (tests).
            let layout = match (std::env::var_os("DAYCUBE_DEV_ROOT"), std::env::var_os("DAYCUBE_RESOURCES")) {
                (Some(root), _) => Layout::checkout(Path::new(&root), data.join("packs")),
                (None, Some(resources)) => Layout::installed(Path::new(&resources), data.join("packs")),
                (None, None) => Layout::installed(&app.path().resource_dir()?, data.join("packs")),
            };
            let config_path = app.path().app_config_dir()?.join("settings.json");
            let config = Config::load(&config_path);
            let (background, tools) = (args.background, args.tools);
            app.manage(Shell {
                layout,
                exe: std::env::current_exe()?,
                args,
                config_path,
                config: Mutex::new(config),
                phase: Mutex::new(Phase::Starting { step: "Starting".into() }),
                token: Mutex::new(None),
                api: Mutex::new(None),
                receiver: Mutex::new(None),
                packs: Packs::default(),
                calibration: Mutex::new(None),
                starting: AtomicBool::new(false),
            });
            tray::build(app.handle())?;
            if app.state::<Shell>().args.self_test {
                self_test_watchdog(app.handle().clone(), Duration::from_secs(420));
            }
            if !background {
                show_startup(app.handle());
            }
            if tools {
                show_tools(app.handle());
            }
            start(app.handle());
            Ok(())
        })
        .on_window_event(|window, event| {
            // Closing the viewer keeps DayCube (and recording) running in the tray.
            if let WindowEvent::CloseRequested { api, .. } = event {
                if window.label() == MAIN {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .invoke_handler(tauri::generate_handler![
            startup_state,
            use_vault,
            pick_folder,
            retry,
            open_viewer,
            open_tools,
            tools_state,
            run_doctor,
            calibrate,
            install_pack,
            set_capture,
            set_autostart,
            open_folder
        ])
        .build(tauri::generate_context!())
        .expect("DayCube could not start");
    app.run(|app, event| match event {
        // The last window closing does not quit: DayCube lives in the tray. Quit (code set) does.
        RunEvent::ExitRequested { api, code: None, .. } => api.prevent_exit(),
        RunEvent::Exit => app.state::<Shell>().shutdown(),
        _ => {}
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn arguments() {
        let a = parse_args(
            ["--self-test", "--vault", "C:\\v", "--report", "r.json", "--hold", "15", "--no-capture"].map(String::from),
        );
        assert!(a.self_test && a.no_capture && !a.background);
        assert_eq!(a.vault, Some(PathBuf::from("C:\\v")));
        assert_eq!(a.report, Some(PathBuf::from("r.json")));
        assert_eq!(a.hold_s, 15);
        assert!(parse_args(["--background".to_string()]).background);
        assert_eq!(parse_args(["--hold".to_string()]).hold_s, 0);
    }

    #[test]
    fn token_script_is_bound_to_our_origin() {
        let script = bootstrap("http://127.0.0.1:7701", "tok\"en</script>", "0.1.0");
        assert!(script.contains("window.location.origin!==\"http://127.0.0.1:7701\""));
        // JSON-escaped: a token cannot end the string or the statement.
        assert!(script.contains(r#"token:"tok\"en</script>""#));
        assert!(script.contains("configurable:false"));
    }

    #[test]
    fn navigation_stays_on_the_loopback_origin() {
        let origin = "http://127.0.0.1:7701";
        assert!(same_origin(&"http://127.0.0.1:7701/2026-10-20?x=1".parse().unwrap(), origin));
        assert!(!same_origin(&"http://127.0.0.1:7702/".parse().unwrap(), origin));
        assert!(!same_origin(&"http://localhost:7701/".parse().unwrap(), origin));
        assert!(!same_origin(&"https://evil.example/".parse().unwrap(), origin));
    }
}
