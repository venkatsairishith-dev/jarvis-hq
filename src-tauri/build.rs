fn main() {
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&[
            "greet",
            "set_click_through",
            "open_new_window",
            "get_screen_capture_capabilities",
            "get_screen_capture_sources",
            "capture_screen_source",
        ]),
    ))
    .expect("failed to run Tauri build script");
}
