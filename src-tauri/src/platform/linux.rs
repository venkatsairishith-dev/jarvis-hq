use std::ffi::c_void;
use std::fs;
use std::path::PathBuf;
use std::process::Command;
use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU64, Ordering};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use gtk::glib::translate::ToGlibPtr;
use gtk::prelude::*;
use gtk_layer_shell::{Edge, KeyboardMode, Layer, LayerShell};
use tauri::Manager;
use webkit2gtk::{
    DeviceInfoPermissionRequest, PermissionRequestExt, SettingsExt, UserMediaPermissionRequest,
    WebViewExt,
};

use super::CursorSample;

pub(crate) const INJECT_CLICK_THROUGH_BRIDGE: bool = false;

static INPUT_REGION_GENERATION: AtomicU64 = AtomicU64::new(0);
static NIRI_FINALIZATION_SCHEDULED: AtomicBool = AtomicBool::new(false);
static NIRI_LAYER_SHELL_ACTIVE: AtomicBool = AtomicBool::new(false);
static NIRI_INPUT_REGION_READY: AtomicBool = AtomicBool::new(false);
static NIRI_PENDING_INPUT_REGION: Mutex<Option<PendingInputRegion>> = Mutex::new(None);
static NIRI_DIAGNOSTIC_LAST_SURFACE_ID: AtomicU32 = AtomicU32::new(0);

const STATIC_INPUT_REGION_ENV: &str = "NEKO_X11_STATIC_INPUT_REGION";
const INPUT_REGION_DIAGNOSTIC_ENV: &str = "NEKO_TAURI_INPUT_REGION_DIAGNOSTIC";
const STATIC_INPUT_REGION_DEFAULT: InputRectangle = InputRectangle {
    x: 24,
    y: 24,
    width: 320,
    height: 220,
};
const INPUT_SENTINEL: InputRectangle = InputRectangle {
    x: 0,
    y: 0,
    width: 1,
    height: 1,
};
const POINTER_HOT_ZONE_SIZE: i32 = 192;
const DRAG_HOT_ZONE_SIZE: i32 = 768;
const BACKEND_MEDIA_PORT: u16 = 48911;
const NIRI_CONFIG_PATCH_DISABLE_ENV: &str = "NEKO_DISABLE_NIRI_CONFIG_PATCH";
const NIRI_CONFIG_PATCH_BEGIN: &str = "// BEGIN N.E.K.O Tauri niri window rules";
const NIRI_CONFIG_PATCH_END: &str = "// END N.E.K.O Tauri niri window rules";
const NIRI_WINDOW_TITLE: &str = "N.E.K.O";
const NIRI_APP_ID: &str = "Neko-tauri";
const NIRI_FLOAT_RETRY_DELAYS_MS: [u64; 6] = [0, 80, 300, 900, 1800, 3200];
const NIRI_SIZE_STABILITY_POLL_MS: u64 = 25;
const NIRI_SIZE_STABILITY_REQUIRED_SAMPLES: usize = 3;
const NIRI_SIZE_STABILITY_MAX_POLLS: usize = 80;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct InputRectangle {
    x: i32,
    y: i32,
    width: i32,
    height: i32,
}

#[derive(Debug)]
struct PendingInputRegion {
    generation: u64,
    ignore: bool,
    reason: String,
    rectangles: Vec<InputRectangle>,
}

#[link(name = "Xext")]
extern "C" {
    fn XShapeGetRectangles(
        display: *mut x11::xlib::Display,
        window: x11::xlib::Window,
        kind: i32,
        count: *mut i32,
        ordering: *mut i32,
    ) -> *mut x11::xlib::XRectangle;
}

#[repr(C)]
struct WlProxy {
    _private: [u8; 0],
}

#[repr(C)]
struct WlInterface {
    _private: [u8; 0],
}

const WL_COMPOSITOR_CREATE_REGION: u32 = 1;
const WL_SURFACE_SET_INPUT_REGION: u32 = 5;
const WL_SURFACE_COMMIT: u32 = 6;
const WL_REGION_DESTROY: u32 = 0;
const WL_REGION_ADD: u32 = 1;
const WL_MARSHAL_FLAG_DESTROY: u32 = 1;

#[link(name = "gdk-3")]
extern "C" {
    fn gdk_wayland_window_get_wl_surface(window: *mut c_void) -> *mut WlProxy;
    fn gdk_wayland_display_get_wl_compositor(display: *mut c_void) -> *mut WlProxy;
}

#[link(name = "wayland-client")]
extern "C" {
    static wl_region_interface: WlInterface;

    fn wl_proxy_get_version(proxy: *mut WlProxy) -> u32;
    fn wl_proxy_get_id(proxy: *mut WlProxy) -> u32;
    fn wl_proxy_marshal_flags(
        proxy: *mut WlProxy,
        opcode: u32,
        interface: *const WlInterface,
        version: u32,
        flags: u32,
        ...
    ) -> *mut WlProxy;
}

fn input_region_diagnostics_enabled() -> bool {
    std::env::var_os(INPUT_REGION_DIAGNOSTIC_ENV).is_some()
}

fn wayland_surface_id(gdk_window: &gdk::Window) -> Option<u32> {
    unsafe {
        let gdk_window_ptr: *mut gdk::ffi::GdkWindow = gdk_window.to_glib_none().0;
        let surface = gdk_wayland_window_get_wl_surface(gdk_window_ptr.cast());
        (!surface.is_null()).then(|| wl_proxy_get_id(surface))
    }
}

fn describe_wayland_gdk_surface_tree(root: &gdk::Window) -> Vec<String> {
    fn visit(window: &gdk::Window, depth: usize, descriptions: &mut Vec<String>) {
        if depth > 4 || descriptions.len() >= 32 {
            return;
        }

        let (x, y, width, height) = window.geometry();
        descriptions.push(format!(
            "depth={depth} surface_id={:?} type={:?} geometry=({x},{y},{width},{height}) visible={} viewable={}",
            wayland_surface_id(window),
            window.window_type(),
            window.is_visible(),
            window.is_viewable()
        ));

        for child in window.children() {
            visit(&child, depth + 1, descriptions);
        }
    }

    let mut descriptions = Vec::new();
    visit(root, 0, &mut descriptions);
    descriptions
}

pub(crate) fn prepare_process_environment() {
    if is_niri_desktop() {
        // niri is a native Wayland compositor. Keeping the carrier on X11
        // inserts xwayland-satellite between GTK's XShape region and niri's
        // wl_surface input region; fractional scaling can then desynchronize
        // the two. Use GTK's native Wayland input region on niri only.
        std::env::set_var("GDK_BACKEND", "wayland");
        match ensure_niri_window_rule_installed() {
            Ok(Some(backup_path)) => println!(
                "[NiriPolicy] 已安装透明浮动窗口规则，原配置备份于 {}",
                backup_path.display()
            ),
            Ok(None) => println!("[NiriPolicy] 透明浮动窗口规则已存在"),
            Err(error) => println!("[NiriPolicy] 规则安装失败，将依靠运行时 IPC 兜底: {error}"),
        }
    } else {
        // GNOME/KDE keep the proven X11 ShapeInput path.
        std::env::set_var("GDK_BACKEND", "x11");
    }
}

pub(crate) fn configure_app(_app: &mut tauri::App) {}

fn is_kde_desktop_value(value: &str) -> bool {
    value
        .split([':', ';', ','])
        .map(str::trim)
        .filter(|token| !token.is_empty())
        .map(str::to_ascii_lowercase)
        .any(|token| {
            token == "kde"
                || token.starts_with("kde-")
                || token.contains("plasma")
                || token.ends_with("/kde")
        })
}

fn is_kde_desktop() -> bool {
    [
        "XDG_CURRENT_DESKTOP",
        "XDG_SESSION_DESKTOP",
        "DESKTOP_SESSION",
    ]
    .iter()
    .filter_map(|name| std::env::var(name).ok())
    .any(|value| is_kde_desktop_value(&value))
}

fn is_niri_desktop_value(value: &str) -> bool {
    value
        .split([':', ';', ','])
        .map(str::trim)
        .filter(|token| !token.is_empty())
        .any(|token| token.eq_ignore_ascii_case("niri"))
}

fn is_niri_desktop() -> bool {
    std::env::var_os("NIRI_SOCKET").is_some()
        || [
            "XDG_CURRENT_DESKTOP",
            "XDG_SESSION_DESKTOP",
            "DESKTOP_SESSION",
            "GDMSESSION",
        ]
        .iter()
        .filter_map(|name| std::env::var(name).ok())
        .any(|value| is_niri_desktop_value(&value))
}

fn niri_window_rule_snippet() -> &'static str {
    r##"window-rule {
    match app-id=r#"(?i)^neko-tauri$"# title=r#"^N\.E\.K\.O$"#
    open-floating true
    open-fullscreen false
    draw-border-with-background false
    focus-ring {
        off
    }
    border {
        off
    }
}"##
}

fn niri_managed_config_block() -> String {
    format!(
        "{NIRI_CONFIG_PATCH_BEGIN}\n\
// Managed by N.E.K.O Tauri. The single transparent carrier must\n\
// open floating and must not inherit niri's solid focus-ring background.\n\
{}\n\
{NIRI_CONFIG_PATCH_END}",
        niri_window_rule_snippet()
    )
}

fn merge_niri_managed_config(original: &str) -> (String, bool) {
    let block = niri_managed_config_block();
    if let (Some(begin), Some(end)) = (
        original.find(NIRI_CONFIG_PATCH_BEGIN),
        original.find(NIRI_CONFIG_PATCH_END),
    ) {
        if end > begin {
            let after_end = end + NIRI_CONFIG_PATCH_END.len();
            let mut merged = String::new();
            merged.push_str(original[..begin].trim_end());
            if !merged.is_empty() {
                merged.push_str("\n\n");
            }
            merged.push_str(&block);
            let suffix = original[after_end..].trim_start();
            if !suffix.is_empty() {
                merged.push_str("\n\n");
                merged.push_str(suffix);
            } else {
                merged.push('\n');
            }
            let changed = merged != original;
            return (merged, changed);
        }
    }

    if original.contains(niri_window_rule_snippet()) {
        return (original.to_string(), false);
    }

    let mut merged = original.trim_end().to_string();
    if !merged.is_empty() {
        merged.push_str("\n\n");
    }
    merged.push_str(&block);
    merged.push('\n');
    (merged, true)
}

fn niri_config_path() -> Option<PathBuf> {
    if let Some(explicit) = std::env::var_os("NIRI_CONFIG").filter(|value| !value.is_empty()) {
        return Some(PathBuf::from(explicit));
    }

    let config_home = std::env::var_os("XDG_CONFIG_HOME")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .or_else(|| {
            std::env::var_os("HOME")
                .filter(|value| !value.is_empty())
                .map(|value| PathBuf::from(value).join(".config"))
        })?;
    Some(config_home.join("niri").join("config.kdl"))
}

fn run_niri_command(args: &[&str]) -> Result<String, String> {
    let output = Command::new("niri")
        .args(args)
        .output()
        .map_err(|error| format!("无法执行 niri {}: {error}", args.join(" ")))?;
    if !output.status.success() {
        return Err(format!(
            "niri {} 失败: {}",
            args.join(" "),
            String::from_utf8_lossy(&output.stderr).trim()
        ));
    }
    Ok(String::from_utf8_lossy(&output.stdout).into_owned())
}

fn ensure_niri_window_rule_installed() -> Result<Option<PathBuf>, String> {
    if std::env::var(NIRI_CONFIG_PATCH_DISABLE_ENV).as_deref() == Ok("1") {
        return Ok(None);
    }

    let config_path = niri_config_path().ok_or_else(|| "无法确定 niri 配置文件路径".to_string())?;
    if !config_path.exists() {
        return Err(format!("niri 配置文件不存在: {}", config_path.display()));
    }
    let write_path = fs::canonicalize(&config_path).unwrap_or_else(|_| config_path.clone());
    let original = fs::read_to_string(&write_path)
        .map_err(|error| format!("无法读取 {}: {error}", write_path.display()))?;
    let (merged, changed) = merge_niri_managed_config(&original);
    if !changed {
        return Ok(None);
    }

    let parent = write_path
        .parent()
        .ok_or_else(|| format!("配置路径没有父目录: {}", write_path.display()))?;
    let file_name = write_path
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or("config.kdl");
    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis();
    let temporary_path = parent.join(format!(
        ".{file_name}.neko-tauri-{}-{stamp}.tmp",
        std::process::id()
    ));
    let backup_path = parent.join(format!("{file_name}.neko-tauri-backup-{stamp}"));

    fs::write(&temporary_path, merged)
        .map_err(|error| format!("无法写入候选配置 {}: {error}", temporary_path.display()))?;
    if let Ok(metadata) = fs::metadata(&write_path) {
        let _ = fs::set_permissions(&temporary_path, metadata.permissions());
    }

    let temporary_arg = temporary_path.to_string_lossy().into_owned();
    if let Err(error) = run_niri_command(&["validate", "-c", temporary_arg.as_str()]) {
        let _ = fs::remove_file(&temporary_path);
        return Err(format!("候选 niri 配置校验失败，未修改用户配置: {error}"));
    }

    fs::copy(&write_path, &backup_path).map_err(|error| format!("无法备份 niri 配置: {error}"))?;
    if let Err(error) = fs::rename(&temporary_path, &write_path) {
        let _ = fs::remove_file(&temporary_path);
        return Err(format!("无法原子替换 niri 配置: {error}"));
    }

    let config_arg = config_path.to_string_lossy().into_owned();
    if let Err(error) = run_niri_command(&[
        "msg",
        "action",
        "load-config-file",
        "--path",
        config_arg.as_str(),
    ]) {
        let restore_result = fs::copy(&backup_path, &write_path);
        let _ = run_niri_command(&[
            "msg",
            "action",
            "load-config-file",
            "--path",
            config_arg.as_str(),
        ]);
        return Err(match restore_result {
            Ok(_) => format!("加载新规则失败，已恢复原配置: {error}"),
            Err(restore_error) => {
                format!("加载新规则失败且恢复配置失败: {error}; restore={restore_error}")
            }
        });
    }

    Ok(Some(backup_path))
}

fn is_trusted_media_origin(uri: &str) -> bool {
    let Ok(url) = tauri::Url::parse(uri) else {
        return false;
    };

    url.scheme() == "http"
        && matches!(
            url.host_str(),
            Some("127.0.0.1" | "localhost" | "::1" | "[::1]")
        )
        && url.port_or_known_default() == Some(BACKEND_MEDIA_PORT)
}

fn configure_media_permissions(window: &tauri::WebviewWindow) -> Result<(), String> {
    window
        .with_webview(|platform_webview| {
            let webview = platform_webview.inner();

            if let Some(settings) = WebViewExt::settings(&webview) {
                settings.set_enable_media_stream(true);
                settings.set_enable_webrtc(true);
            }

            webview.connect_permission_request(|webview, request| {
                let is_media_request = request
                    .dynamic_cast_ref::<UserMediaPermissionRequest>()
                    .is_some()
                    || request
                        .dynamic_cast_ref::<DeviceInfoPermissionRequest>()
                        .is_some();

                if !is_media_request {
                    // Leave unrelated permissions to WebKitGTK's default policy.
                    return false;
                }

                let uri = webview.uri().unwrap_or_default();
                let trusted = is_trusted_media_origin(uri.as_str());
                let origin = tauri::Url::parse(uri.as_str())
                    .ok()
                    .and_then(|url| {
                        let host = url.host_str()?.to_string();
                        Some(format!(
                            "{}://{}:{}",
                            url.scheme(),
                            host,
                            url.port_or_known_default().unwrap_or_default()
                        ))
                    })
                    .unwrap_or_else(|| "unknown".to_string());

                if trusted {
                    request.allow();
                    println!("[MediaPermission] 已允许本地 N.E.K.O 媒体请求: origin={origin}");
                } else {
                    request.deny();
                    println!("[MediaPermission] 已拒绝非可信媒体请求: origin={origin}");
                }

                true
            });
        })
        .map_err(|error| format!("无法配置 Linux WebView 媒体权限: {error}"))
}

pub(crate) fn configure_main_window(
    _app: &tauri::App,
    window: &tauri::WebviewWindow,
) -> Result<(), String> {
    configure_media_permissions(window)?;
    if is_niri_desktop() {
        window
            .set_title(NIRI_WINDOW_TITLE)
            .map_err(|error| format!("无法设置 niri 主窗口稳定标题: {error}"))?;
    }
    let niri_layer_shell = configure_niri_layer_shell(window)?;
    configure_overlay(window, niri_layer_shell)?;
    if is_kde_desktop() {
        // KWin constrains even oversized NORMAL windows to _NET_WORKAREA.
        // FULLSCREEN is the supported EWMH state that covers the complete
        // physical output instead of the panel-reserved work area.
        window
            .set_fullscreen(true)
            .map_err(|error| format!("无法将 KDE 主窗口设为全屏覆盖层: {error}"))?;
        println!("[WindowBounds] KDE 全屏覆盖状态已请求");
    } else if niri_layer_shell {
        println!("[WindowBounds] niri layer-shell 已锚定当前输出四边，由合成器分配覆盖尺寸");
    } else if is_niri_desktop() {
        // xwayland-satellite can desynchronize its Wayland surface geometry
        // and input region when an X11 window is realized at one size and
        // resized to an output-sized overlay before its first mapped frame.
        // Keep the configured normal size until on_page_load reveals the
        // window. The post-map finalizer performs exactly one output resize.
        println!("[WindowBounds] niri 最终尺寸延迟到窗口映射后设置");
    } else {
        resize_to_virtual_desktop(window)?;
    }
    if !niri_layer_shell {
        window
            .set_always_on_top(true)
            .map_err(|error| format!("无法将 Linux 主窗口置顶: {error}"))?;
    }

    Ok(())
}

fn configure_niri_layer_shell(window: &tauri::WebviewWindow) -> Result<bool, String> {
    if !is_niri_desktop() {
        return Ok(false);
    }

    let gtk_window = window.gtk_window().map_err(|error| error.to_string())?;
    if gtk_window.is_realized() {
        return Err(
            "niri layer-shell 必须在 GTK 主窗口首次 realize 之前初始化，但窗口已经 realize"
                .to_string(),
        );
    }
    if !gtk_layer_shell::is_supported() {
        println!(
            "[NiriLayerShell] 合成器未公开 zwlr_layer_shell_v1，保留 xdg_toplevel 浮动窗口兜底"
        );
        return Ok(false);
    }

    gtk_window.init_layer_shell();
    gtk_window.set_namespace("neko-tauri");
    gtk_window.set_layer(Layer::Overlay);
    gtk_window.set_keyboard_mode(KeyboardMode::OnDemand);
    gtk_window.set_exclusive_zone(0);
    for edge in [Edge::Left, Edge::Right, Edge::Top, Edge::Bottom] {
        gtk_window.set_anchor(edge, true);
    }
    NIRI_LAYER_SHELL_ACTIVE.store(true, Ordering::SeqCst);
    println!(
        "[NiriLayerShell] 主窗口已在首次 realize 前配置为 overlay layer surface: anchors=all exclusive_zone=0 keyboard=on-demand"
    );
    Ok(true)
}

fn configure_overlay(window: &tauri::WebviewWindow, niri_layer_shell: bool) -> Result<(), String> {
    let gtk_window = window.gtk_window().map_err(|error| error.to_string())?;

    if is_kde_desktop() {
        // KWin constrains DOCK windows to _NET_WORKAREA. On a desktop with
        // panels that turns a 2560x1600 overlay into (for example) 2560x1488,
        // producing a visible boundary and clipping avatars near the bottom.
        // NORMAL + keep_above preserves full physical-monitor bounds on KDE.
        gtk_window.set_type_hint(gdk::WindowTypeHint::Normal);
        println!("[WindowType] KDE/Plasma detected; using NORMAL full-screen overlay");
    } else if niri_layer_shell {
        // Layer-shell owns stacking and placement. Applying xdg-toplevel/EWMH
        // hints here would describe a different Wayland role and is at best a
        // no-op, at worst a source of compositor-specific remapping.
        gtk_window.set_decorated(false);
        println!("[WindowType] niri detected; using layer-shell OVERLAY carrier");
        return Ok(());
    } else if is_niri_desktop() {
        // niri decides floating vs. tiling itself. Advertising the carrier as
        // a regular undecorated toplevel gives the compositor a stable surface
        // identity; the managed rule and IPC fallback place it in the floating
        // layer. A DOCK hint is an X11/EWMH request and is not authoritative
        // through xwayland-satellite.
        gtk_window.set_type_hint(gdk::WindowTypeHint::Normal);
        gtk_window.set_decorated(false);
        println!("[WindowType] niri detected; using NORMAL managed floating overlay");
    } else {
        // GNOME/Mutter can leave a maximized transparent NORMAL window below
        // the active application even while _NET_WM_STATE_ABOVE is present.
        // A dock is semantically closer to this desktop overlay there.
        gtk_window.set_type_hint(gdk::WindowTypeHint::Dock);
        println!("[WindowType] non-KDE desktop detected; using DOCK overlay");
    }
    gtk_window.set_keep_above(true);
    gtk_window.stick();
    Ok(())
}

fn find_niri_main_window() -> Result<Option<(u64, bool)>, String> {
    let stdout = run_niri_command(&["msg", "--json", "windows"])?;
    let windows: serde_json::Value = serde_json::from_str(&stdout)
        .map_err(|error| format!("无法解析 niri 窗口列表: {error}"))?;
    let Some(windows) = windows.as_array() else {
        return Err("niri 窗口列表不是数组".to_string());
    };

    Ok(windows.iter().find_map(|entry| {
        let title = entry.get("title").and_then(serde_json::Value::as_str)?;
        let app_id = entry.get("app_id").and_then(serde_json::Value::as_str)?;
        if title != NIRI_WINDOW_TITLE || !app_id.eq_ignore_ascii_case(NIRI_APP_ID) {
            return None;
        }
        let id = entry.get("id").and_then(serde_json::Value::as_u64)?;
        let is_floating = entry
            .get("is_floating")
            .and_then(serde_json::Value::as_bool)
            .unwrap_or(false);
        Some((id, is_floating))
    }))
}

fn move_niri_window_to_floating(window_id: u64) -> Result<(), String> {
    let id = window_id.to_string();
    run_niri_command(&["msg", "action", "move-window-to-floating", "--id", &id]).map(|_| ())
}

fn schedule_niri_post_map_finalization(window: tauri::WebviewWindow) {
    if NIRI_FINALIZATION_SCHEDULED.swap(true, Ordering::SeqCst) {
        return;
    }

    thread::spawn(move || {
        let mut last_error = None;
        let mut mapped_window_id = None;
        for delay_ms in NIRI_FLOAT_RETRY_DELAYS_MS {
            if delay_ms > 0 {
                thread::sleep(Duration::from_millis(delay_ms));
            }

            match find_niri_main_window() {
                Ok(Some((window_id, is_floating))) => {
                    if !is_floating {
                        if let Err(error) = move_niri_window_to_floating(window_id) {
                            last_error = Some(error);
                            continue;
                        }
                        println!("[NiriPolicy] IPC 已将主窗口移入浮动层: id={window_id}");
                    } else {
                        println!("[NiriPolicy] 主窗口已由规则放入浮动层: id={window_id}");
                    }
                    mapped_window_id = Some(window_id);
                    break;
                }
                Ok(None) => {}
                Err(error) => last_error = Some(error),
            }
        }

        let Some(window_id) = mapped_window_id else {
            println!(
                "[NiriPolicy] 未能在重试窗口内确认主窗口已映射{}",
                last_error
                    .map(|error| format!(": {error}"))
                    .unwrap_or_default()
            );
            mark_niri_input_region_ready(window);
            return;
        };

        let expected_size = match virtual_desktop_bounds(&window) {
            Ok(Some((_, _, width, height))) => (width, height),
            Ok(None) => {
                println!("[NiriPolicy] 未发现显示器，保留当前窗口尺寸");
                mark_niri_input_region_ready(window);
                return;
            }
            Err(error) => {
                println!("[NiriPolicy] 无法读取最终显示区域尺寸: {error}");
                mark_niri_input_region_ready(window);
                return;
            }
        };

        if let Err(error) = resize_to_virtual_desktop(&window) {
            println!("[NiriPolicy] 映射后设置最终显示区域尺寸失败: id={window_id} error={error}");
            mark_niri_input_region_ready(window);
            return;
        }
        println!(
            "[NiriPolicy] 映射后已单次请求最终尺寸: id={window_id} expected={}x{}",
            expected_size.0, expected_size.1
        );

        let mut stable_samples = 0;
        let mut last_actual_size = None;
        for _ in 0..NIRI_SIZE_STABILITY_MAX_POLLS {
            match window.inner_size() {
                Ok(size) => {
                    let actual = (size.width, size.height);
                    last_actual_size = Some(actual);
                    if actual == expected_size {
                        stable_samples += 1;
                        if stable_samples >= NIRI_SIZE_STABILITY_REQUIRED_SAMPLES {
                            println!(
                                "[NiriPolicy] 最终尺寸已稳定: id={window_id} actual={}x{} samples={stable_samples}",
                                actual.0, actual.1
                            );
                            mark_niri_input_region_ready(window);
                            return;
                        }
                    } else {
                        stable_samples = 0;
                    }
                }
                Err(error) => last_error = Some(error.to_string()),
            }
            thread::sleep(Duration::from_millis(NIRI_SIZE_STABILITY_POLL_MS));
        }

        println!(
            "[NiriPolicy] 等待最终尺寸稳定超时: id={window_id} expected={}x{} actual={:?}{}",
            expected_size.0,
            expected_size.1,
            last_actual_size,
            last_error
                .map(|error| format!(" error={error}"))
                .unwrap_or_default()
        );
        mark_niri_input_region_ready(window);
    });
}

pub(crate) fn after_main_window_revealed(window: &tauri::WebviewWindow) {
    if NIRI_LAYER_SHELL_ACTIVE.load(Ordering::SeqCst) {
        if NIRI_FINALIZATION_SCHEDULED.swap(true, Ordering::SeqCst) {
            return;
        }
        // gtk_widget_show() realizes the hidden window synchronously. Applying
        // the cached region now means niri sees the layer surface's first
        // interactive commit without waiting for xdg-toplevel IPC discovery.
        println!("[NiriLayerShell] 主窗口已映射，开始提交缓存输入区域");
        mark_niri_input_region_ready(window.clone());
    } else if is_niri_desktop() {
        schedule_niri_post_map_finalization(window.clone());
    }
}

fn virtual_desktop_bounds(
    window: &tauri::WebviewWindow,
) -> Result<Option<(i32, i32, u32, u32)>, String> {
    let monitors = window
        .available_monitors()
        .map_err(|error| error.to_string())?;
    let Some(first) = monitors.first() else {
        return Ok(None);
    };

    let first_position = first.position();
    let first_size = first.size();
    let mut min_x = first_position.x;
    let mut min_y = first_position.y;
    let mut max_x = first_position.x + first_size.width as i32;
    let mut max_y = first_position.y + first_size.height as i32;

    for monitor in &monitors[1..] {
        let position = monitor.position();
        let size = monitor.size();
        min_x = min_x.min(position.x);
        min_y = min_y.min(position.y);
        max_x = max_x.max(position.x + size.width as i32);
        max_y = max_y.max(position.y + size.height as i32);
    }

    Ok(Some((
        min_x,
        min_y,
        (max_x - min_x) as u32,
        (max_y - min_y) as u32,
    )))
}

fn resize_to_virtual_desktop(window: &tauri::WebviewWindow) -> Result<(), String> {
    let Some((min_x, min_y, width, height)) = virtual_desktop_bounds(window)? else {
        return Ok(());
    };

    window
        .set_position(tauri::PhysicalPosition::new(min_x, min_y))
        .map_err(|error| error.to_string())?;
    window
        .set_size(tauri::PhysicalSize::new(width, height))
        .map_err(|error| error.to_string())
}

fn static_input_region_diagnostic() -> Option<InputRectangle> {
    let raw = std::env::var(STATIC_INPUT_REGION_ENV).ok()?;
    let normalized = raw.trim();

    if normalized.eq_ignore_ascii_case("1") || normalized.eq_ignore_ascii_case("true") {
        return Some(STATIC_INPUT_REGION_DEFAULT);
    }

    let values = normalized
        .split(',')
        .map(str::trim)
        .map(str::parse::<i32>)
        .collect::<Result<Vec<_>, _>>()
        .ok()?;

    if values.len() != 4 || values[2] <= 0 || values[3] <= 0 {
        println!(
            "[InputRegionDiagnostic] 环境变量格式无效: {STATIC_INPUT_REGION_ENV}={normalized:?}; 应为 1 或 x,y,width,height"
        );
        return None;
    }

    Some(InputRectangle {
        x: values[0],
        y: values[1],
        width: values[2],
        height: values[3],
    })
}

fn query_x11_input_shape(gdk_window: &gdk::Window) -> Result<Vec<InputRectangle>, String> {
    const SHAPE_INPUT: i32 = 2;

    let x11_window = gdk_window
        .clone()
        .downcast::<gdkx11::X11Window>()
        .map_err(|_| "当前 GDK 窗口不是 X11 窗口".to_string())?;
    let display = unsafe { gdkx11::ffi::gdk_x11_get_default_xdisplay() };
    if display.is_null() {
        return Err("无法取得 X11 Display".to_string());
    }

    let mut count = 0;
    let mut ordering = 0;
    let rectangles = unsafe {
        XShapeGetRectangles(
            display,
            x11_window.xid(),
            SHAPE_INPUT,
            &mut count,
            &mut ordering,
        )
    };
    if rectangles.is_null() {
        return Err("XShapeGetRectangles 未返回输入区域".to_string());
    }

    let result = unsafe {
        std::slice::from_raw_parts(rectangles, count.max(0) as usize)
            .iter()
            .map(|rectangle| InputRectangle {
                x: i32::from(rectangle.x),
                y: i32::from(rectangle.y),
                width: i32::from(rectangle.width),
                height: i32::from(rectangle.height),
            })
            .collect()
    };
    unsafe {
        x11::xlib::XFree(rectangles.cast::<c_void>());
    }
    Ok(result)
}

fn apply_static_input_region(
    gtk_window: &gtk::ApplicationWindow,
    rectangle: InputRectangle,
) -> Result<Vec<InputRectangle>, String> {
    let gdk_window = gtk_window
        .window()
        .ok_or_else(|| "GTK 主窗口尚未创建 GDK 窗口".to_string())?;
    let region = gdk::cairo::Region::create_rectangle(&gdk::cairo::RectangleInt::new(
        rectangle.x,
        rectangle.y,
        rectangle.width,
        rectangle.height,
    ));

    gdk_window.input_shape_combine_region(&region, 0, 0);
    gdk_window.display().flush();
    gdk_window.display().sync();
    query_x11_input_shape(&gdk_window)
}

fn json_number(value: &serde_json::Value, path: &[&str]) -> Option<f64> {
    let mut current = value;
    for segment in path {
        current = current.get(*segment)?;
    }
    current.as_f64().filter(|number| number.is_finite())
}

fn css_input_rectangle(
    values: &[serde_json::Value],
    coordinate_scale: f64,
    padding_css: f64,
) -> Option<InputRectangle> {
    if values.len() != 4 {
        return None;
    }
    let values = values
        .iter()
        .map(serde_json::Value::as_f64)
        .collect::<Option<Vec<_>>>()?;
    let width = values[2];
    let height = values[3];
    if !values.iter().all(|value| value.is_finite()) || width <= 0.0 || height <= 0.0 {
        return None;
    }

    let padding = (padding_css * coordinate_scale).ceil();
    Some(InputRectangle {
        x: (values[0] * coordinate_scale - padding).floor() as i32,
        y: (values[1] * coordinate_scale - padding).floor() as i32,
        width: (width * coordinate_scale + padding * 2.0).ceil() as i32,
        height: (height * coordinate_scale + padding * 2.0).ceil() as i32,
    })
}

fn uses_native_wayland_backend() -> bool {
    std::env::var("GDK_BACKEND")
        .ok()
        .map(|backend| {
            backend
                .split([',', ':'])
                .map(str::trim)
                .any(|value| value.eq_ignore_ascii_case("wayland"))
        })
        .unwrap_or(false)
}

fn pointer_input_rectangles(ignore: bool, reason: &str, details: &str) -> Vec<InputRectangle> {
    let mut rectangles = vec![INPUT_SENTINEL];
    let Ok(details): Result<serde_json::Value, _> = serde_json::from_str(details) else {
        return rectangles;
    };
    let dpr = json_number(&details, &["dpr"])
        .filter(|value| (0.5..=8.0).contains(value))
        .unwrap_or(1.0);
    let native_wayland = uses_native_wayland_backend();
    let coordinate_scale = if native_wayland { 1.0 } else { dpr };

    if let Some(persistent_rectangles) = details
        .get("persistentRects")
        .and_then(serde_json::Value::as_array)
    {
        rectangles.extend(
            persistent_rectangles
                .iter()
                .filter_map(serde_json::Value::as_array)
                .filter_map(|values| css_input_rectangle(values, coordinate_scale, 2.0))
                .take(128),
        );
    }
    if ignore {
        return rectangles;
    }

    let dom_rect = details
        .get("domHit")
        .and_then(|value| value.get("rect"))
        .and_then(serde_json::Value::as_array);
    if let Some(rect) = dom_rect.filter(|rect| rect.len() == 4) {
        if let Some(rectangle) = css_input_rectangle(rect, coordinate_scale, 12.0) {
            rectangles.push(rectangle);
            return rectangles;
        }
    }

    let physical = details
        .get("physical")
        .and_then(serde_json::Value::as_array);
    if let Some(position) = physical.filter(|position| position.len() >= 2) {
        let x = position[0].as_f64();
        let y = position[1].as_f64();
        if let (Some(x), Some(y)) = (x, y) {
            if x.is_finite() && y.is_finite() {
                let physical_size = if reason.starts_with("drag-active") {
                    DRAG_HOT_ZONE_SIZE
                } else {
                    POINTER_HOT_ZONE_SIZE
                };
                let position_scale = if native_wayland { dpr } else { 1.0 };
                let size = (f64::from(physical_size) / position_scale).round().max(1.0) as i32;
                rectangles.push(InputRectangle {
                    x: (x / position_scale).round() as i32 - size / 2,
                    y: (y / position_scale).round() as i32 - size / 2,
                    width: size,
                    height: size,
                });
            }
        }
    }
    rectangles
}

fn apply_input_rectangles(
    gtk_window: &gtk::ApplicationWindow,
    rectangles: &[InputRectangle],
) -> Result<(i32, i32, Vec<InputRectangle>), String> {
    let gdk_window = gtk_window
        .window()
        .ok_or_else(|| "GTK 主窗口尚未创建 GDK 窗口".to_string())?;
    let width = gdk_window.width();
    let height = gdk_window.height();
    let mut clipped = Vec::with_capacity(rectangles.len());

    for rectangle in rectangles {
        let left = rectangle.x.max(0).min(width);
        let top = rectangle.y.max(0).min(height);
        let right = rectangle
            .x
            .saturating_add(rectangle.width)
            .max(0)
            .min(width);
        let bottom = rectangle
            .y
            .saturating_add(rectangle.height)
            .max(0)
            .min(height);
        if right > left && bottom > top {
            clipped.push(InputRectangle {
                x: left,
                y: top,
                width: right - left,
                height: bottom - top,
            });
        }
    }

    if clipped.is_empty() {
        clipped.push(INPUT_SENTINEL);
    }
    let cairo_rectangles: Vec<_> = clipped
        .iter()
        .map(|rectangle| {
            gdk::cairo::RectangleInt::new(
                rectangle.x,
                rectangle.y,
                rectangle.width,
                rectangle.height,
            )
        })
        .collect();
    let region = gdk::cairo::Region::create_rectangles(&cairo_rectangles);
    gdk_window.input_shape_combine_region(&region, 0, 0);
    gdk_window.display().flush();

    Ok((width, height, clipped))
}

fn recommit_input_rectangles(
    gtk_window: &gtk::ApplicationWindow,
    rectangles: &[InputRectangle],
) -> Result<(i32, i32, Vec<InputRectangle>), String> {
    let gdk_window = gtk_window
        .window()
        .ok_or_else(|| "GTK 主窗口尚未创建 GDK 窗口".to_string())?;
    let full_region = gdk::cairo::Region::create_rectangle(&gdk::cairo::RectangleInt::new(
        0,
        0,
        gdk_window.width().max(1),
        gdk_window.height().max(1),
    ));

    // Force two distinct XShape updates after the final mapped size. This
    // gives Xwayland/satellite a geometry-consistent surface commit instead of
    // reusing the input region created for the hidden startup dimensions.
    gdk_window.input_shape_combine_region(&full_region, 0, 0);
    gdk_window.display().flush();
    gdk_window.display().sync();

    let result = apply_input_rectangles(gtk_window, rectangles)?;
    gdk_window.display().sync();
    Ok(result)
}

fn apply_native_wayland_input_rectangles(
    gtk_window: &gtk::ApplicationWindow,
    rectangles: &[InputRectangle],
) -> Result<(i32, i32, Vec<InputRectangle>), String> {
    let gdk_window = gtk_window
        .window()
        .ok_or_else(|| "GTK 主窗口尚未创建 GDK 窗口".to_string())?;
    let width = gdk_window.width();
    let height = gdk_window.height();
    let mut clipped = Vec::with_capacity(rectangles.len());

    for rectangle in rectangles {
        let left = rectangle.x.max(0).min(width);
        let top = rectangle.y.max(0).min(height);
        let right = rectangle
            .x
            .saturating_add(rectangle.width)
            .max(0)
            .min(width);
        let bottom = rectangle
            .y
            .saturating_add(rectangle.height)
            .max(0)
            .min(height);
        if right > left && bottom > top {
            clipped.push(InputRectangle {
                x: left,
                y: top,
                width: right - left,
                height: bottom - top,
            });
        }
    }
    if clipped.is_empty() {
        clipped.push(INPUT_SENTINEL);
    }

    let cairo_rectangles: Vec<_> = clipped
        .iter()
        .map(|rectangle| {
            gdk::cairo::RectangleInt::new(
                rectangle.x,
                rectangle.y,
                rectangle.width,
                rectangle.height,
            )
        })
        .collect();
    let gdk_region = gdk::cairo::Region::create_rectangles(&cairo_rectangles);
    let diagnostics_enabled = input_region_diagnostics_enabled();

    // Keep GDK's cached input region in sync with the native wl_surface.
    // Otherwise a later GTK/WebKit surface commit can replace the one-shot
    // native region with GDK's previous full-window input state.
    gdk_window.input_shape_combine_region(&gdk_region, 0, 0);

    // GDK3 defers its Wayland input-region update until its next surface
    // commit. Submit the same region directly as well so pointer routing
    // changes immediately instead of waiting for the next WebKit frame.
    unsafe {
        let gdk_window_ptr: *mut gdk::ffi::GdkWindow = gdk_window.to_glib_none().0;
        let display = gdk_window.display();
        let gdk_display_ptr: *mut gdk::ffi::GdkDisplay = display.to_glib_none().0;
        let surface = gdk_wayland_window_get_wl_surface(gdk_window_ptr.cast());
        let compositor = gdk_wayland_display_get_wl_compositor(gdk_display_ptr.cast());
        if surface.is_null() || compositor.is_null() {
            return Err("无法获取 niri Wayland surface/compositor".to_string());
        }
        let surface_id = wl_proxy_get_id(surface);
        let compositor_id = wl_proxy_get_id(compositor);

        let region = wl_proxy_marshal_flags(
            compositor,
            WL_COMPOSITOR_CREATE_REGION,
            std::ptr::addr_of!(wl_region_interface),
            wl_proxy_get_version(compositor),
            0,
            std::ptr::null_mut::<c_void>(),
        );
        if region.is_null() {
            return Err("无法创建 niri Wayland 输入区域".to_string());
        }
        let region_id = wl_proxy_get_id(region);

        if diagnostics_enabled {
            let window_area = i64::from(width.max(1)) * i64::from(height.max(1));
            let union_area: i64 = (0..gdk_region.num_rectangles())
                .map(|index| gdk_region.rectangle(index))
                .map(|rectangle| i64::from(rectangle.width()) * i64::from(rectangle.height()))
                .sum();
            let mut extents = gdk::cairo::RectangleInt::new(0, 0, 0, 0);
            gdk_region.extents(&mut extents);
            let coverage_percent = union_area as f64 * 100.0 / window_area as f64;

            println!(
                "[InputRegionDiagnostic] backend={} gdk_window={} scale_factor={} window_size={width}x{height} surface_id={surface_id} compositor_id={compositor_id} region_id={region_id} source_rectangles={} union_rectangles={} union_area={union_area} coverage_percent={coverage_percent:.2} extents=({},{},{},{}) rectangles={clipped:?}",
                gdk_window.display().type_().name(),
                gdk_window.type_().name(),
                gdk_window.scale_factor(),
                clipped.len(),
                gdk_region.num_rectangles(),
                extents.x(),
                extents.y(),
                extents.width(),
                extents.height()
            );

            if NIRI_DIAGNOSTIC_LAST_SURFACE_ID.swap(surface_id, Ordering::SeqCst) != surface_id {
                println!(
                    "[InputRegionDiagnostic] GDK surface tree for root surface_id={surface_id}: {:?}",
                    describe_wayland_gdk_surface_tree(&gdk_window)
                );
            }
        }

        for rectangle in &clipped {
            wl_proxy_marshal_flags(
                region,
                WL_REGION_ADD,
                std::ptr::null(),
                wl_proxy_get_version(region),
                0,
                rectangle.x,
                rectangle.y,
                rectangle.width,
                rectangle.height,
            );
        }
        wl_proxy_marshal_flags(
            surface,
            WL_SURFACE_SET_INPUT_REGION,
            std::ptr::null(),
            wl_proxy_get_version(surface),
            0,
            region,
        );
        wl_proxy_marshal_flags(
            surface,
            WL_SURFACE_COMMIT,
            std::ptr::null(),
            wl_proxy_get_version(surface),
            0,
        );
        wl_proxy_marshal_flags(
            region,
            WL_REGION_DESTROY,
            std::ptr::null(),
            wl_proxy_get_version(region),
            WL_MARSHAL_FLAG_DESTROY,
        );
    }
    gdk_window.display().flush();
    Ok((width, height, clipped))
}

fn mark_niri_input_region_ready(window: tauri::WebviewWindow) {
    NIRI_INPUT_REGION_READY.store(true, Ordering::SeqCst);

    let pending = NIRI_PENDING_INPUT_REGION
        .lock()
        .ok()
        .and_then(|mut pending| pending.take());
    let pending = pending.unwrap_or_else(|| PendingInputRegion {
        generation: INPUT_REGION_GENERATION.fetch_add(1, Ordering::SeqCst) + 1,
        ignore: true,
        reason: "niri-post-map-sentinel".to_string(),
        rectangles: vec![INPUT_SENTINEL],
    });
    let generation = pending.generation;
    let gtk_window_handle = window.clone();

    let scheduling_result = window.run_on_main_thread(move || {
        if INPUT_REGION_GENERATION.load(Ordering::SeqCst) != generation {
            println!(
                "[ClickThrough] niri 映射后输入区域任务已过期: generation={generation}"
            );
            return;
        }

        let result = gtk_window_handle
            .gtk_window()
            .map_err(|error| error.to_string())
            .and_then(|gtk_window| {
                if uses_native_wayland_backend() {
                    apply_native_wayland_input_rectangles(&gtk_window, &pending.rectangles)
                } else if let Some(rectangle) = static_input_region_diagnostic() {
                    let actual = apply_static_input_region(&gtk_window, rectangle)?;
                    let gdk_window = gtk_window
                        .window()
                        .ok_or_else(|| "GTK 主窗口尚未创建 GDK 窗口".to_string())?;
                    Ok((gdk_window.width(), gdk_window.height(), actual))
                } else {
                    recommit_input_rectangles(&gtk_window, &pending.rectangles)
                }
            });

        match result {
            Ok((width, height, applied)) => println!(
                "[ClickThrough] niri 映射后输入区域已重新提交: generation={generation} ignore={} reason={} region_count={} window_size={width}x{height}",
                pending.ignore,
                pending.reason,
                applied.len()
            ),
            Err(error) => println!(
                "[ClickThrough] niri 映射后输入区域重新提交失败: generation={generation} error={error}"
            ),
        }
    });

    if let Err(error) = scheduling_result {
        println!("[ClickThrough] 无法调度 niri 映射后输入区域: {error}");
    }
}

pub(crate) fn startup_input_region(window: &tauri::WebviewWindow) -> Result<(), String> {
    if is_niri_desktop() {
        let wait_for = if NIRI_LAYER_SHELL_ACTIVE.load(Ordering::SeqCst) {
            "layer-shell 映射"
        } else {
            "映射和最终尺寸稳定"
        };
        println!("[ClickThrough] niri 启动输入区域已延迟到{wait_for}之后");
        return Ok(());
    }

    let gtk_window = window.gtk_window().map_err(|error| error.to_string())?;
    if !gtk_window.is_realized() {
        // The main window is intentionally created hidden so KWin never
        // presents WebKitGTK's unpainted fullscreen surface. Realizing creates
        // the backing GDK window needed by XShape without mapping it onscreen.
        gtk_window.realize();
        println!("[WindowVisibility] Linux 隐藏主窗口已完成 GTK realize");
    }
    if let Some(rectangle) = static_input_region_diagnostic() {
        let actual = apply_static_input_region(&gtk_window, rectangle)?;
        println!(
            "[InputRegionDiagnostic] 启动时固定区域已核验: requested={rectangle:?} actual={actual:?}"
        );
    } else {
        let (width, height, regions) = apply_input_rectangles(&gtk_window, &[INPUT_SENTINEL])?;
        println!(
            "[ClickThrough] Linux 启动哨兵区域已应用: regions={regions:?} window_size={width}x{height}"
        );
    }
    Ok(())
}

pub(crate) fn set_click_through(
    window: &tauri::Window,
    ignore: bool,
    reason: &str,
    details: &str,
) -> Result<(), String> {
    let defer_for_niri = is_niri_desktop() && !NIRI_INPUT_REGION_READY.load(Ordering::SeqCst);

    if let Some(rectangle) = static_input_region_diagnostic() {
        if defer_for_niri {
            println!(
                "[InputRegionDiagnostic] niri 尚未完成最终尺寸，固定区域延迟应用: requested={rectangle:?}"
            );
            return Ok(());
        }

        let gtk_window_handle = window.clone();
        let reason = reason.to_string();
        window
            .run_on_main_thread(move || {
                match gtk_window_handle
                    .gtk_window()
                    .map_err(|error| error.to_string())
                    .and_then(|gtk_window| apply_static_input_region(&gtk_window, rectangle))
                {
                    Ok(actual) => println!(
                        "[InputRegionDiagnostic] 固定区域已核验: requested={rectangle:?} actual={actual:?}; 已忽略动态请求 ignore={ignore} reason={reason}"
                    ),
                    Err(error) => println!(
                        "[InputRegionDiagnostic] 固定区域应用或核验失败: requested={rectangle:?} error={error}"
                    ),
                }
            })
            .map_err(|error| error.to_string())?;
        return Ok(());
    }

    let rectangles = pointer_input_rectangles(ignore, reason, details);
    let region_reason = reason.to_string();
    let generation = INPUT_REGION_GENERATION.fetch_add(1, Ordering::SeqCst) + 1;

    if defer_for_niri {
        if let Ok(mut pending) = NIRI_PENDING_INPUT_REGION.lock() {
            *pending = Some(PendingInputRegion {
                generation,
                ignore,
                reason: region_reason,
                rectangles,
            });
        }
        println!(
            "[ClickThrough] niri 输入区域已缓存，等待映射后最终尺寸: generation={generation} ignore={ignore} reason={reason}"
        );
        return Ok(());
    }

    let gtk_window_handle = window.clone();

    window
        .run_on_main_thread(move || {
            if INPUT_REGION_GENERATION.load(Ordering::SeqCst) != generation {
                println!(
                    "[ClickThrough] Linux 输入区域任务已丢弃: generation={generation} ignore={ignore}"
                );
                return;
            }

            let result = gtk_window_handle
                .gtk_window()
                .map_err(|error| error.to_string())
                .and_then(|gtk_window| {
                    if uses_native_wayland_backend() {
                        apply_native_wayland_input_rectangles(&gtk_window, &rectangles)
                    } else {
                        apply_input_rectangles(&gtk_window, &rectangles)
                    }
                });

            match result {
                Ok((width, height, applied)) => {
                    let preview_len = applied.len().min(12);
                    let mode = if uses_native_wayland_backend() {
                        "Wayland 原生局部输入区域"
                    } else {
                        "X11 局部输入区域"
                    };
                    println!(
                        "[ClickThrough] Linux {mode}已应用: generation={generation} ignore={ignore} reason={region_reason} region_count={} region_preview={:?} window_size={width}x{height}",
                        applied.len(),
                        &applied[..preview_len]
                    );
                }
                Err(error) => println!(
                    "[ClickThrough] Linux 输入区域应用失败: generation={generation} ignore={ignore} error={error}"
                ),
            }
        })
        .map_err(|error| error.to_string())
}

pub(crate) fn prepare_for_navigation(_window: &tauri::WebviewWindow) {
    // Do not call TAO's whole-window restore here. It would overwrite the
    // persistent X Shape region installed for Linux.
}

pub(crate) fn diagnostic_query() -> Option<&'static str> {
    if static_input_region_diagnostic().is_some() {
        Some("neko_x11_static_input_region=1")
    } else if uses_native_wayland_backend() {
        Some("neko_native_wayland_input_region=1")
    } else {
        None
    }
}

pub(crate) fn cursor_sample(window: &tauri::WebviewWindow) -> Option<CursorSample> {
    let cursor = window.cursor_position().ok()?;
    let origin = window.inner_position().ok()?;
    let css_scale = if uses_native_wayland_backend() {
        window.scale_factor().ok().filter(|scale| *scale > 0.0)?
    } else {
        1.0
    };
    Some(CursorSample {
        absolute: (cursor.x, cursor.y),
        relative: (
            (cursor.x - f64::from(origin.x)) / css_scale,
            (cursor.y - f64::from(origin.y)) / css_scale,
        ),
    })
}

pub(crate) fn open_settings_window(
    app: &tauri::AppHandle,
    url: tauri::Url,
    label: String,
) -> Result<(), String> {
    let builder = tauri::WebviewWindowBuilder::new(app, label, tauri::WebviewUrl::External(url))
        .title("设置面板")
        .inner_size(900.0, 700.0)
        .transparent(false)
        .decorations(true)
        .always_on_top(true);

    let builder = if let Some(parent) = app.get_webview_window("main") {
        builder
            .transient_for(&parent)
            .map_err(|error| format!("无法将设置面板关联到主覆盖层: {error}"))?
    } else {
        builder
    };

    let window = builder.build().map_err(|error| error.to_string())?;
    configure_media_permissions(&window)
}

#[cfg(test)]
mod tests {
    use super::{
        is_kde_desktop_value, is_niri_desktop_value, is_trusted_media_origin,
        merge_niri_managed_config, niri_managed_config_block, pointer_input_rectangles,
        static_input_region_diagnostic, InputRectangle, INPUT_SENTINEL, NIRI_CONFIG_PATCH_BEGIN,
        NIRI_CONFIG_PATCH_END, STATIC_INPUT_REGION_DEFAULT, STATIC_INPUT_REGION_ENV,
    };

    #[test]
    fn recognizes_kde_and_plasma_desktop_identifiers() {
        for value in [
            "KDE",
            "KDE:Plasma",
            "plasma",
            "plasmawayland",
            "/usr/share/xsessions/plasma",
        ] {
            assert!(is_kde_desktop_value(value), "{value}");
        }

        for value in ["GNOME", "ubuntu:GNOME", "X-Cinnamon", "XFCE", ""] {
            assert!(!is_kde_desktop_value(value), "{value}");
        }
    }

    #[test]
    fn recognizes_niri_desktop_identifiers_without_misclassifying_other_desktops() {
        for value in ["niri", "NIRI", "GNOME:niri", "niri;wayland"] {
            assert!(is_niri_desktop_value(value), "{value}");
        }

        for value in ["GNOME", "KDE", "plasmawayland", "sway", ""] {
            assert!(!is_niri_desktop_value(value), "{value}");
        }
    }

    #[test]
    fn appends_and_updates_the_managed_niri_rule_idempotently() {
        let original = "layout {\n    gaps 12\n}\n";
        let (appended, changed) = merge_niri_managed_config(original);
        assert!(changed);
        assert!(appended.contains(NIRI_CONFIG_PATCH_BEGIN));
        assert!(appended.contains("open-floating true"));
        assert!(appended.contains(r##"title=r#"^N\.E\.K\.O$"#"##));

        let (unchanged, changed_again) = merge_niri_managed_config(&appended);
        assert!(!changed_again);
        assert_eq!(unchanged, appended);

        let stale = format!(
            "before\n\n{NIRI_CONFIG_PATCH_BEGIN}\nold rule\n{NIRI_CONFIG_PATCH_END}\n\nafter\n"
        );
        let (updated, updated_changed) = merge_niri_managed_config(&stale);
        assert!(updated_changed);
        assert!(updated.contains(&niri_managed_config_block()));
        assert!(!updated.contains("old rule"));
        assert!(updated.starts_with("before"));
        assert!(updated.ends_with("after\n"));
    }

    #[test]
    fn allows_media_only_from_the_local_backend_origin() {
        assert!(is_trusted_media_origin("http://127.0.0.1:48911/"));
        assert!(is_trusted_media_origin(
            "http://localhost:48911/settings?tab=microphone"
        ));
        assert!(is_trusted_media_origin("http://[::1]:48911/"));

        assert!(!is_trusted_media_origin("http://127.0.0.1:1420/"));
        assert!(!is_trusted_media_origin("https://127.0.0.1:48911/"));
        assert!(!is_trusted_media_origin("http://example.com:48911/"));
        assert!(!is_trusted_media_origin(
            "http://127.0.0.1.evil.example:48911/"
        ));
        assert!(!is_trusted_media_origin("not a url"));
    }

    #[test]
    fn parses_static_input_region_diagnostic_configuration() {
        let previous = std::env::var_os(STATIC_INPUT_REGION_ENV);

        std::env::set_var(STATIC_INPUT_REGION_ENV, "1");
        assert_eq!(
            static_input_region_diagnostic(),
            Some(STATIC_INPUT_REGION_DEFAULT)
        );

        std::env::set_var(STATIC_INPUT_REGION_ENV, "10,20,300,400");
        assert_eq!(
            static_input_region_diagnostic(),
            Some(InputRectangle {
                x: 10,
                y: 20,
                width: 300,
                height: 400,
            })
        );

        std::env::set_var(STATIC_INPUT_REGION_ENV, "10,20,0,400");
        assert_eq!(static_input_region_diagnostic(), None);

        match previous {
            Some(value) => std::env::set_var(STATIC_INPUT_REGION_ENV, value),
            None => std::env::remove_var(STATIC_INPUT_REGION_ENV),
        }
    }

    #[test]
    fn keeps_only_the_sentinel_for_transparent_pixels() {
        assert_eq!(
            pointer_input_rectangles(true, "transparent", r#"{"physical":[339,189],"dpr":1.25}"#),
            vec![INPUT_SENTINEL]
        );
    }

    #[test]
    fn keeps_persistent_ui_regions_over_transparency() {
        assert_eq!(
            pointer_input_rectangles(
                true,
                "transparent",
                r#"{"dpr":1.25,"persistentRects":[[24,24,320,220],[100,500,40,20]]}"#
            ),
            vec![
                INPUT_SENTINEL,
                InputRectangle {
                    x: 27,
                    y: 27,
                    width: 406,
                    height: 281,
                },
                InputRectangle {
                    x: 122,
                    y: 622,
                    width: 56,
                    height: 31,
                },
            ]
        );
    }

    #[test]
    fn converts_dom_css_bounds_to_x11_physical_coordinates() {
        assert_eq!(
            pointer_input_rectangles(
                false,
                "dom-hit",
                r#"{"physical":[339,189],"dpr":1.25,"domHit":{"rect":[24,24,320,220]}}"#
            ),
            vec![
                INPUT_SENTINEL,
                InputRectangle {
                    x: 15,
                    y: 15,
                    width: 430,
                    height: 305,
                }
            ]
        );
    }

    #[test]
    fn creates_pointer_and_drag_hot_zones() {
        assert_eq!(
            pointer_input_rectangles(
                false,
                "live2d-alpha",
                r#"{"physical":[339,189],"dpr":1.25}"#
            )[1],
            InputRectangle {
                x: 243,
                y: 93,
                width: 192,
                height: 192,
            }
        );
        assert_eq!(
            pointer_input_rectangles(false, "drag-active", r#"{"physical":[339,189],"dpr":1.25}"#)
                [1],
            InputRectangle {
                x: -45,
                y: -195,
                width: 768,
                height: 768,
            }
        );
    }
}
