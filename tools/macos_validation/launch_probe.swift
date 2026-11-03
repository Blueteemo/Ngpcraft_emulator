import AppKit
import CoreGraphics
import Foundation
import ApplicationServices

let appURL = URL(fileURLWithPath: CommandLine.arguments[1]).standardizedFileURL
let settings = appURL.appendingPathComponent("Contents/MacOS/config.ini").path
let config = NSWorkspace.OpenConfiguration()
config.createsNewApplicationInstance = true
config.environment = ["NGPCRAFT_SETTINGS": settings, "NGPCRAFT_NO_UPNP": "1"]
let start = ProcessInfo.processInfo.systemUptime
var firstWindow: Double? = nil
var samples: [[String: Any]] = []
var launched = false
var failure: String? = nil
var savedIcons = Set<pid_t>()
let dock = NSWorkspace.shared.runningApplications.first { $0.bundleIdentifier == "com.apple.dock" }
func children(_ element: AXUIElement) -> [AXUIElement] {
    var value: CFTypeRef?
    AXUIElementCopyAttributeValue(element, kAXChildrenAttribute as CFString, &value)
    return value as? [AXUIElement] ?? []
}
func title(_ element: AXUIElement) -> String {
    var value: CFTypeRef?
    AXUIElementCopyAttributeValue(element, kAXTitleAttribute as CFString, &value)
    return value as? String ?? ""
}
func running(_ element: AXUIElement) -> Bool {
    var value: CFTypeRef?
    AXUIElementCopyAttributeValue(element, "AXIsApplicationRunning" as CFString, &value)
    return value as? Bool ?? false
}
NSWorkspace.shared.openApplication(at: appURL, configuration: config) { app, error in
    launched = true
    if let error = error { failure = error.localizedDescription }
}
let timer = Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { timer in
    let elapsed = ProcessInfo.processInfo.systemUptime - start
    let apps = NSWorkspace.shared.runningApplications.filter {
        $0.executableURL?.path.hasPrefix(appURL.path + "/") == true
    }
    let pids = Set(apps.map { Int($0.processIdentifier) })
    var dockCount = 0
    if let dock = dock {
        for list in children(AXUIElementCreateApplication(dock.processIdentifier)) {
            dockCount += children(list).filter { title($0).contains("NgpCraft") && running($0) }.count
        }
    }
    let windows = (CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] ?? []).filter {
        guard let pid = $0[kCGWindowOwnerPID as String] as? Int,
              let bounds = $0[kCGWindowBounds as String] as? [String: NSNumber] else { return false }
        return pids.contains(pid) && (bounds["Width"]?.doubleValue ?? 0) > 300 && (bounds["Height"]?.doubleValue ?? 0) > 200
    }
    if !windows.isEmpty && firstWindow == nil { firstWindow = elapsed }
    samples.append(["seconds": elapsed, "apps": apps.map {
        ["pid": Int($0.processIdentifier), "policy": $0.activationPolicy.rawValue,
         "name": $0.localizedName ?? "", "executable": $0.executableURL?.path ?? ""] as [String: Any]
    }, "window_count": windows.count, "dock_items": dockCount])
    if elapsed > (firstWindow.map { $0 + 1.0 } ?? 35.0) || failure != nil {
        timer.invalidate()
        // Blank-window timing only; never run this probe against a user's install.
        for app in apps {
            let ax = AXUIElementCreateApplication(app.processIdentifier)
            var value: CFTypeRef?
            if AXUIElementCopyAttributeValue(ax, kAXWindowsAttribute as CFString, &value) == .success,
               let axWindows = value as? [AXUIElement] {
                for window in axWindows {
                    var button: CFTypeRef?
                    if AXUIElementCopyAttributeValue(window, kAXCloseButtonAttribute as CFString, &button) == .success, let button = button {
                        AXUIElementPerformAction(button as! AXUIElement, kAXPressAction as CFString)
                    }
                }
            }
        }
        let result: [String: Any] = ["app": appURL.path, "first_window_seconds": firstWindow as Any? ?? NSNull(), "error": failure as Any? ?? NSNull(), "samples": samples]
        let data = try! JSONSerialization.data(withJSONObject: result, options: [.prettyPrinted, .sortedKeys])
        FileHandle.standardOutput.write(data)
        exit(firstWindow == nil ? 1 : 0)
    }
}
RunLoop.main.run()
