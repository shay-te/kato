// Empty preload. The kato planning UI is a normal web app served
// over HTTP and doesn't need to bridge to native APIs — the only
// reason this file exists is to set ``contextIsolation: true`` on
// the BrowserWindow without Electron complaining about the missing
// preload script.
//
// If we ever want to expose native niceties (file:// drag-drop,
// notification badge counts, save-as via OS dialog), this is the
// file that owns the contextBridge.
