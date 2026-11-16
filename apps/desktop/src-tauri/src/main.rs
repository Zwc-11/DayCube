// No console window behind DayCube in release builds.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    daycube_desktop::run()
}
