//! GPU packs: the speech worker's and the 3D/memories (recon) environments. Each is CUDA PyTorch,
//! 3–4 GB, over the 2 GB that an installer or a release file may hold, so the app installs them on
//! request with its bundled uv, from the same pyproject.toml and uv.lock the repository uses
//! (``uv sync --locked``), into ``%LOCALAPPDATA%\app.daycube.desktop\packs\<name>``.

use serde::Serialize;
use std::collections::HashMap;
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};

use crate::layout::Layout;
use crate::process::Tree;

#[derive(Clone, Debug, Default, Serialize)]
pub struct PackState {
    pub installed: bool,
    pub installing: bool,
    pub failed: Option<String>,
    pub lines: Vec<String>,
}

#[derive(Default)]
pub struct Packs {
    jobs: Mutex<HashMap<String, Arc<Mutex<PackState>>>>,
}

/// The uv command that installs ``name``: the pack's own project, locked, into the packs folder,
/// on the app's own Python (no download of another one).
pub fn sync_command(layout: &Layout, uv: &Path, name: &str) -> Command {
    let mut command = Command::new(uv);
    command
        .arg("sync")
        .arg("--locked")
        .arg("--no-dev")
        .arg("--project")
        .arg(layout.src.join("services").join(name))
        .arg("--python")
        .arg(&layout.core)
        .env("UV_PROJECT_ENVIRONMENT", layout.packs.join(name))
        .env("UV_CACHE_DIR", layout.packs.join(".uv-cache"))
        .env("UV_PYTHON_DOWNLOADS", "never")
        .env("UV_LINK_MODE", "copy")
        .env("NO_COLOR", "1")
        .env_remove("VIRTUAL_ENV")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    command
}

impl Packs {
    pub fn state(&self, layout: &Layout, name: &str) -> PackState {
        let mut state = self
            .jobs
            .lock()
            .unwrap()
            .get(name)
            .map(|s| s.lock().unwrap().clone())
            .unwrap_or_default();
        state.installed = layout.pack_python(name, false).is_some();
        state
    }

    /// Start installing ``name`` in the background; ``done`` runs after a success.
    pub fn install(
        &self,
        layout: &Layout,
        name: &str,
        done: impl FnOnce() + Send + 'static,
    ) -> Result<(), String> {
        if !crate::layout::PACKS.contains(&name) {
            return Err(format!("no pack named {name}"));
        }
        let uv = layout.uv.clone().ok_or(
            "this build has no bundled uv; in a checkout, run uv sync --project services/<name>",
        )?;
        let job = {
            let mut jobs = self.jobs.lock().unwrap();
            let job = jobs.entry(name.to_string()).or_default().clone();
            let mut s = job.lock().unwrap();
            if s.installing {
                return Err(format!("{name} is already installing"));
            }
            *s = PackState {
                installing: true,
                ..PackState::default()
            };
            drop(s);
            job
        };
        let log_path = layout.packs.join(format!("{name}.log"));
        let mut command = sync_command(layout, &uv, name);
        let name = name.to_string();
        std::thread::spawn(move || {
            let result = run(&mut command, &job, &log_path);
            let mut s = job.lock().unwrap();
            s.installing = false;
            match result {
                Ok(()) => {
                    drop(s);
                    done();
                }
                Err(error) => s.failed = Some(format!("{name}: {error}")),
            }
        });
        Ok(())
    }
}

fn run(command: &mut Command, job: &Mutex<PackState>, log_path: &PathBuf) -> Result<(), String> {
    if let Some(dir) = log_path.parent() {
        std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    }
    let mut tree = Tree::spawn(command).map_err(|e| format!("could not start uv: {e}"))?;
    // uv reports progress on stderr; both streams go to the page and to the log.
    let (lines_tx, lines_rx) = std::sync::mpsc::channel::<String>();
    let mut readers = Vec::new();
    for stream in [tree_stdout(&mut tree), tree_stderr(&mut tree)]
        .into_iter()
        .flatten()
    {
        let tx = lines_tx.clone();
        readers.push(std::thread::spawn(move || {
            for line in BufReader::new(stream).lines().map_while(Result::ok) {
                let _ = tx.send(line);
            }
        }));
    }
    drop(lines_tx);
    let mut log = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(log_path)
        .map_err(|e| e.to_string())?;
    for line in lines_rx {
        use std::io::Write;
        let _ = writeln!(log, "{line}");
        let mut s = job.lock().unwrap();
        s.lines.push(line);
        let excess = s.lines.len().saturating_sub(200);
        s.lines.drain(..excess);
    }
    for reader in readers {
        let _ = reader.join();
    }
    loop {
        match tree.try_wait() {
            Ok(Some(status)) if status.success() => return Ok(()),
            Ok(Some(status)) => {
                return Err(format!("uv failed ({status}); see {}", log_path.display()))
            }
            Ok(None) => std::thread::sleep(std::time::Duration::from_millis(100)),
            Err(e) => return Err(e.to_string()),
        }
    }
}

fn tree_stdout(tree: &mut Tree) -> Option<Box<dyn std::io::Read + Send>> {
    tree.take_stdout()
        .map(|s| Box::new(s) as Box<dyn std::io::Read + Send>)
}

fn tree_stderr(tree: &mut Tree) -> Option<Box<dyn std::io::Read + Send>> {
    tree.take_stderr()
        .map(|s| Box::new(s) as Box<dyn std::io::Read + Send>)
}

/// What a pack command looks like (tests).
#[cfg(test)]
pub fn describe(command: &Command) -> Vec<std::ffi::OsString> {
    std::iter::once(command.get_program().to_os_string())
        .chain(command.get_args().map(|a| a.to_os_string()))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::ffi::OsString;

    #[test]
    fn sync_command_is_locked_and_kept_out_of_the_install_folder() {
        let layout = Layout::installed(Path::new("/app/res"), PathBuf::from("/data/packs"));
        let command = sync_command(&layout, Path::new("/app/res/runtime/uv"), "speech");
        let args: Vec<String> = describe(&command)
            .iter()
            .map(|a| a.to_string_lossy().into_owned())
            .collect();
        assert_eq!(&args[1..4], ["sync", "--locked", "--no-dev"]);
        assert!(args.contains(&"/app/res/src/services/speech".to_string()));
        let envs: HashMap<_, _> = command
            .get_envs()
            .filter_map(|(k, v)| Some((k.to_os_string(), v?.to_os_string())))
            .collect();
        assert_eq!(
            envs[&OsString::from("UV_PROJECT_ENVIRONMENT")],
            OsString::from("/data/packs/speech")
        );
        assert_eq!(
            envs[&OsString::from("UV_PYTHON_DOWNLOADS")],
            OsString::from("never")
        );
    }

    #[test]
    fn unknown_pack_and_missing_uv_are_refused() {
        let layout = Layout::installed(
            Path::new("/nonexistent"),
            PathBuf::from("/nonexistent/packs"),
        );
        let packs = Packs::default();
        assert!(packs
            .install(&layout, "bitcoin", || {})
            .unwrap_err()
            .contains("no pack"));
        assert!(packs
            .install(&layout, "speech", || {})
            .unwrap_err()
            .contains("uv"));
        assert!(!packs.state(&layout, "speech").installed);
    }

    #[cfg(unix)]
    #[test]
    fn install_streams_output_and_reports_failure() {
        let dir = std::env::temp_dir().join(format!("daycube-pack-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        // A stand-in uv that prints to both streams and fails.
        let uv = dir.join("uv");
        std::fs::write(
            &uv,
            "#!/bin/sh\necho resolving\necho 'error: no network' >&2\nexit 2\n",
        )
        .unwrap();
        std::os::unix::fs::PermissionsExt::set_mode(
            &mut std::fs::metadata(&uv).unwrap().permissions(),
            0o755,
        );
        std::fs::set_permissions(&uv, std::os::unix::fs::PermissionsExt::from_mode(0o755)).unwrap();
        let mut layout = Layout::installed(&dir, dir.join("packs"));
        layout.uv = Some(uv);
        let packs = Packs::default();
        packs
            .install(&layout, "speech", || panic!("must not run after a failure"))
            .unwrap();
        let start = std::time::Instant::now();
        while packs.state(&layout, "speech").installing {
            assert!(start.elapsed() < std::time::Duration::from_secs(10));
            std::thread::sleep(std::time::Duration::from_millis(50));
        }
        let state = packs.state(&layout, "speech");
        assert!(state.failed.unwrap().contains("uv failed"));
        assert!(
            state.lines.contains(&"resolving".to_string())
                && state.lines.contains(&"error: no network".to_string())
        );
        assert!(std::fs::read_to_string(dir.join("packs/speech.log"))
            .unwrap()
            .contains("no network"));
    }
}
