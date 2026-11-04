//! The app's own settings (``%APPDATA%\app.daycube.desktop\settings.json``): which vault, whether the
//! app runs capture and starts at sign-in, and the port it last used.

use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

fn yes() -> bool {
    true
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct Config {
    #[serde(default)]
    pub vault: Option<PathBuf>,
    /// Run the capture receiver and eye tracker from the app.
    #[serde(default = "yes")]
    pub capture: bool,
    /// Start (to the tray) at every Windows sign-in.
    #[serde(default = "yes")]
    pub autostart: bool,
    #[serde(default)]
    pub port: Option<u16>,
}

impl Default for Config {
    fn default() -> Self {
        Self { vault: None, capture: true, autostart: true, port: None }
    }
}

impl Config {
    pub fn load(path: &Path) -> Self {
        std::fs::read(path).ok().and_then(|raw| serde_json::from_slice(&raw).ok()).unwrap_or_default()
    }

    pub fn save(&self, path: &Path) -> std::io::Result<()> {
        if let Some(dir) = path.parent() {
            std::fs::create_dir_all(dir)?;
        }
        let tmp = path.with_extension("json.tmp");
        std::fs::write(&tmp, serde_json::to_vec_pretty(self)?)?;
        std::fs::rename(tmp, path)
    }
}

/// A vault this PC already has, for the first run: the first candidate holding a token.
pub fn existing_vault(candidates: &[PathBuf]) -> Option<PathBuf> {
    candidates.iter().find(|v| v.join(".token").is_file()).cloned()
}

/// Where a vault is likely to be: the repository's usual place, then the user's folders.
pub fn vault_candidates() -> Vec<PathBuf> {
    let mut out = vec![
        PathBuf::from(r"E:\DAYCUBE\vault"),
        PathBuf::from(r"D:\DAYCUBE\vault"),
        PathBuf::from(r"C:\DAYCUBE\vault"),
    ];
    if let Some(home) = std::env::var_os("USERPROFILE").or_else(|| std::env::var_os("HOME")).map(PathBuf::from) {
        out.push(home.join("DAYCUBE").join("vault"));
        out.push(home.join("Documents").join("DayCube").join("vault"));
    }
    out
}

/// The folder a new vault goes in when none exists yet.
pub fn default_new_vault() -> PathBuf {
    std::env::var_os("USERPROFILE")
        .or_else(|| std::env::var_os("HOME"))
        .map(PathBuf::from)
        .unwrap_or_default()
        .join("Documents")
        .join("DayCube")
        .join("vault")
}

/// A folder accepted as a vault: an existing vault, an empty folder, or one that does not exist
/// yet. A folder full of other things is refused, so a misclick cannot scatter a vault into it.
pub fn usable_vault(path: &Path) -> Result<(), String> {
    if !path.is_absolute() {
        return Err("choose a full folder path".into());
    }
    if path.join(".token").is_file() || !path.exists() {
        return Ok(());
    }
    if !path.is_dir() {
        return Err(format!("{} is a file, not a folder", path.display()));
    }
    match std::fs::read_dir(path).map(|mut entries| entries.next().is_none()) {
        Ok(true) => Ok(()),
        Ok(false) => Err(format!("{} already holds other files and is not a DayCube vault", path.display())),
        Err(error) => Err(format!("{} cannot be read: {error}", path.display())),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("daycube-config-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn round_trip_and_defaults() {
        let dir = temp("rt");
        let path = dir.join("settings.json");
        assert_eq!(Config::load(&path), Config::default());
        let config = Config { vault: Some(dir.join("vault")), capture: false, autostart: true, port: Some(7701) };
        config.save(&path).unwrap();
        assert_eq!(Config::load(&path), config);
        std::fs::write(&path, b"{\"vault\": null}").unwrap();
        let partial = Config::load(&path);
        assert!(partial.capture && partial.autostart);
        std::fs::write(&path, b"not json").unwrap();
        assert_eq!(Config::load(&path), Config::default());
    }

    #[test]
    fn vault_choices() {
        let dir = temp("vault");
        let vault = dir.join("vault");
        std::fs::create_dir_all(&vault).unwrap();
        assert!(usable_vault(&vault).is_ok(), "empty folder");
        assert!(usable_vault(&dir.join("new")).is_ok(), "folder to create");
        std::fs::write(vault.join("notes.txt"), b"x").unwrap();
        assert!(usable_vault(&vault).is_err(), "folder with other files");
        std::fs::write(vault.join(".token"), b"t").unwrap();
        assert!(usable_vault(&vault).is_ok(), "existing vault");
        assert!(usable_vault(Path::new("relative")).is_err());
        assert_eq!(existing_vault(&[dir.join("none"), vault.clone()]), Some(vault));
    }
}
