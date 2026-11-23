import SwiftUI
import UIKit
import UserNotifications

struct PushRoute: Equatable {
    let room: String
    let machine: Int?
    let end: Double?
}

@MainActor
final class PushBridge: NSObject, ObservableObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    @Published var authorization: UNAuthorizationStatus = .notDetermined
    @Published var token: String?
    @Published var route: PushRoute?
    @Published var registrationError: String?
    private var requesting = false
    var enabled = true

    static var sandbox: Bool {
        #if DEBUG
        true
        #else
        false
        #endif
    }

    func application(_ application: UIApplication, didFinishLaunchingWithOptions options: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        UNUserNotificationCenter.current().delegate = self
        return true
    }

    func prepare(enabled: Bool) async {
        self.enabled = enabled
        guard enabled, !requesting else { return }
        requesting = true
        defer { requesting = false }
        let center = UNUserNotificationCenter.current()
        authorization = await center.notificationSettings().authorizationStatus
        if authorization == .notDetermined {
            do {
                _ = try await center.requestAuthorization(options: [.alert, .sound, .badge])
                authorization = await center.notificationSettings().authorizationStatus
            } catch {
                registrationError = "iOS couldn’t open the notification permission prompt."
            }
        }
        if authorization == .authorized || authorization == .provisional {
            UIApplication.shared.registerForRemoteNotifications()
        }
    }

    func application(_ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        registrationError = nil
        token = deviceToken.map { String(format: "%02x", $0) }.joined()
    }

    func application(_ application: UIApplication, didFailToRegisterForRemoteNotificationsWithError error: Error) {
        registrationError = "This build couldn’t register for Apple notifications. Check its signing and push capability."
    }

    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification,
                                           withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        Task { @MainActor in completionHandler(enabled ? [.banner, .sound, .list] : []) }
    }

    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse,
                                           withCompletionHandler completionHandler: @escaping () -> Void) {
        let data = response.notification.request.content.userInfo
        if let room = data["room"] as? String {
            let turn = data["kind"] as? String == "turn"
            let next = PushRoute(room: room, machine: turn ? nil : data["machine"] as? Int,
                                 end: turn ? nil : data["end"] as? Double)
            Task { @MainActor in route = next }
        }
        completionHandler()
    }
}

@main @MainActor
struct LoopLaundryApp: App {
    @UIApplicationDelegateAdaptor(PushBridge.self) private var push
    @StateObject private var store = LoopStore()

    var body: some Scene {
        WindowGroup {
            HomeView(store: store, push: push)
                .tint(Color(red: 0.21, green: 0.37, blue: 0.49))
        }
    }
}

@MainActor
struct HomeView: View {
    enum Sheet: Identifiable {
        case join, create, edit, preferences, start(Machine, String), pickup(Machine, String)
        var id: String {
            switch self {
            case .join: return "join"
            case .create: return "create"
            case .edit: return "edit"
            case .preferences: return "notifications"
            case .start(let machine, let room): return "start-\(room)-\(machine.id)"
            case .pickup(let machine, let room): return "pickup-\(room)-\(machine.id)"
            }
        }
    }

    @ObservedObject var store: LoopStore
    @ObservedObject var push: PushBridge
    @Environment(\.scenePhase) private var phase
    @State private var sheet: Sheet?
    @State private var entry = ""
    @State private var form = RoomSettings()
    @State private var minutes = 35
    @State private var filter = "all"
    @State private var leavePrompt = false
    @State private var forceTarget: Machine?
    @State private var registrationMessage: String?

    var body: some View {
        NavigationStack {
            Group {
                if let room = store.room {
                    dashboard(room)
                } else {
                    welcome
                }
            }
            .navigationTitle(store.room?.name ?? "Loop Laundry")
            .toolbar {
                if store.room != nil {
                    ToolbarItem(placement: .topBarTrailing) {
                        Menu {
                            ForEach(store.access.rooms) { room in
                                Button { store.switchRoom(room.code) } label: {
                                    if room.code == store.room?.code { Label(room.name, systemImage: "checkmark") }
                                    else { Text(room.name) }
                                }
                            }
                            Button("Join a room", systemImage: "plus") { showJoin() }
                            Button("Create a room", systemImage: "house") { showCreate() }
                            Button(role: .destructive) {
                                leavePrompt = true
                            } label: {
                                Label("Leave current room", systemImage: "rectangle.portrait.and.arrow.right")
                            }
                        } label: { Label("Rooms", systemImage: "building.2") }
                        .disabled(store.busy)
                    }
                }
                ToolbarItem(placement: .topBarLeading) {
                    Button { sheet = .preferences } label: {
                        Image(systemName: store.access.notificationsEnabled ? "bell" : "bell.slash")
                    }.accessibilityLabel("Notification settings")
                }
            }
        }
        .sheet(item: $sheet, content: editor)
        .alert("Loop Laundry", isPresented: Binding(get: { sheet == nil && store.error != nil }, set: { if !$0 { store.error = nil } })) {
            Button("OK") { store.error = nil }
        } message: { Text(store.error ?? "") }
        .confirmationDialog("Leave this room?", isPresented: $leavePrompt, titleVisibility: .visible) {
            Button("Leave room", role: .destructive) { Task { await store.leave() } }
        } message: { Text("Your queue spots in this room will be released.") }
        .confirmationDialog("Release this finished cycle?", isPresented: Binding(get: { forceTarget != nil }, set: { if !$0 { forceTarget = nil } })) {
            if let target = forceTarget, let room = store.room {
                Button("Force stop", role: .destructive) {
                    forceTarget = nil
                    Task { _ = await store.action("force-stop", for: target, in: room.code) }
                }
            }
        } message: { Text("Check that the drum is empty before releasing the machine.") }
        .task(id: store.room?.code) {
            guard store.room != nil else { return }
            _ = await store.refresh(showFailure: true)
            await preparePush()
            while !Task.isCancelled {
                do { try await Task.sleep(for: .seconds(5)) } catch { break }
                if phase == .active { _ = await store.refresh() }
            }
        }
        .onChange(of: phase) { _, state in
            if state == .active {
                store.loadAccess()
                if store.room != nil { Task { await preparePush() } }
            }
        }
        .onReceive(push.$token) { token in
            if let token { Task { await register(token) } }
        }
        .onReceive(push.$route) { incoming in
            guard let incoming else { return }
            Task { await openNotification(incoming) }
        }
        .onOpenURL { url in
            guard let code = RoomCode.read(url.absoluteString) else { return }
            if store.access.rooms.contains(where: { $0.code == code }) { store.switchRoom(code) }
            else { showJoin(code) }
        }
    }

    private var welcome: some View {
        VStack(alignment: .leading, spacing: 20) {
            Image(systemName: "washer").font(.system(size: 48, weight: .light))
                .foregroundStyle(Color.accentColor)
                .accessibilityHidden(true)
            Text("A little more flow\nto your day.").font(.system(.largeTitle, design: .serif))
            Text("Check shared washers and dryers, join a queue, and get a reminder when it’s time to head down.")
                .foregroundStyle(.secondary)
            Button("Join a room", systemImage: "arrow.right.circle") { showJoin() }
                .buttonStyle(.borderedProminent)
            Button("Create a room", systemImage: "plus") { showCreate() }
                .buttonStyle(.bordered)
            Spacer()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(24)
    }

    private func dashboard(_ room: Room) -> some View {
        List {
            Section {
                Picker("Machine type", selection: $filter) {
                    Text("All").tag("all")
                    Text("Washers").tag("washer")
                    Text("Dryers").tag("dryer")
                }.pickerStyle(.segmented)
            }
            if !store.machines.isEmpty {
                Section("Machines") {
                    ForEach(store.machines.filter { filter == "all" || $0.type == filter }) { machine in
                        machineRow(machine, room)
                    }
                }
            }
            Section("Room") {
                LabeledContent("Room code", value: room.code)
                    .textSelection(.enabled)
                ShareLink(item: URL(string: "https://loop-laundry.vercel.app/?room=\(room.code)")!) {
                    Label("Invite residents", systemImage: "square.and.arrow.up")
                }
                Button("Edit room", systemImage: "slider.horizontal.3") {
                    form = store.settings
                    sheet = .edit
                }
                .disabled(!store.loadedRoom)
            }
        }
        .refreshable { _ = await store.refresh(showFailure: true) }
    }

    private func machineRow(_ machine: Machine, _ room: Room) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Label(machine.name, systemImage: machine.type == "washer" ? "washer" : "dryer").font(.headline)
            Text(machine.status).font(.subheadline).foregroundStyle(.secondary)
            if let position = machine.queuePosition, machine.turnDeadline == nil {
                Text("Queue position \(position)").font(.caption).foregroundStyle(.secondary)
            } else if machine.queueLength > 0 && machine.queuePosition == nil {
                Text("\(machine.queueLength) in queue").font(.caption).foregroundStyle(.secondary)
            }
            HStack {
                if machine.canStart {
                    Button("Start a load") {
                        minutes = machine.type == "washer" ? 35 : 45
                        sheet = .start(machine, room.code)
                    }
                } else if machine.mine && machine.state == "done" {
                    Button("Clothes picked up") { act("clear", machine, room.code) }
                } else if machine.mine && machine.state == "busy" {
                    Button("End cycle") { act("finish", machine, room.code) }
                }
                if !machine.mine && (!machine.canStart || machine.queuePosition != nil) {
                    Button(machine.queuePosition == nil ? "Join queue" : "Leave queue") {
                        act("queue", machine, room.code)
                    }
                }
                if !machine.mine && machine.state == "done" {
                    Button("Force stop", role: .destructive) { forceTarget = machine }
                }
            }
            .buttonStyle(.bordered)
            .disabled(store.busy)
        }
        .padding(.vertical, 6)
    }

    private func editor(_ item: Sheet) -> some View {
        NavigationStack {
            Form {
                switch item {
                case .join:
                    TextField("Room code or invite link", text: $entry)
                        .textInputAutocapitalization(.characters).autocorrectionDisabled()
                    Button("Join room") { submit { await store.join(entry) } }
                        .disabled(entry.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || store.busy)
                case .create, .edit:
                    TextField("Room name", text: $form.name)
                    Stepper("Washers: \(form.washers)", value: $form.washers, in: 0...50)
                    Stepper("Dryers: \(form.dryers)", value: $form.dryers, in: 0...50)
                    if case .create = item { Button("Create room") { submit { await store.create(form) } }.disabled(store.busy) }
                    else { Button("Save room") { submit { await store.edit(form) } }.disabled(store.busy) }
                case .start(let machine, let code):
                    Stepper("Cycle duration: \(minutes) min", value: $minutes, in: 1...240)
                    Button("Start \(machine.name)") {
                        submit { await store.action("start", for: machine, in: code, minutes: minutes) }
                    }.disabled(store.busy)
                case .pickup(let machine, let code):
                    Text("Have you removed your clothes from \(machine.name)?")
                    Button("Yes, removed") { submit { await store.action("clear", for: machine, in: code) } }.disabled(store.busy)
                    Button("Remind me in an hour") { submit { await store.action("still-there", for: machine, in: code) } }.disabled(store.busy)
                case .preferences:
                    Toggle("Laundry alerts", isOn: Binding(get: { store.access.notificationsEnabled }, set: togglePush))
                    if let registrationMessage { Text(registrationMessage).foregroundStyle(.secondary) }
                    if let error = push.registrationError { Text(error).foregroundStyle(.secondary) }
                    if push.authorization == .denied {
                        Button("Open iPhone Settings") {
                            if let url = URL(string: UIApplication.openSettingsURLString) { UIApplication.shared.open(url) }
                        }
                    }
                    if store.workerActive == false && store.access.notificationsEnabled {
                        Text("The reminder worker hasn’t checked in recently.").foregroundStyle(.secondary)
                    }
                    Link("Privacy policy", destination: LoopAPI.baseURL.appendingPathComponent("privacy"))
                }
            }
            .navigationTitle(sheetTitle(item))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Done") { sheet = nil } } }
            .alert("Loop Laundry", isPresented: Binding(get: { store.error != nil }, set: { if !$0 { store.error = nil } })) {
                Button("OK") { store.error = nil }
            } message: { Text(store.error ?? "") }
        }
    }

    private func sheetTitle(_ item: Sheet) -> String {
        switch item {
        case .join: return "Join a room"
        case .create: return "Create a room"
        case .edit: return "Edit room"
        case .preferences: return "Notifications"
        case .start: return "Start a load"
        case .pickup: return "Laundry is ready"
        }
    }

    private func showJoin(_ code: String = "") { entry = code; sheet = .join }
    private func showCreate() { form = RoomSettings(); sheet = .create }
    private func act(_ name: String, _ machine: Machine, _ code: String) {
        Task { _ = await store.action(name, for: machine, in: code) }
    }

    private func submit(_ work: @escaping @MainActor () async -> Bool) {
        Task { if await work() { sheet = nil } }
    }

    private func preparePush() async {
        await push.prepare(enabled: store.access.notificationsEnabled)
        if let token = push.token { await register(token) }
    }

    private func register(_ token: String) async {
        guard store.access.notificationsEnabled else { return }
        do {
            try await store.registerPush(token, sandbox: PushBridge.sandbox)
            if !store.access.notificationsEnabled { try await store.unregisterPush(); return }
            registrationMessage = "This iPhone is registered for pickup and queue alerts."
        } catch { registrationMessage = error.localizedDescription }
    }

    private func togglePush(_ enabled: Bool) {
        store.setNotifications(enabled)
        push.enabled = store.access.notificationsEnabled
        Task {
            if store.access.notificationsEnabled { await preparePush() }
            else {
                do {
                    try await store.unregisterPush()
                    registrationMessage = "Laundry alerts are off on this iPhone."
                } catch { registrationMessage = "Loop couldn’t update the server. Turn off alerts in iPhone Settings or try again." }
            }
        }
    }

    private func openNotification(_ route: PushRoute) async {
        push.route = nil
        guard store.access.rooms.contains(where: { $0.code == route.room }) else { return }
        store.switchRoom(route.room)
        _ = await store.refresh()
        if let machine = store.machines.first(where: { $0.id == route.machine && $0.end == route.end && $0.mine && $0.state == "done" }) {
            sheet = .pickup(machine, route.room)
        }
    }
}
