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

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

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
