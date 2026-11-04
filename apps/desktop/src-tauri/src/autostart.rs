//! Start at sign-in, through the same per-user ``Run`` value that ``capture.cmd install-autostart``
//! writes (``DayCube capture``). One value means the app and a repository checkout replace each
//! other instead of both starting a receiver on 127.0.0.1:7700, and ``capture doctor``, which reads
//! this value and checks that its program exists, keeps working.

use std::path::Path;

#[cfg_attr(not(windows), allow(dead_code))]
pub const VALUE: &str = "DayCube capture";
pub const BACKGROUND: &str = "--background";

/// The command line the sign-in entry runs: the app, to the tray, no window.
pub fn command_line(exe: &Path) -> String {
    format!("\"{}\" {BACKGROUND}", exe.display())
}

/// Whether ``line`` (the current Run value) starts this app.
pub fn is_ours(line: &str, exe: &Path) -> bool {
    let program = match line.strip_prefix('"') {
        Some(rest) => rest.split('"').next().unwrap_or(""),
        None => line.split_whitespace().next().unwrap_or(""),
    };
    program.eq_ignore_ascii_case(&exe.display().to_string())
}

#[cfg(windows)]
mod registry {
    use winreg::enums::{HKEY_CURRENT_USER, KEY_QUERY_VALUE, KEY_SET_VALUE};
    use winreg::RegKey;

    const RUN: &str = r"Software\Microsoft\Windows\CurrentVersion\Run";

    pub fn current() -> Option<String> {
        RegKey::predef(HKEY_CURRENT_USER)
            .open_subkey_with_flags(RUN, KEY_QUERY_VALUE)
            .ok()?
            .get_value(super::VALUE)
            .ok()
    }

    pub fn set(line: &str) -> std::io::Result<()> {
        let (key, _) = RegKey::predef(HKEY_CURRENT_USER).create_subkey_with_flags(RUN, KEY_SET_VALUE)?;
        key.set_value(super::VALUE, &line)
    }

    pub fn remove() -> std::io::Result<()> {
        match RegKey::predef(HKEY_CURRENT_USER).open_subkey_with_flags(RUN, KEY_SET_VALUE) {
            Ok(key) => match key.delete_value(super::VALUE) {
                Err(e) if e.kind() != std::io::ErrorKind::NotFound => Err(e),
                _ => Ok(()),
            },
            Err(_) => Ok(()),
        }
    }
}

#[cfg(not(windows))]
mod registry {
    pub fn current() -> Option<String> {
        None
    }
    pub fn set(_: &str) -> std::io::Result<()> {
        Ok(())
    }
    pub fn remove() -> std::io::Result<()> {
        Ok(())
    }
}

/// Make the sign-in entry match ``wanted``. Returns what the entry was before, when it changed.
pub fn apply(wanted: bool, exe: &Path) -> std::io::Result<Option<String>> {
    let before = registry::current();
    let line = command_line(exe);
    if wanted {
        if before.as_deref() != Some(line.as_str()) {
            registry::set(&line)?;
            return Ok(Some(before.unwrap_or_default()));
        }
    } else if before.as_deref().is_some_and(|b| is_ours(b, exe)) {
        // Only our own entry: a checkout's receiver entry is the user's business once the app is off.
        registry::remove()?;
        return Ok(before);
    }
    Ok(None)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    #[test]
    fn line_and_ownership() {
        let exe = PathBuf::from(r"C:\Users\c\AppData\Local\Programs\DayCube\DayCube.exe");
        let line = command_line(&exe);
        assert_eq!(line, "\"C:\\Users\\c\\AppData\\Local\\Programs\\DayCube\\DayCube.exe\" --background");
        assert!(is_ours(&line, &exe));
        assert!(is_ours(&line.to_lowercase(), &exe));
        // The checkout's entry (python -m capture run) is not ours.
        assert!(!is_ours(
            r#""E:\DAYCUBE\services\capture-win\.venv\Scripts\pythonw.exe" -m capture run --receive --gaze"#,
            &exe
        ));
        assert!(!is_ours("", &exe));
    }
}

#[cfg(all(test, windows))]
mod windows_tests {
    use super::*;
    use std::path::PathBuf;

    #[test]
    fn apply_writes_replaces_and_removes_the_shared_entry() {
        let saved = registry::current();
        let exe = PathBuf::from(r"C:\DayCubeTest\DayCube.exe");
        // A checkout's entry is replaced by the app's: one receiver, never two.
        registry::set(r#""E:\DAYCUBE\services\capture-win\.venv\Scripts\pythonw.exe" -m capture run --receive --gaze"#)
            .unwrap();
        assert!(apply(true, &exe).unwrap().is_some());
        assert_eq!(registry::current().as_deref(), Some(command_line(&exe).as_str()));
        assert!(apply(true, &exe).unwrap().is_none(), "no change when already ours");
        assert!(apply(false, &exe).unwrap().is_some());
        assert_eq!(registry::current(), None);
        // Turning off never removes an entry that is not the app's.
        registry::set("other.exe").unwrap();
        assert!(apply(false, &exe).unwrap().is_none());
        assert_eq!(registry::current().as_deref(), Some("other.exe"));
        match saved {
            Some(line) => registry::set(&line).unwrap(),
            None => registry::remove().unwrap(),
        }
    }
}
