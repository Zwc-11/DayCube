//! A Python service kept running: started, restarted when it stops (backing off while it keeps
//! failing), and ended with everything it started. Output goes to a log file in the vault.

use serde::Serialize;
use std::ffi::OsString;
use std::fs::{File, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use crate::process::Tree;

const LOG_LIMIT: u64 = 5_000_000;
/// Inherited variables that would point the bundled Python at another installation.
const FOREIGN: [&str; 4] = ["PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "PYTHONSTARTUP"];

#[derive(Clone, Debug)]
pub struct Spec {
    pub name: &'static str,
    pub program: PathBuf,
    pub args: Vec<OsString>,
    pub env: Vec<(String, OsString)>,
    pub cwd: PathBuf,
    pub log: PathBuf,
}

impl Spec {
    pub fn command(&self) -> Command {
        let mut command = Command::new(&self.program);
        command.args(&self.args).current_dir(&self.cwd);
        for name in FOREIGN {
            command.env_remove(name);
        }
        for (key, value) in &self.env {
            command.env(key, value);
        }
        command
    }
}

#[derive(Clone, Debug, Default, Serialize)]
pub struct State {
    pub running: bool,
    pub pid: Option<u32>,
    pub starts: u32,
    pub last_exit: Option<String>,
}

pub struct Service {
    pub spec: Spec,
    stop: Arc<AtomicBool>,
    state: Arc<Mutex<State>>,
    thread: Option<JoinHandle<()>>,
}

pub fn now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

fn open_log(path: &Path) -> std::io::Result<File> {
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir)?;
    }
    if std::fs::metadata(path)
        .map(|m| m.len() > LOG_LIMIT)
        .unwrap_or(false)
    {
        let _ = std::fs::rename(path, path.with_extension("log.1"));
    }
    OpenOptions::new().create(true).append(true).open(path)
}

/// The last ``bytes`` of a log, for an error message.
pub fn tail(path: &Path, bytes: u64) -> String {
    let Ok(mut file) = File::open(path) else {
        return String::new();
    };
    let len = file.metadata().map(|m| m.len()).unwrap_or(0);
    let _ = file.seek(SeekFrom::Start(len.saturating_sub(bytes)));
    let mut out = Vec::new();
    let _ = file.read_to_end(&mut out);
    let text = String::from_utf8_lossy(&out).into_owned();
    match text.find('\n') {
        Some(i) if len > bytes => text[i + 1..].to_string(),
        _ => text,
    }
}

fn sleep_unless(stop: &AtomicBool, total: Duration) {
    let until = Instant::now() + total;
    while !stop.load(Ordering::Relaxed) && Instant::now() < until {
        std::thread::sleep(Duration::from_millis(100));
    }
}

impl Service {
    pub fn start(spec: Spec) -> Self {
        let stop = Arc::new(AtomicBool::new(false));
        let state = Arc::new(Mutex::new(State::default()));
        let thread = {
            let (spec, stop, state) = (spec.clone(), stop.clone(), state.clone());
            std::thread::Builder::new()
                .name(format!("service-{}", spec.name))
                .spawn(move || supervise(&spec, &stop, &state))
                .expect("thread")
        };
        Self {
            spec,
            stop,
            state,
            thread: Some(thread),
        }
    }

    pub fn state(&self) -> State {
        self.state.lock().unwrap().clone()
    }

    /// End the service and everything it started, and stop restarting it.
    pub fn stop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

impl Drop for Service {
    fn drop(&mut self) {
        self.stop();
    }
}

fn supervise(spec: &Spec, stop: &AtomicBool, state: &Mutex<State>) {
    let mut delay = Duration::from_secs(2);
    while !stop.load(Ordering::Relaxed) {
        let started = Instant::now();
        let outcome = run_once(spec, stop, state);
        {
            let mut s = state.lock().unwrap();
            s.running = false;
            s.pid = None;
            s.last_exit = Some(outcome.clone());
        }
        if stop.load(Ordering::Relaxed) {
            break;
        }
        if let Ok(mut log) = open_log(&spec.log) {
            let _ = writeln!(
                log,
                "[desktop {}] {} ended: {outcome}; starting again in {} s",
                now(),
                spec.name,
                delay.as_secs()
            );
        }
        // A service that ran a while gets a quick restart; one that keeps failing waits longer.
        delay = if started.elapsed() > Duration::from_secs(120) {
            Duration::from_secs(2)
        } else {
            (delay * 2).min(Duration::from_secs(60))
        };
        sleep_unless(stop, delay);
    }
}

fn run_once(spec: &Spec, stop: &AtomicBool, state: &Mutex<State>) -> String {
    let log = match open_log(&spec.log) {
        Ok(log) => log,
        Err(error) => return format!("cannot write its log {}: {error}", spec.log.display()),
    };
    let mut header = &log;
    let _ = writeln!(
        header,
        "[desktop {}] starting {}: {} {:?}",
        now(),
        spec.name,
        spec.program.display(),
        spec.args
    );
    let (Ok(out), Ok(err)) = (log.try_clone(), log.try_clone()) else {
        return "cannot share its log".into();
    };
    let mut command = spec.command();
    command.stdin(Stdio::null()).stdout(out).stderr(err);
    let mut tree = match Tree::spawn(&mut command) {
        Ok(tree) => tree,
        Err(error) => return format!("could not start {}: {error}", spec.program.display()),
    };
    {
        let mut s = state.lock().unwrap();
        s.running = true;
        s.pid = Some(tree.id());
        s.starts += 1;
    }
    loop {
        if stop.load(Ordering::Relaxed) {
            tree.kill();
            return "stopped by the app".into();
        }
        match tree.try_wait() {
            Ok(Some(status)) => {
                tree.kill(); // whatever it started goes with it
                return format!("exited ({status})");
            }
            Ok(None) => std::thread::sleep(Duration::from_millis(250)),
            Err(error) => {
                tree.kill();
                return format!("lost track of it: {error}");
            }
        }
    }
}

/// Wait until ``ready`` answers true, the service has failed ``max_failures`` times, or ``limit``
/// passes. Err carries why, for the startup page.
pub fn wait_ready(
    service: &Service,
    limit: Duration,
    max_failures: u32,
    ready: impl Fn() -> bool,
) -> Result<(), String> {
    let until = Instant::now() + limit;
    while Instant::now() < until {
        if ready() {
            return Ok(());
        }
        let s = service.state();
        if !s.running && s.starts >= max_failures {
            return Err(format!(
                "{} stopped: {}",
                service.spec.name,
                s.last_exit.unwrap_or_default()
            ));
        }
        std::thread::sleep(Duration::from_millis(300));
    }
    Err(format!(
        "{} did not answer within {} s",
        service.spec.name,
        limit.as_secs()
    ))
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;

    fn temp(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("daycube-svc-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn sh(name: &'static str, script: &str, dir: &Path) -> Spec {
        Spec {
            name,
            program: "sh".into(),
            args: vec!["-c".into(), script.into()],
            env: vec![("DAYCUBE_TEST".into(), "yes".into())],
            cwd: dir.to_path_buf(),
            log: dir.join(format!("{name}.log")),
        }
    }

    #[test]
    fn restarts_a_failing_service_and_logs_it() {
        let dir = temp("fail");
        let mut service = Service::start(sh("flaky", "echo ran $DAYCUBE_TEST; exit 3", &dir));
        let err = wait_ready(&service, Duration::from_secs(20), 2, || false).unwrap_err();
        assert!(err.contains("exited"), "{err}");
        service.stop();
        let log = std::fs::read_to_string(dir.join("flaky.log")).unwrap();
        assert!(log.matches("ran yes").count() >= 2, "{log}");
        assert!(log.contains("starting again"));
    }

    #[test]
    fn stop_ends_a_running_service() {
        let dir = temp("run");
        let mut service = Service::start(sh("sleeper", "echo up; sleep 60", &dir));
        wait_ready(&service, Duration::from_secs(5), 1, || {
            std::fs::read_to_string(dir.join("sleeper.log"))
                .unwrap_or_default()
                .contains("up")
        })
        .unwrap();
        let pid = service.state().pid.unwrap();
        service.stop();
        let state = service.state();
        assert!(!state.running);
        assert_eq!(state.last_exit.as_deref(), Some("stopped by the app"));
        assert!(
            !std::path::Path::new(&format!("/proc/{pid}/cmdline")).exists()
                || std::fs::read_to_string(format!("/proc/{pid}/stat"))
                    .unwrap()
                    .contains(") Z")
        );
    }

    #[test]
    fn foreign_python_variables_are_dropped() {
        std::env::set_var("PYTHONHOME", "/elsewhere");
        let dir = temp("env");
        let mut service = Service::start(sh("env", "echo home=[$PYTHONHOME]; sleep 60", &dir));
        wait_ready(&service, Duration::from_secs(5), 1, || {
            std::fs::read_to_string(dir.join("env.log"))
                .unwrap_or_default()
                .contains("home=")
        })
        .unwrap();
        service.stop();
        assert!(std::fs::read_to_string(dir.join("env.log"))
            .unwrap()
            .contains("home=[]"));
    }

    #[test]
    fn tail_starts_at_a_line() {
        let dir = temp("tail");
        let path = dir.join("x.log");
        std::fs::write(&path, "first line\nsecond line\nthird line\n").unwrap();
        assert_eq!(tail(&path, 16), "third line\n");
        assert_eq!(tail(&path, 1000), "first line\nsecond line\nthird line\n");
        assert_eq!(tail(&dir.join("none.log"), 10), "");
    }
}
