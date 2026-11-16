//! GPU packs: the speech worker's and the 3D/memories (recon) environments. Each is CUDA PyTorch,
//! 3–4 GB, over the 2 GB that an installer or a release file may hold, so the app installs them on
//! request with its bundled uv, from the same pyproject.toml and uv.lock the repository uses
//! (``uv sync --locked``), into ``%LOCALAPPDATA%\app.daycube.desktop\packs\<name>``. uv's output
//! goes to ``packs\<name>.log``; the tools page shows its end.

use serde::Serialize;
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};

use crate::layout::{Layout, PACKS};
use crate::process::Tree;
use crate::services::tail;

#[derive(Clone, Debug, Default, Serialize)]
pub struct PackState {
    pub installed: bool,
    pub installing: bool,
    pub failed: Option<String>,
    pub log: String,
}

#[derive(Clone, PartialEq)]
enum Install {
    Running,
    Done,
    Failed(String),
}

/// Each pack's install in this run of the app.
#[derive(Default)]
pub struct Packs(Arc<Mutex<HashMap<String, Install>>>);

/// The uv command that installs ``name``: the pack's own project, locked, into the packs folder,
/// on the app's own Python (no download of another one).
pub fn sync_command(layout: &Layout, uv: &Path, name: &str) -> Command {
    let mut command = Command::new(uv);
    command
        .args(["sync", "--locked", "--no-dev", "--project"])
        .arg(layout.src.join("services").join(name))
        .arg("--python")
        .arg(&layout.core)
        .env("UV_PROJECT_ENVIRONMENT", layout.packs.join(name))
        .env("UV_CACHE_DIR", layout.packs.join(".uv-cache"))
        .env("UV_PYTHON_DOWNLOADS", "never")
        .env("UV_LINK_MODE", "copy")
        .env("NO_COLOR", "1")
        .env_remove("VIRTUAL_ENV")
        .stdin(Stdio::null());
    command
}

fn log_path(layout: &Layout, name: &str) -> PathBuf {
    layout.packs.join(format!("{name}.log"))
}

impl Packs {
    pub fn state(&self, layout: &Layout, name: &str) -> PackState {
        let last = self.0.lock().unwrap().get(name).cloned();
        PackState {
            installed: layout.pack_python(name, false).is_some(),
            installing: last == Some(Install::Running),
            failed: match last {
                Some(Install::Failed(why)) => Some(why),
                _ => None,
            },
            log: tail(&log_path(layout, name), 4000),
        }
    }

    /// Start installing ``name`` in the background; ``done`` runs after a success.
    pub fn install(&self, layout: &Layout, name: &str, done: impl FnOnce() + Send + 'static) -> Result<(), String> {
        if !PACKS.contains(&name) {
            return Err(format!("no pack named {name}"));
        }
        let uv = layout
            .uv
            .clone()
            .ok_or("this build has no bundled uv; in a checkout, run uv sync --project services/<name>")?;
        if self.0.lock().unwrap().insert(name.to_string(), Install::Running) == Some(Install::Running) {
            return Err(format!("{name} is already installing"));
        }
        let (log, mut command) = (log_path(layout, name), sync_command(layout, &uv, name));
        let (jobs, name) = (self.0.clone(), name.to_string());
        std::thread::spawn(move || {
            let outcome = run(&mut command, &log);
            let ok = outcome.is_ok();
            jobs.lock().unwrap().insert(name, outcome.map_or_else(Install::Failed, |()| Install::Done));
            if ok {
                done();
            }
        });
        Ok(())
    }
}

fn run(command: &mut Command, log: &Path) -> Result<(), String> {
    if let Some(dir) = log.parent() {
        std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    }
    let file = std::fs::File::create(log).map_err(|e| e.to_string())?;
    command.stdout(file.try_clone().map_err(|e| e.to_string())?).stderr(file);
    let mut tree = Tree::spawn(command).map_err(|e| format!("could not start uv: {e}"))?;
    loop {
        match tree.try_wait() {
            Ok(Some(status)) if status.success() => return Ok(()),
            Ok(Some(status)) => return Err(format!("uv failed ({status}); see {}", log.display())),
            Ok(None) => std::thread::sleep(std::time::Duration::from_millis(200)),
            Err(e) => return Err(e.to_string()),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::ffi::OsString;

    #[test]
    fn sync_command_is_locked_and_kept_out_of_the_install_folder() {
        let layout = Layout::installed(Path::new("/app/res"), PathBuf::from("/data/packs"));
        let command = sync_command(&layout, Path::new("/app/res/runtime/uv"), "speech");
        let args: Vec<_> = command.get_args().collect();
        assert_eq!(&args[..3], ["sync", "--locked", "--no-dev"]);
        // Paths compare by component: Windows joins with `\`.
        let project = Path::new("/app/res").join("src").join("services").join("speech");
        assert!(args.iter().any(|a| Path::new(a) == project), "{args:?}");
        let envs: HashMap<_, _> = command.get_envs().filter_map(|(k, v)| Some((k.to_os_string(), v?))).collect();
        assert_eq!(Path::new(envs[&OsString::from("UV_PROJECT_ENVIRONMENT")]), Path::new("/data/packs").join("speech"));
        assert_eq!(envs[&OsString::from("UV_PYTHON_DOWNLOADS")], "never");
    }

    #[test]
    fn unknown_pack_and_missing_uv_are_refused() {
        let layout = Layout::installed(Path::new("/nonexistent"), PathBuf::from("/nonexistent/packs"));
        let packs = Packs::default();
        assert!(packs.install(&layout, "bitcoin", || {}).unwrap_err().contains("no pack"));
        assert!(packs.install(&layout, "speech", || {}).unwrap_err().contains("uv"));
        assert!(!packs.state(&layout, "speech").installed);
    }

    #[cfg(unix)]
    #[test]
    fn a_failed_install_is_reported_with_its_log() {
        let dir = std::env::temp_dir().join(format!("daycube-pack-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        // A stand-in uv that prints to both streams and fails.
        let uv = dir.join("uv");
        std::fs::write(&uv, "#!/bin/sh\necho resolving\necho 'error: no network' >&2\nexit 2\n").unwrap();
        std::fs::set_permissions(&uv, std::os::unix::fs::PermissionsExt::from_mode(0o755)).unwrap();
        let mut layout = Layout::installed(&dir, dir.join("packs"));
        layout.uv = Some(uv);
        let packs = Packs::default();
        packs.install(&layout, "speech", || panic!("must not run after a failure")).unwrap();
        assert!(packs.install(&layout, "speech", || {}).unwrap_err().contains("already"));
        let start = std::time::Instant::now();
        while packs.state(&layout, "speech").installing {
            assert!(start.elapsed() < std::time::Duration::from_secs(10));
            std::thread::sleep(std::time::Duration::from_millis(50));
        }
        let state = packs.state(&layout, "speech");
        assert!(state.failed.unwrap().contains("uv failed"));
        assert!(state.log.contains("resolving") && state.log.contains("error: no network"), "{}", state.log);
    }
}
