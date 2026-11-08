//! The tray icon: what capture is doing, pause and resume, the windows, start at sign-in, quit.

use std::time::Duration;
use tauri::menu::{CheckMenuItem, Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Manager, Wry};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons};

use crate::{http, Shell, RECEIVER_PORT};

pub struct Items {
    status: MenuItem<Wry>,
    pause: MenuItem<Wry>,
    autostart: CheckMenuItem<Wry>,
}

/// What the receiver's /status says, in a few words, and whether recording is paused.
pub fn describe(status: Option<&serde_json::Value>, capture: bool) -> (String, Option<bool>) {
    if !capture {
        return ("Capture is off".into(), None);
    }
    let Some(status) = status else {
        return ("Capture is starting…".into(), None);
    };
    let paused = status["paused"].as_bool().unwrap_or(false);
    if paused {
        return ("Recording paused".into(), Some(true));
    }
    let gaze = &status["gaze"];
    let eyes = if gaze["tracking"].as_bool() == Some(true) {
        "eye tracker on".to_string()
    } else {
        match gaze["reason"].as_str() {
            Some(reason) if !reason.is_empty() => format!("eye tracker off: {reason}"),
            _ => "eye tracker off".into(),
        }
    };
    (format!("Recording · {eyes}"), Some(false))
}

pub fn build(app: &AppHandle) -> tauri::Result<()> {
    let status = MenuItem::with_id(app, "status", "Starting…", false, None::<&str>)?;
    let open = MenuItem::with_id(app, "open", "Open DayCube", true, None::<&str>)?;
    let pause = MenuItem::with_id(app, "pause", "Pause recording", false, None::<&str>)?;
    let forget = MenuItem::with_id(app, "forget", "Delete the last hour…", true, None::<&str>)?;
    let calibrate = MenuItem::with_id(app, "calibrate", "Calibrate the eye tracker", true, None::<&str>)?;
    let tools = MenuItem::with_id(app, "tools", "Doctor and GPU packs…", true, None::<&str>)?;
    let autostart = {
        let on = app.state::<Shell>().config.lock().unwrap().autostart;
        CheckMenuItem::with_id(app, "autostart", "Start at sign-in", true, on, None::<&str>)?
    };
    let quit = MenuItem::with_id(app, "quit", "Quit DayCube", true, None::<&str>)?;
    let menu = Menu::with_items(
        app,
        &[
            &status,
            &PredefinedMenuItem::separator(app)?,
            &open,
            &pause,
            &forget,
            &PredefinedMenuItem::separator(app)?,
            &calibrate,
            &tools,
            &autostart,
            &PredefinedMenuItem::separator(app)?,
            &quit,
        ],
    )?;
    app.manage(Items { status, pause, autostart });
    TrayIconBuilder::with_id("daycube")
        .icon(app.default_window_icon().cloned().expect("bundle icon"))
        .tooltip("DayCube")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| on_menu(app, event.id().as_ref()))
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } = event {
                crate::show_main(tray.app_handle());
            }
        })
        .build(app)?;
    let app = app.clone();
    std::thread::spawn(move || loop {
        refresh(&app);
        std::thread::sleep(Duration::from_secs(4));
    });
    Ok(())
}

/// Read the receiver's status and update the menu.
fn refresh(app: &AppHandle) {
    let shell = app.state::<Shell>();
    let capture = shell.capture_wanted() && shell.receiver.lock().unwrap().is_some();
    let status = if capture {
        http::get(RECEIVER_PORT, "/status", Duration::from_secs(2))
            .ok()
            .and_then(|r| serde_json::from_str(&r.body).ok())
    } else {
        None
    };
    let (text, paused) = describe(status.as_ref(), capture);
    let autostart = shell.config.lock().unwrap().autostart;
    let items = app.state::<Items>();
    let _ = items.status.set_text(&text);
    let _ = items.pause.set_text(if paused == Some(true) { "Resume recording" } else { "Pause recording" });
    let _ = items.pause.set_enabled(paused.is_some());
    let _ = items.autostart.set_checked(autostart);
    if let Some(tray) = app.tray_by_id("daycube") {
        let _ = tray.set_tooltip(Some(format!("DayCube · {text}")));
    }
}

fn on_menu(app: &AppHandle, id: &str) {
    match id {
        "open" => crate::show_main(app),
        "tools" => crate::show_tools(app),
        "calibrate" => {
            if let Err(error) = crate::calibrate(app.state::<Shell>()) {
                app.dialog().message(error).title("DayCube").show(|_| {});
            }
        }
        "pause" => {
            let app = app.clone();
            std::thread::spawn(move || {
                let shell = app.state::<Shell>();
                let token = shell.token.lock().unwrap().clone();
                let status = http::get(RECEIVER_PORT, "/status", Duration::from_secs(2)).ok();
                let paused = status
                    .and_then(|r| serde_json::from_str::<serde_json::Value>(&r.body).ok())
                    .and_then(|s| s["paused"].as_bool());
                let action = if paused == Some(true) { "/resume" } else { "/pause" };
                let _ = http::request("POST", RECEIVER_PORT, action, token.as_deref(), Duration::from_secs(3));
                refresh(&app);
            });
        }
        "forget" => {
            let app = app.clone();
            std::thread::spawn(move || {
                let sure = app
                    .dialog()
                    .message("Delete everything recorded in the last hour? Pages, gaze, screen text, places and recordings from it go for good.")
                    .title("DayCube")
                    .buttons(MessageDialogButtons::OkCancelCustom("Delete".into(), "Keep".into()))
                    .blocking_show();
                if sure {
                    let text = forget_last_hour(&app.state::<Shell>());
                    app.dialog().message(text).title("DayCube").show(|_| {});
                }
            });
        }
        "autostart" => {
            let on = !app.state::<Shell>().config.lock().unwrap().autostart;
            if let Err(error) = crate::apply_autostart(app, on) {
                app.dialog()
                    .message(format!("Could not change the sign-in entry: {error}"))
                    .title("DayCube")
                    .show(|_| {});
            }
        }
        "quit" => {
            let app = app.clone();
            std::thread::spawn(move || {
                let quit = app
                    .dialog()
                    .message("Quit DayCube? Recording stops until you open DayCube again or sign in to Windows.")
                    .title("DayCube")
                    .buttons(MessageDialogButtons::OkCancelCustom("Quit".into(), "Keep running".into()))
                    .blocking_show();
                if quit {
                    app.state::<Shell>().shutdown();
                    app.exit(0);
                }
            });
        }
        _ => {}
    }
}

/// ``DELETE /api/recent``: the API deletes the last hour and rebuilds what was made from it.
fn forget_last_hour(shell: &Shell) -> String {
    let token = shell.token.lock().unwrap().clone();
    let Some(port) = shell.config.lock().unwrap().port else {
        return "The vault API is not running.".into();
    };
    match http::request("DELETE", port, "/api/recent?minutes=60", token.as_deref(), Duration::from_secs(120)) {
        Ok(reply) if reply.status == 200 => deleted_in_words(&reply.body),
        Ok(reply) => format!("Nothing was deleted: the API answered {}.\n{}", reply.status, reply.body),
        Err(error) => format!("Nothing was deleted: {error}"),
    }
}

/// What ``/recent`` removed, in a sentence: "Deleted 412 records: capture 380, events 2, …".
pub fn deleted_in_words(body: &str) -> String {
    let mut counts: Vec<(String, u64)> = Vec::new();
    let reply: serde_json::Value = serde_json::from_str(body).unwrap_or_default();
    for day in reply["deleted"].as_array().into_iter().flatten() {
        for (kind, n) in day["removed"].as_object().into_iter().flatten() {
            let n = n.as_u64().unwrap_or(0);
            match counts.iter_mut().find(|(k, _)| k == kind) {
                Some((_, total)) => *total += n,
                None => counts.push((kind.clone(), n)),
            }
        }
    }
    counts.retain(|(_, n)| *n > 0);
    if counts.is_empty() {
        return "Nothing was recorded in the last hour.".into();
    }
    let total: u64 = counts.iter().map(|(_, n)| n).sum();
    let parts: Vec<String> = counts.iter().map(|(k, n)| format!("{k} {n}")).collect();
    format!("Deleted the last hour: {total} records ({}).", parts.join(", "))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn deletion_in_words() {
        let body =
            r#"{"deleted": [{"removed": {"capture": 380, "events": 2}}, {"removed": {"capture": 20, "places": 0}}]}"#;
        assert_eq!(deleted_in_words(body), "Deleted the last hour: 402 records (capture 400, events 2).");
        assert_eq!(deleted_in_words(r#"{"deleted": [{"removed": {}}]}"#), "Nothing was recorded in the last hour.");
    }

    #[test]
    fn status_in_words() {
        assert_eq!(describe(None, false).0, "Capture is off");
        assert_eq!(describe(None, true), ("Capture is starting…".into(), None));
        assert_eq!(describe(Some(&json!({"paused": true})), true), ("Recording paused".into(), Some(true)));
        assert_eq!(
            describe(Some(&json!({"paused": false, "gaze": {"tracking": true}})), true).0,
            "Recording · eye tracker on"
        );
        assert_eq!(
            describe(Some(&json!({"paused": false, "gaze": {"tracking": false, "reason": "not calibrated"}})), true).0,
            "Recording · eye tracker off: not calibrated"
        );
    }
}
