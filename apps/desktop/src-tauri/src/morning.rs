//! Yesterday's review as a Windows notification, once each morning from the push time (the API's
//! ``GET /morning``, ``DAYCUBE_PUSH_AT``, 08:00); and Alt+Shift+F, which brings DayCube to the
//! front with its search open from any app.

use std::time::Duration;
use tauri::{AppHandle, Manager};
use tauri_plugin_notification::NotificationExt;

use crate::{http, Shell};

pub const SEARCH: &str = "Alt+Shift+F";
const EVERY: Duration = Duration::from_secs(15 * 60);

/// The review in ``/morning``'s answer as (date, title, text), unless there is none or it was shown.
pub fn unseen(body: &str, notified: Option<&str>) -> Option<(String, String, String)> {
    let reply: serde_json::Value = serde_json::from_str(body).ok()?;
    let date = reply["date"].as_str()?;
    if notified == Some(date) {
        return None;
    }
    Some((date.into(), reply["title"].as_str()?.into(), reply["text"].as_str().unwrap_or_default().into()))
}

pub fn watch(app: AppHandle) {
    std::thread::spawn(move || {
        std::thread::sleep(Duration::from_secs(60)); // after the API's warm-up
        loop {
            check(&app);
            std::thread::sleep(EVERY);
        }
    });
}

fn check(app: &AppHandle) {
    let shell = app.state::<Shell>();
    let (on, port, notified) = {
        let config = shell.config.lock().unwrap();
        (config.notify, config.port, config.notified.clone())
    };
    let (true, Some(port)) = (on, port) else { return };
    let Ok(reply) = http::get(port, "/api/morning", Duration::from_secs(5)) else { return };
    let Some((date, title, text)) = unseen(&reply.body, notified.as_deref()) else { return };
    match app.notification().builder().title(&title).body(&text).show() {
        Ok(()) => {
            let mut config = shell.config.lock().unwrap();
            config.notified = Some(date);
            let _ = config.save(&shell.config_path);
        }
        Err(error) => shell.note(&format!("morning notification failed: {error}")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_review_is_shown_once() {
        let body = r#"{"date": "2026-11-07", "title": "DayCube · 2026-11-07", "text": "2 h at the desk"}"#;
        assert_eq!(
            unseen(body, None),
            Some(("2026-11-07".into(), "DayCube · 2026-11-07".into(), "2 h at the desk".into()))
        );
        assert_eq!(unseen(body, Some("2026-11-07")), None);
        assert_eq!(unseen(r#"{"date": null}"#, None), None);
        assert_eq!(unseen("not json", None), None);
    }
}
