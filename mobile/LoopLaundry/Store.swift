import Foundation
import Combine
import Security

struct Room: Codable, Identifiable {
    var id: String { code }
    let code: String
    var name: String
    let token: String
}

struct RoomSettings: Codable {
    var name = ""
    var washers = 2
    var dryers = 2
}

struct Machine: Codable, Identifiable {
    let id: Int
    let name: String
    let type: String
    let state: String
    let mine: Bool
    let end: Double?
    let queueLength: Int
    let queuePosition: Int?
    let turnDeadline: Double?

    var canStart: Bool { state == "free" && (queueLength == 0 || queuePosition == 1) }
    var status: String {
        if let deadline = turnDeadline {
            return "Your turn · \(max(1, Int(ceil((deadline - Date().timeIntervalSince1970) / 60)))) min left"
        }
        switch state {
        case "busy":
            let minutes = max(1, Int(ceil(((end ?? 0) - Date().timeIntervalSince1970) / 60)))
            return "\(mine ? "Your cycle" : "In use") · \(minutes) min left"
        case "done": return "Waiting for pickup"
        default: return queueLength == 0 ? "Available" : "Reserved"
        }
    }
}

private struct RoomState: Decodable {
    struct Details: Decodable { let name: String }
    let room: Details
    let machines: [Machine]
    let apnsActive: Bool?
}

private struct Token: Decodable { let token: String }
private struct Success: Decodable { let ok: Bool }
private struct APIError: Decodable { let detail: String }
private struct Minutes: Encodable { let minutes: Int }
private struct APNSDevice: Encodable { let token: String; let sandbox: Bool }

enum LoopError: LocalizedError {
    case message(String)
    case keychain
    var errorDescription: String? {
        switch self {
        case .message(let text): return text
        case .keychain: return "Loop couldn’t save room access on this phone. Try again."
        }
    }
}

struct SavedAccess: Codable {
    var deviceID = UUID().uuidString
    var rooms: [Room] = []
    var activeCode: String?
    var notificationsEnabled = true
}

enum RoomCode {
    static func read(_ input: String) -> String? {
        let text = input.trimmingCharacters(in: .whitespacesAndNewlines)
        let parsed = URLComponents(string: text)
        let code: String
        if parsed?.scheme != nil {
            let expectedHost = LoopAPI.baseURL.host
            guard (parsed?.scheme == "https" && parsed?.host == expectedHost) ||
                    (parsed?.scheme == "looplaundry" && parsed?.host == "join"),
                  let query = parsed?.queryItems?.first(where: { $0.name == "room" })?.value else { return nil }
            code = query.uppercased()
        } else {
            code = text.uppercased()
        }
        return code.range(of: "^[A-Z0-9]{1,32}$", options: .regularExpression) == nil ? nil : code
    }
}

enum KeychainStore {
    private static var query: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: Bundle.main.bundleIdentifier ?? "com.samruddhinavale.looplaundry",
         kSecAttrAccount as String: "saved-rooms"]
    }

    static func load() throws -> SavedAccess {
        var lookup = query
        lookup[kSecReturnData as String] = true
        lookup[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(lookup as CFDictionary, &result)
        if status == errSecItemNotFound { return SavedAccess() }
        guard status == errSecSuccess, let data = result as? Data else { throw LoopError.keychain }
        return try JSONDecoder().decode(SavedAccess.self, from: data)
    }

    static func save(_ access: SavedAccess) throws {
        let data = try JSONEncoder().encode(access)
        let update = [kSecValueData as String: data]
        let status = SecItemUpdate(query as CFDictionary, update as CFDictionary)
        if status == errSecSuccess { return }
        guard status == errSecItemNotFound else { throw LoopError.keychain }
        var item = query
        item[kSecValueData as String] = data
        item[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        guard SecItemAdd(item as CFDictionary, nil) == errSecSuccess else { throw LoopError.keychain }
    }
}

struct LoopAPI {
    static let baseURL = URL(string: "https://loop-laundry.vercel.app")!

    func request<T: Decodable>(_ path: String, method: String = "GET", device: String,
                               room: Room? = nil, code: String? = nil, body: Data? = nil,
                               headers: [String: String] = [:]) async throws -> T {
        var request = URLRequest(url: Self.baseURL.appendingPathComponent(path), cachePolicy: .reloadIgnoringLocalCacheData,
                                 timeoutInterval: 15)
        request.httpMethod = method
        request.setValue(device, forHTTPHeaderField: "X-Client-Id")
        request.setValue(code ?? room?.code, forHTTPHeaderField: "X-Room-Code")
        request.setValue(room?.token, forHTTPHeaderField: "X-Room-Token")
        if let body {
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        for (key, value) in headers { request.setValue(value, forHTTPHeaderField: key) }
        let (data, result) = try await URLSession.shared.data(for: request)
        guard let response = result as? HTTPURLResponse else { throw LoopError.message("Loop didn’t receive a response.") }
        guard (200..<300).contains(response.statusCode) else {
            if response.statusCode == 403 {
                throw LoopError.message("Room access is temporarily unavailable. Your saved room is still on this phone.")
            }
            let detail = try? JSONDecoder().decode(APIError.self, from: data).detail
            throw LoopError.message(detail ?? "Loop couldn’t complete this request. Try again.")
        }
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(T.self, from: data)
    }
}

@MainActor final class LoopStore: ObservableObject {
    @Published private(set) var access = SavedAccess()
    @Published private(set) var machines: [Machine] = []
    @Published private(set) var workerActive: Bool?
    @Published private(set) var loadedRoom = false
    @Published private(set) var busy = false
    @Published var error: String?
    private let api = LoopAPI()
    private var storageReady = false

    init() { loadAccess() }

    func loadAccess() {
        guard !storageReady else { return }
        do {
            access = try KeychainStore.load()
            storageReady = true
        } catch { self.error = error.localizedDescription }
    }

    var room: Room? { access.rooms.first(where: { $0.code == access.activeCode }) }
    var settings: RoomSettings {
        RoomSettings(name: room?.name ?? "", washers: machines.filter { $0.type == "washer" }.count,
                     dryers: machines.filter { $0.type == "dryer" }.count)
    }

    private func updateAccess(_ update: (inout SavedAccess) -> Void) throws {
        guard storageReady else { throw LoopError.keychain }
        var next = access
        update(&next)
        try KeychainStore.save(next)
        access = next
    }

    func switchRoom(_ code: String) {
        guard code != room?.code, access.rooms.contains(where: { $0.code == code }) else { return }
        do {
            try updateAccess { $0.activeCode = code }
            machines = []
            workerActive = nil
            loadedRoom = false
        } catch { self.error = error.localizedDescription }
    }

    @discardableResult
    func refresh(showFailure: Bool = false) async -> Bool {
        guard let room else { return false }
        do {
            let state: RoomState = try await api.request("api/state", device: access.deviceID, room: room)
            guard self.room?.code == room.code else { return false }
            machines = state.machines
            workerActive = state.apnsActive
            loadedRoom = true
            if state.room.name != room.name {
                try updateAccess { access in
                    if let i = access.rooms.firstIndex(where: { $0.code == room.code }) {
                        access.rooms[i].name = state.room.name
                    }
                }
            }
            return true
        } catch {
            if showFailure && self.room?.code == room.code { self.error = error.localizedDescription }
            return false
        }
    }

    func join(_ input: String) async -> Bool {
        guard let code = RoomCode.read(input) else {
            error = "Enter a room code or paste a Loop invite link."
            return false
        }
        if access.rooms.contains(where: { $0.code == code }) {
            switchRoom(code)
            return true
        }
        return await perform {
            let token: Token = try await self.api.request("api/room/membership", method: "POST",
                                                          device: self.access.deviceID, code: code)
            let newRoom = Room(code: code, name: code, token: token.token)
            try self.save(newRoom)
        }
    }

    func create(_ input: RoomSettings) async -> Bool {
        await perform {
            let room: Room = try await self.api.request("api/rooms", method: "POST", device: self.access.deviceID,
                                                         body: JSONEncoder().encode(input))
            try self.save(room)
        }
    }

    private func save(_ room: Room) throws {
        try updateAccess {
            $0.rooms.append(room)
            $0.activeCode = room.code
        }
        machines = []
        workerActive = nil
        loadedRoom = false
    }

    func edit(_ input: RoomSettings) async -> Bool {
        guard let room else { return false }
        return await perform {
            let _: Success = try await self.api.request("api/room", method: "PUT", device: self.access.deviceID,
                                                         room: room, body: JSONEncoder().encode(input))
            await self.refresh(showFailure: true)
        }
    }

    func leave() async {
        guard let room else { return }
        _ = await perform {
            let _: Success = try await self.api.request("api/room/membership", method: "DELETE",
                                                         device: self.access.deviceID, room: room)
            try self.updateAccess {
                $0.rooms.removeAll { $0.code == room.code }
                $0.activeCode = $0.rooms.first?.code
            }
            self.machines = []
            self.workerActive = nil
            self.loadedRoom = false
        }
    }

    func action(_ name: String, for machine: Machine, in code: String, minutes: Int? = nil) async -> Bool {
        guard let room, room.code == code else { return false }
        return await perform {
            var headers: [String: String] = [:]
            if ["finish", "clear", "force-stop", "still-there"].contains(name), let end = machine.end {
                headers["X-Cycle-End"] = String(end)
            }
            if name == "queue" {
                headers["X-Queue-Intent"] = machine.queuePosition == nil ? "join" : "leave"
            }
            let _: Success = try await self.api.request("api/machines/\(machine.id)/\(name)", method: "POST",
                                                         device: self.access.deviceID, room: room,
                                                         body: try minutes.map { try JSONEncoder().encode(Minutes(minutes: $0)) },
                                                         headers: headers)
            await self.refresh(showFailure: true)
        }
    }

    func setNotifications(_ enabled: Bool) {
        do { try updateAccess { $0.notificationsEnabled = enabled } }
        catch { self.error = error.localizedDescription }
    }

    func registerPush(_ token: String, sandbox: Bool) async throws {
        guard access.notificationsEnabled, let room else { return }
        let _: Success = try await api.request("api/push/ios", method: "PUT", device: access.deviceID,
                                                room: room, body: JSONEncoder().encode(APNSDevice(token: token, sandbox: sandbox)))
    }

    func unregisterPush() async throws {
        guard let room else { return }
        let _: Success = try await api.request("api/push/device", method: "DELETE", device: access.deviceID, room: room)
    }

    private func perform(_ operation: () async throws -> Void) async -> Bool {
        guard !busy, storageReady else { return false }
        busy = true
        defer { busy = false }
        do {
            try await operation()
            return true
        } catch {
            self.error = error.localizedDescription
            return false
        }
    }
}
