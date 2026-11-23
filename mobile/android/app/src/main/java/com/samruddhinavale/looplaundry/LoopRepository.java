package com.samruddhinavale.looplaundry;

import android.content.Context;
import android.content.SharedPreferences;
import android.net.Uri;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import org.json.JSONArray;
import org.json.JSONObject;

import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.util.Locale;
import java.util.UUID;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

final class LoopRepository {
    static final String BASE = "https://loop-laundry.vercel.app";
    static final String HOST = "loop-laundry.vercel.app";
    private static final String ALIAS = "loop-room-access";
    final SharedPreferences preferences;
    private volatile JSONObject access;

    LoopRepository(Context context) throws Exception {
        preferences = context.getSharedPreferences("loop", Context.MODE_PRIVATE);
        String stored = preferences.getString("access", null);
        if (stored == null) {
            access = new JSONObject().put("device", UUID.randomUUID().toString()).put("rooms", new JSONArray());
            save(access);
        } else {
            byte[] bytes = Base64.decode(stored, Base64.NO_WRAP);
            if (bytes.length <= 12) throw new IOException("Saved room data is unavailable.");
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.DECRYPT_MODE, key(), new GCMParameterSpec(128, bytes, 0, 12));
            access = new JSONObject(new String(cipher.doFinal(bytes, 12, bytes.length - 12), StandardCharsets.UTF_8));
        }
    }

    private SecretKey key() throws Exception {
        KeyStore store = KeyStore.getInstance("AndroidKeyStore");
        store.load(null);
        if (store.getKey(ALIAS, null) instanceof SecretKey existing) return existing;
        KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
        generator.init(new KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build());
        return generator.generateKey();
    }

    private void save(JSONObject next) throws Exception {
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.ENCRYPT_MODE, key());
        byte[] encrypted = cipher.doFinal(next.toString().getBytes(StandardCharsets.UTF_8));
        byte[] iv = cipher.getIV();
        if (iv.length != 12) throw new IOException("Loop couldn't save room access.");
        byte[] record = new byte[iv.length + encrypted.length];
        System.arraycopy(iv, 0, record, 0, iv.length);
        System.arraycopy(encrypted, 0, record, iv.length, encrypted.length);
        if (!preferences.edit().putString("access", Base64.encodeToString(record, Base64.NO_WRAP)).commit()) {
            throw new IOException("Loop couldn't save room access.");
        }
        access = next;
    }

    static String codeFrom(String input) {
        String code = input.trim();
        Uri uri = Uri.parse(code);
        if (uri.getScheme() != null) {
            boolean valid = ("https".equals(uri.getScheme()) && HOST.equals(uri.getHost())) ||
                    ("looplaundry".equals(uri.getScheme()) && "join".equals(uri.getHost()));
            if (!valid || !uri.isHierarchical()) return null;
            code = uri.getQueryParameter("room");
            if (code == null) return null;
        }
        code = code.toUpperCase(Locale.ROOT);
        return code.matches("[A-Z0-9]{1,32}") ? code : null;
    }

    JSONArray rooms() { return access.optJSONArray("rooms"); }
    JSONObject find(String code) {
        JSONArray rooms = rooms();
        for (int i = 0; i < rooms.length(); i++) {
            JSONObject room = rooms.optJSONObject(i);
            if (code.equals(room.optString("code"))) return room;
        }
        return null;
    }
    JSONObject room() { return find(access.optString("active")); }
    boolean alertsEnabled() { return preferences.getBoolean("alerts_enabled", true); }
    void setAlertsEnabled(boolean enabled) { preferences.edit().putBoolean("alerts_enabled", enabled).apply(); }

    void select(String code) throws Exception {
        if (find(code) != null && !code.equals(access.optString("active"))) {
            save(new JSONObject(access.toString()).put("active", code));
        }
    }

    void join(String input) throws Exception {
        String code = codeFrom(input);
        if (code == null) throw new IOException("Enter a room code or paste a Loop invite link.");
        if (find(code) != null) {
            select(code);
            return;
        }
        JSONObject token = request("/api/room/membership", "POST", null, code, null, null, null);
        saveRoom(new JSONObject().put("code", code).put("name", code).put("token", token.getString("token")));
    }

    void create(JSONObject settings) throws Exception {
        saveRoom(request("/api/rooms", "POST", null, null, settings, null, null));
    }

    private void saveRoom(JSONObject room) throws Exception {
        JSONObject next = new JSONObject(access.toString()).put("active", room.getString("code"));
        next.getJSONArray("rooms").put(room);
        save(next);
    }

    JSONObject state() throws Exception {
        JSONObject room = room();
        if (room == null) return new JSONObject();
        JSONObject state = request("/api/state", "GET", room, null, null, null, null);
        String name = state.getJSONObject("room").getString("name");
        if (!name.equals(room.optString("name"))) {
            JSONObject next = new JSONObject(access.toString());
            JSONArray rooms = next.getJSONArray("rooms");
            for (int i = 0; i < rooms.length(); i++) {
                if (room.getString("code").equals(rooms.getJSONObject(i).getString("code"))) {
                    rooms.getJSONObject(i).put("name", name);
                    break;
                }
            }
            save(next);
        }
        return state;
    }

    void edit(JSONObject settings) throws Exception {
        request("/api/room", "PUT", requireRoom(), null, settings, null, null);
    }

    void leave() throws Exception {
        JSONObject room = requireRoom();
        String code = room.getString("code");
        request("/api/room/membership", "DELETE", room, null, null, null, null);
        JSONObject next = new JSONObject(access.toString());
        JSONArray rooms = next.getJSONArray("rooms");
        for (int i = rooms.length() - 1; i >= 0; i--) {
            if (code.equals(rooms.getJSONObject(i).getString("code"))) rooms.remove(i);
        }
        next.put("active", rooms.length() == 0 ? "" : rooms.getJSONObject(0).getString("code"));
        save(next);
    }

    void action(String code, JSONObject machine, String action, int minutes) throws Exception {
        JSONObject room = requireRoom();
        if (!code.equals(room.getString("code"))) throw new IOException("This room changed. Try again.");
        String end = null;
        String intent = null;
        if (action.equals("queue")) intent = machine.isNull("queue_position") ? "join" : "leave";
        if ((action.equals("finish") || action.equals("clear") || action.equals("force-stop") || action.equals("still-there"))
                && !machine.isNull("end")) end = machine.getString("end");
        JSONObject body = action.equals("start") ? new JSONObject().put("minutes", minutes) : null;
        request("/api/machines/" + machine.getInt("id") + "/" + action, "POST", room, null, body, end, intent);
    }

    void registerPush(String token) throws Exception {
        if (room() != null && alertsEnabled()) {
            request("/api/push/android", "PUT", room(), null, new JSONObject().put("token", token), null, null);
        }
    }

    void unregisterPush() throws Exception {
        if (room() != null) request("/api/push/device", "DELETE", room(), null, null, null, null);
    }

    private JSONObject requireRoom() throws IOException {
        JSONObject room = room();
        if (room == null) throw new IOException("Open a room first.");
        return room;
    }

    private JSONObject request(String path, String method, JSONObject room, String code, JSONObject body,
                               String cycleEnd, String queueIntent) throws Exception {
        HttpURLConnection connection = (HttpURLConnection) new URL(BASE + path).openConnection();
        try {
            connection.setConnectTimeout(8_000);
            connection.setReadTimeout(12_000);
            connection.setInstanceFollowRedirects(false);
            connection.setRequestMethod(method);
            connection.setRequestProperty("X-Client-Id", access.getString("device"));
            if (room != null) {
                connection.setRequestProperty("X-Room-Code", room.getString("code"));
                connection.setRequestProperty("X-Room-Token", room.getString("token"));
            } else if (code != null) connection.setRequestProperty("X-Room-Code", code);
            if (cycleEnd != null) connection.setRequestProperty("X-Cycle-End", cycleEnd);
            if (queueIntent != null) connection.setRequestProperty("X-Queue-Intent", queueIntent);
            if (body != null) {
                connection.setDoOutput(true);
                connection.setRequestProperty("Content-Type", "application/json");
                try (var stream = connection.getOutputStream()) {
                    stream.write(body.toString().getBytes(StandardCharsets.UTF_8));
                }
            }
            int status = connection.getResponseCode();
            try (InputStream stream = status < 400 ? connection.getInputStream() : connection.getErrorStream()) {
                var buffer = new java.io.ByteArrayOutputStream();
                if (stream != null) {
                    byte[] chunk = new byte[4096];
                    int read;
                    while ((read = stream.read(chunk)) != -1) buffer.write(chunk, 0, read);
                }
                JSONObject data;
                try { data = new JSONObject(new String(buffer.toByteArray(), StandardCharsets.UTF_8)); }
                catch (org.json.JSONException e) { throw new IOException("Loop did not receive a valid response."); }
                if (status < 200 || status >= 300) {
                    throw new IOException(status == 403 ? "Room access is temporarily unavailable. Your saved room is still on this phone."
                            : data.optString("detail", "Loop couldn’t complete this request."));
                }
                return data;
            }
        } finally {
            connection.disconnect();
        }
    }
}
