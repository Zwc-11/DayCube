//! Where the app's parts are: an installed app's resources, or a repository checkout when
//! ``DAYCUBE_DEV_ROOT`` names one (development builds use the checkout's own environments).

use std::ffi::OsString;
use std::path::{Path, PathBuf};

#[derive(Clone, Debug)]
pub struct Layout {
    /// The folder the Python code calls PROJECT_ROOT (``services/...`` lives under it).
    pub src: PathBuf,
    /// The core environment's console interpreter: API, receiver, doctor (started without a window).
    pub core: PathBuf,
    /// The eye tracker's interpreter, windowless.
    pub gaze: Option<PathBuf>,
    pub viewer: PathBuf,
    pub extension: PathBuf,
    pub uv: Option<PathBuf>,
    /// Where GPU packs (speech, recon) are installed: outside the install folder, so updates keep them.
    pub packs: PathBuf,
    pub checkout: bool,
}

pub const PACKS: [&str; 2] = ["speech", "recon"];

fn exe(name: &str) -> String {
    if cfg!(windows) {
        format!("{name}.exe")
    } else {
        name.to_string()
    }
}

/// A virtual environment's interpreter: ``Scripts\<name>.exe`` on Windows, ``bin/python`` elsewhere.
pub fn venv_python(venv: &Path, windowless: bool) -> Option<PathBuf> {
    let names: &[&str] = if windowless { &["pythonw", "python"] } else { &["python"] };
    names
        .iter()
        .map(|n| venv.join("Scripts").join(format!("{n}.exe")))
        .chain([venv.join("bin").join("python")])
        .find(|p| p.is_file())
}

impl Layout {
    pub fn installed(resources: &Path, packs: PathBuf) -> Self {
        let runtime = resources.join("runtime");
        let python = |env: &str, name: &str| {
            let dir = runtime.join(env);
            [dir.join(exe(name)), dir.join("bin").join("python3")].into_iter().find(|p| p.is_file())
        };
        Self {
            src: resources.join("src"),
            core: python("core", "python").unwrap_or_else(|| runtime.join("core").join(exe("python"))),
            gaze: python("gaze", "pythonw").or_else(|| python("gaze", "python")),
            viewer: resources.join("viewer"),
            extension: resources.join("extension"),
            uv: Some(runtime.join(exe("uv"))).filter(|p| p.is_file()),
            packs,
            checkout: false,
        }
    }

    pub fn checkout(root: &Path, packs: PathBuf) -> Self {
        let services = root.join("services");
        Self {
            src: root.to_path_buf(),
            core: venv_python(&services.join("capture-win").join(".venv"), false)
                .unwrap_or_else(|| services.join("capture-win").join(".venv").join("bin").join("python")),
            gaze: venv_python(&services.join("gaze").join(".venv"), true),
            viewer: root.join("apps").join("viewer").join("dist"),
            extension: root.join("extensions").join("chrome"),
            uv: None,
            packs,
            checkout: true,
        }
    }

    /// An installed GPU pack's interpreter (None in a checkout: the receiver finds its own .venv).
    pub fn pack_python(&self, name: &str, windowless: bool) -> Option<PathBuf> {
        if self.checkout {
            return None;
        }
        venv_python(&self.packs.join(name), windowless)
    }

    /// What is missing for the app to run at all, in words.
    pub fn problems(&self) -> Vec<String> {
        let mut out = Vec::new();
        if !self.core.is_file() {
            out.push(format!("the Python runtime is missing ({})", self.core.display()));
        }
        if !self.viewer.join("index.html").is_file() {
            out.push(format!("the viewer is missing ({})", self.viewer.display()));
        }
        if !self.src.join("services").join("ingest").is_dir() {
            out.push(format!("the DayCube sources are missing ({})", self.src.display()));
        }
        out
    }

    /// The environment every Python child gets: the vault, the .env beside it, the other
    /// environments' interpreters, and isolation from a Python installed on the PC.
    pub fn python_env(&self, vault: &Path) -> Vec<(String, OsString)> {
        let mut env: Vec<(String, OsString)> = vec![
            ("DAYCUBE_VAULT_ROOT".into(), vault.into()),
            ("DAYCUBE_AUTO_RESTART".into(), "false".into()),
            ("DAYCUBE_DESKTOP".into(), "1".into()),
            ("PYTHONUTF8".into(), "1".into()),
            ("PYTHONIOENCODING".into(), "utf-8".into()),
            ("PYTHONUNBUFFERED".into(), "1".into()),
            ("PYTHONNOUSERSITE".into(), "1".into()),
        ];
        if let Some(parent) = vault.parent() {
            env.push(("DAYCUBE_ENV_FILE".into(), parent.join(".env").into()));
        }
        if !self.checkout {
            // Downloaded models (speech, embeddings) live beside the packs, not in the install
            // folder (an update replaces it) and not in the system temp folder (cleaned up).
            if let Some(data) = self.packs.parent() {
                env.push(("HF_HOME".into(), data.join("cache").join("huggingface").into()));
                env.push(("FASTEMBED_CACHE_PATH".into(), data.join("cache").join("fastembed").into()));
            }
            if let Some(gaze) = &self.gaze {
                env.push(("DAYCUBE_GAZE_PYTHON".into(), gaze.into()));
            }
            if let Some(speech) = self.pack_python("speech", true) {
                env.push(("DAYCUBE_SPEECH_PYTHON".into(), speech.into()));
            }
            if let Some(recon) = self.pack_python("recon", false) {
                env.push(("DAYCUBE_RECON_PYTHON".into(), recon.into()));
            }
        }
        env
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn touch(path: &Path) {
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, b"").unwrap();
    }

    #[test]
    fn installed_layout_and_env() {
        let root = std::env::temp_dir().join(format!("daycube-layout-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        let resources = root.join("res");
        touch(&resources.join("runtime/core").join(exe("python")));
        touch(&resources.join("runtime/gaze").join(exe("pythonw")));
        touch(&resources.join("viewer/index.html"));
        std::fs::create_dir_all(resources.join("src/services/ingest")).unwrap();
        let packs = root.join("packs");
        let speech = if cfg!(windows) {
            packs.join("speech").join("Scripts").join("pythonw.exe")
        } else {
            packs.join("speech").join("bin").join("python")
        };
        touch(&speech);

        let layout = Layout::installed(&resources, packs);
        assert!(layout.problems().is_empty(), "{:?}", layout.problems());
        assert!(layout.gaze.is_some());
        assert!(layout.pack_python("recon", false).is_none());

        let vault = root.join("DAYCUBE").join("vault");
        let env: std::collections::HashMap<_, _> = layout.python_env(&vault).into_iter().collect();
        assert_eq!(env["DAYCUBE_VAULT_ROOT"], OsString::from(&vault));
        assert_eq!(env["DAYCUBE_ENV_FILE"], OsString::from(root.join("DAYCUBE").join(".env")));
        assert_eq!(env["DAYCUBE_AUTO_RESTART"], OsString::from("false"));
        assert_eq!(env["DAYCUBE_SPEECH_PYTHON"], OsString::from(&speech));
        assert!(env.contains_key("DAYCUBE_GAZE_PYTHON"));
        assert!(!env.contains_key("DAYCUBE_RECON_PYTHON"));
        assert_eq!(env["HF_HOME"], OsString::from(root.join("cache").join("huggingface")));
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn missing_parts_are_named() {
        let layout = Layout::installed(Path::new("/nonexistent/daycube"), PathBuf::from("/nonexistent/packs"));
        let problems = layout.problems().join("; ");
        assert!(problems.contains("Python runtime"));
        assert!(problems.contains("viewer"));
        assert!(problems.contains("sources"));
    }

    #[test]
    fn checkout_leaves_environment_discovery_to_the_receiver() {
        let layout = Layout::checkout(Path::new("/repo"), PathBuf::from("/packs"));
        let env: std::collections::HashMap<_, _> = layout.python_env(Path::new("/repo/vault")).into_iter().collect();
        assert!(!env.contains_key("DAYCUBE_GAZE_PYTHON"));
        assert_eq!(layout.viewer, PathBuf::from("/repo/apps/viewer/dist"));
    }
}
