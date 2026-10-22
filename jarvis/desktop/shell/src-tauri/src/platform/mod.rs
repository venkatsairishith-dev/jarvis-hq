//! Native desktop integration boundary.
//!
//! The web UI and backend lifecycle are shared. Window levels, cursor
//! coordinates and click-through behavior are delegated to exactly one
//! platform implementation at compile time.

#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct CursorSample {
    pub(crate) absolute: (f64, f64),
    pub(crate) relative: (f64, f64),
}

#[cfg(target_os = "linux")]
mod linux;
#[cfg(target_os = "macos")]
mod macos;
#[cfg(target_os = "windows")]
mod windows;

#[cfg(target_os = "linux")]
pub(crate) use linux::*;
#[cfg(target_os = "macos")]
pub(crate) use macos::*;
#[cfg(target_os = "windows")]
pub(crate) use windows::*;

#[cfg(not(any(target_os = "linux", target_os = "macos", target_os = "windows")))]
compile_error!("N.E.K.O desktop currently supports Linux, macOS and Windows");
