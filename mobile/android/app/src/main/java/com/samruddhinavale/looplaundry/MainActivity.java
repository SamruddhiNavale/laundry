package com.samruddhinavale.looplaundry;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.app.NotificationManager;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.text.InputType;
import android.view.View;
import android.view.WindowInsets;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.Switch;
import android.widget.TextView;
import android.widget.Toast;
import com.google.firebase.FirebaseApp;
import com.google.firebase.messaging.FirebaseMessaging;
import org.json.JSONArray;
import org.json.JSONObject;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class MainActivity extends Activity {
    private static final int INK = 0xff23364a, BLUE = 0xff365e7c, PAPER = 0xfff4f8fb;
    private static final int NOTIFICATIONS = 31;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final ExecutorService network = Executors.newSingleThreadExecutor();
    private LoopRepository repository;
    private LinearLayout content;
    private ScrollView scroll;
    private JSONObject state = new JSONObject();
    private String filter = "all";
    private boolean busy;
    private boolean visible;
    private String pushStatus = "";
    private Intent pendingLink;
    private final Runnable poll = new Runnable() {
        @Override public void run() {
            if (!visible || isFinishing()) return;
            if (!busy && repository.room() != null) refresh(false);
            handler.postDelayed(this, 5000);
        }
    };
    private interface Operation { void run() throws Exception; }

    @Override protected void onCreate(Bundle state) {
        super.onCreate(state);
        try { repository = new LoopRepository(this); }
        catch (Exception error) {
            TextView problem = new TextView(this);
            problem.setText("Loop couldn’t read saved rooms. Unlock your phone and reopen Loop.");
            setContentView(problem);
            return;
        }
        LoopMessagingService.channel(this);
        scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        scroll.setBackgroundColor(PAPER);
        scroll.setOnApplyWindowInsetsListener((view, insets) -> {
            if (Build.VERSION.SDK_INT >= 30) {
                var edges = insets.getInsets(WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout());
                view.setPadding(edges.left, edges.top, edges.right, edges.bottom);
            }
            return insets;
        });
        content = new LinearLayout(this);
        content.setPadding(dp(22), dp(22), dp(22), dp(30));
        content.setOrientation(LinearLayout.VERTICAL);
        scroll.addView(content);
        setContentView(scroll);
        if (Build.VERSION.SDK_INT < 30) scroll.setFitsSystemWindows(true);
        render();
        open(getIntent());
    }

    @Override protected void onNewIntent(Intent intent) { super.onNewIntent(intent); setIntent(intent); open(intent); }
    @Override protected void onResume() {
        super.onResume();
        visible = true;
        if (repository != null) {
            handler.removeCallbacks(poll);
            handler.post(poll);
            configurePush(true);
        }
    }
    @Override protected void onPause() { visible = false; handler.removeCallbacks(poll); super.onPause(); }
    @Override protected void onDestroy() { network.shutdownNow(); super.onDestroy(); }

    private int dp(int units) { return Math.round(units * getResources().getDisplayMetrics().density); }
    private TextView text(String value, int size, boolean bold) {
        TextView label = new TextView(this);
        label.setText(value); label.setTextSize(size); label.setTextColor(INK);
        if (bold) label.setTypeface(null, Typeface.BOLD);
        label.setPadding(0, dp(5), 0, dp(5));
        return label;
    }

    private void button(LinearLayout parent, String label, Runnable action) {
        Button button = new Button(this);
        button.setText(label); button.setAllCaps(false); button.setTextColor(BLUE);
        button.setMinHeight(dp(48)); button.setOnClickListener(view -> action.run());
        parent.addView(button);
    }

    private void render() {
        if (content == null || isDestroyed()) return;
        content.removeAllViews();
        JSONObject room = repository.room();
        content.addView(text("Loop Laundry", 26, true));
        if (room == null) {
            TextView title = text("A little more flow\nto your day.", 34, false);
            title.setTypeface(Typeface.SERIF);
            content.addView(title);
            content.addView(text("Check shared machines, save your queue spot, and know when it’s time to head down.", 16, false));
            button(content, "Join a room", () -> join(""));
            button(content, "Create a room", () -> roomForm(false));
            button(content, "Notification settings", this::notificationSettings);
            return;
        }
        String selected = room.optString("code");
        content.addView(text(room.optString("name"), 24, true));
        button(content, "My rooms", this::rooms);
        button(content, "Notification settings", this::notificationSettings);
        LinearLayout tabs = new LinearLayout(this);
        button(tabs, "All", () -> setFilter("all"));
        button(tabs, "Washers", () -> setFilter("washer"));
        button(tabs, "Dryers", () -> setFilter("dryer"));
        content.addView(tabs);
        JSONArray machines = state.optJSONArray("machines");
        if (machines == null || machines.length() == 0) {
            content.addView(text(machines == null ? "Loading machines…" : "This room has no machines yet.", 16, false));
            if (machines == null) button(content, "Refresh room", () -> refresh(true));
        } else {
            for (int i = 0; i < machines.length(); i++) {
                JSONObject machine = machines.optJSONObject(i);
                if (machine != null && (filter.equals("all") || filter.equals(machine.optString("type")))) machineRow(machine, selected);
            }
        }
        button(content, "Invite residents", () -> share(room));
        if (machines != null) button(content, "Edit room", () -> roomForm(true));
    }

    private void setFilter(String value) { filter = value; render(); }

    private void machineRow(JSONObject machine, String code) {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(14), dp(12), dp(14), dp(12));
        GradientDrawable background = new GradientDrawable();
        background.setColor(Color.WHITE); background.setCornerRadius(dp(12));
        background.setStroke(dp(1), 0xffdceaf3); card.setBackground(background);
        var space = new LinearLayout.LayoutParams(-1, -2);
        space.setMargins(0, dp(4), 0, dp(10));
        content.addView(card, space);
        String status = machine.optString("state");
        boolean mine = machine.optBoolean("mine");
        int queue = machine.optInt("queue_length");
        int position = machine.optInt("queue_position", 0);
        boolean canStart = status.equals("free") && (queue == 0 || position == 1);
        String name = machine.optString("name");
        card.addView(text(name, 18, true));
        if (!machine.isNull("turn_deadline")) {
            long left = Math.max(1, (long)Math.ceil((machine.optDouble("turn_deadline") - System.currentTimeMillis() / 1000.0) / 60));
            card.addView(text("Your turn · " + left + " min left", 15, false));
        } else if (status.equals("busy")) {
            long left = Math.max(1, (long)Math.ceil((machine.optDouble("end") - System.currentTimeMillis() / 1000.0) / 60));
            card.addView(text((mine ? "Your cycle" : "In use") + " · " + left + " min left", 15, false));
        } else card.addView(text(status.equals("done") ? "Waiting for pickup" : queue == 0 ? "Available" : "Reserved", 15, false));
        if (position > 1 || (position == 1 && !status.equals("free"))) card.addView(text("Queue position " + position, 14, false));
        if (canStart) button(card, "Start a load", () -> cycle(code, machine));
        if (mine && status.equals("busy")) button(card, "End cycle", () -> act(code, machine, "finish", 0));
        if (mine && status.equals("done")) button(card, "Clothes picked up", () -> act(code, machine, "clear", 0));
        if (!mine && (!canStart || position > 0)) {
            button(card, position == 0 ? "Join queue" : "Leave queue", () -> act(code, machine, "queue", 0));
        }
        if (!mine && status.equals("done")) button(card, "Force stop", () -> new AlertDialog.Builder(this)
                .setTitle("Release finished cycle?")
                .setMessage("Check that the drum is empty. The queue will be kept.")
                .setNegativeButton("Cancel", null)
                .setPositiveButton("Force stop", (dialog, which) -> act(code, machine, "force-stop", 0)).show());
    }

    private void cycle(String code, JSONObject machine) {
        var input = input("Minutes", machine.optString("type").equals("washer") ? "35" : "45", true);
        new AlertDialog.Builder(this).setTitle("Cycle duration").setView(input).setNegativeButton("Cancel", null)
                .setPositiveButton("Start", (dialog, which) -> {
                    try {
                        int minutes = Integer.parseInt(input.getText().toString());
                        if (minutes < 1 || minutes > 240) throw new NumberFormatException();
                        act(code, machine, "start", minutes);
                    } catch (NumberFormatException error) { report("Enter a duration from 1 to 240 minutes."); }
                }).show();
    }

    private void act(String code, JSONObject machine, String action, int minutes) {
        submit(() -> repository.action(code, machine, action, minutes), false);
    }

    private void rooms() {
        JSONArray joined = repository.rooms();
        String[] choices = new String[joined.length() + 3];
        for (int i = 0; i < joined.length(); i++) choices[i] = joined.optJSONObject(i).optString("name");
        choices[joined.length()] = "Join another room";
        choices[joined.length() + 1] = "Create a room";
        choices[joined.length() + 2] = "Leave current room";
        new AlertDialog.Builder(this).setTitle("My rooms").setItems(choices, (dialog, selected) -> {
            if (selected < joined.length()) {
                String code = joined.optJSONObject(selected).optString("code");
                submit(() -> repository.select(code), true);
            } else if (selected == joined.length()) join("");
            else if (selected == joined.length() + 1) roomForm(false);
            else new AlertDialog.Builder(this).setTitle("Leave current room?")
                    .setMessage("Loop will release your spots in this room’s queues.")
                    .setNegativeButton("Cancel", null)
                    .setPositiveButton("Leave", (confirm, which) -> submit(repository::leave, true)).show();
        }).show();
    }

    private EditText input(String hint, String value, boolean number) {
        EditText entry = new EditText(this);
        entry.setHint(hint); entry.setText(value);
        entry.setInputType(number ? InputType.TYPE_CLASS_NUMBER : InputType.TYPE_CLASS_TEXT);
        entry.setSingleLine(true);
        return entry;
    }

    private void join(String prefill) {
        EditText code = input("Room code or invite link", prefill, false);
        new AlertDialog.Builder(this).setTitle("Join a room").setView(code).setNegativeButton("Cancel", null)
                .setPositiveButton("Join", (dialog, which) -> {
                    String value = code.getText().toString();
                    submit(() -> repository.join(value), true);
                }).show();
    }

    private void roomForm(boolean edit) {
        var group = new LinearLayout(this);
        group.setPadding(dp(20), 0, dp(20), 0);
        group.setOrientation(LinearLayout.VERTICAL);
        int washers = 0, dryers = 0;
        JSONArray machines = state.optJSONArray("machines");
        if (edit && machines != null) for (int i = 0; i < machines.length(); i++) {
            if ("washer".equals(machines.optJSONObject(i).optString("type"))) washers++; else dryers++;
        }
        var name = input("Room name", edit ? repository.room().optString("name") : "", false);
        var wash = input("Washers", edit ? Integer.toString(washers) : "2", true);
        var dry = input("Dryers", edit ? Integer.toString(dryers) : "2", true);
        group.addView(name); group.addView(text("Washers", 14, false)); group.addView(wash);
        group.addView(text("Dryers", 14, false)); group.addView(dry);
        new AlertDialog.Builder(this).setTitle(edit ? "Edit room" : "Create a room").setView(group)
                .setNegativeButton("Cancel", null).setPositiveButton("Save", (dialog, which) -> {
                    String roomName = name.getText().toString().trim();
                    try {
                        int w = Integer.parseInt(wash.getText().toString()), d = Integer.parseInt(dry.getText().toString());
                        if (roomName.isEmpty() || w < 0 || w > 50 || d < 0 || d > 50) throw new NumberFormatException();
                        submit(() -> {
                            JSONObject form = new JSONObject().put("name", roomName).put("washers", w).put("dryers", d);
                            if (edit) repository.edit(form); else repository.create(form);
                        }, true);
                    } catch (NumberFormatException error) { report("Enter a room name and machine counts from 0 to 50."); }
                }).show();
    }

    private void share(JSONObject room) {
        String code = room.optString("code");
        Intent share = new Intent(Intent.ACTION_SEND).setType("text/plain")
                .putExtra(Intent.EXTRA_TEXT, "Join " + room.optString("name") + " on Loop Laundry. Room code: " + code + "\n" + LoopRepository.BASE + "/?room=" + code);
        startActivity(Intent.createChooser(share, "Invite residents"));
    }

    private void notificationSettings() {
        var panel = new LinearLayout(this);
        panel.setOrientation(LinearLayout.VERTICAL); panel.setPadding(dp(20), 0, dp(20), 0);
        Switch toggle = new Switch(this);
        toggle.setText("Laundry alerts"); toggle.setChecked(repository.alertsEnabled());
        toggle.setOnCheckedChangeListener((view, enabled) -> {
            repository.setAlertsEnabled(enabled);
            if (enabled) configurePush(true);
            else {
                if (!FirebaseApp.getApps(this).isEmpty()) FirebaseMessaging.getInstance().setAutoInitEnabled(false);
                network.submit(() -> {
                    try { repository.unregisterPush(); }
                    catch (Exception error) { runOnUiThread(() -> report("Alerts are off in Loop. The server setting couldn’t be updated.")); }
                });
            }
        });
        panel.addView(toggle);
        if (!pushStatus.isEmpty()) panel.addView(text(pushStatus, 14, false));
        if (repository.alertsEnabled() && state.has("fcm_active") && !state.optBoolean("fcm_active")) {
            panel.addView(text("The Android reminder worker hasn’t checked in recently.", 14, false));
        }
        button(panel, "Open Android notification settings", () -> startActivity(new Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS)
                .putExtra(Settings.EXTRA_APP_PACKAGE, getPackageName())));
        button(panel, "Privacy policy", () -> startActivity(new Intent(Intent.ACTION_VIEW,
                Uri.parse(LoopRepository.BASE + "/privacy"))));
        new AlertDialog.Builder(this).setTitle("Notifications").setView(panel).setPositiveButton("Done", null).show();
    }

    private void configurePush(boolean prompt) {
        if (!repository.alertsEnabled() || repository.room() == null) return;
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            if (prompt && !repository.preferences.getBoolean("permission_requested", false)) {
                repository.preferences.edit().putBoolean("permission_requested", true).apply();
                requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, NOTIFICATIONS);
            }
            return;
        }
        if (!getSystemService(NotificationManager.class).areNotificationsEnabled()) return;
        if (FirebaseApp.getApps(this).isEmpty()) {
            pushStatus = "This build needs its Firebase configuration before it can receive alerts.";
            return;
        }
        FirebaseMessaging messaging = FirebaseMessaging.getInstance();
        messaging.setAutoInitEnabled(true);
        messaging.getToken().addOnSuccessListener(token -> {
            if (isDestroyed() || network.isShutdown()) return;
            network.submit(() -> {
                try {
                    repository.registerPush(token);
                    runOnUiThread(() -> pushStatus = "This phone is registered for pickup and queue alerts.");
                } catch (Exception error) {
                    runOnUiThread(() -> pushStatus = "Loop couldn’t register this phone for alerts. Reopen the app to retry.");
                }
            });
        }).addOnFailureListener(error -> pushStatus = "Firebase couldn’t issue a device token. Reopen Loop to retry.");
    }

    @Override public void onRequestPermissionsResult(int code, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(code, permissions, results);
        if (code == NOTIFICATIONS) configurePush(false);
    }

    private void open(Intent intent) {
        if (repository == null || intent == null) return;
        if (busy && (intent.getData() != null || intent.hasExtra("room"))) {
            pendingLink = intent;
            return;
        }
        if (intent.getData() != null) {
            String code = LoopRepository.codeFrom(intent.getData().toString());
            if (code != null) {
                if (repository.find(code) == null) join(code);
                else submit(() -> repository.select(code), true);
            }
        } else if (intent.hasExtra("room")) {
            String code = intent.getStringExtra("room");
            if (code == null || repository.find(code) == null) return;
            String machine = intent.getStringExtra("machine");
            String end = intent.getStringExtra("end");
            submit(() -> repository.select(code), true, () -> openPickup(code, machine, end));
        }
    }

    private void openPickup(String code, String id, String end) {
        JSONArray machines = state.optJSONArray("machines");
        if (id == null || end == null || machines == null) return;
        double expectedEnd;
        try { expectedEnd = Double.parseDouble(end); }
        catch (NumberFormatException error) { return; }
        for (int i = 0; i < machines.length(); i++) {
            JSONObject machine = machines.optJSONObject(i);
            if (id.equals(machine.optString("id")) && machine.optBoolean("mine") && "done".equals(machine.optString("state"))
                    && expectedEnd == machine.optDouble("end")) {
                new AlertDialog.Builder(this).setTitle("Laundry is ready")
                        .setMessage("Have you removed your clothes from " + machine.optString("name") + "?")
                        .setNegativeButton("Remind me in an hour", (dialog, which) -> act(code, machine, "still-there", 0))
                        .setPositiveButton("Yes, removed", (dialog, which) -> act(code, machine, "clear", 0)).show();
                return;
            }
        }
    }

    private void refresh(boolean reportErrors) {
        if (busy) return;
        busy = true;
        network.submit(() -> {
            try {
                JSONObject result = repository.state();
                runOnUiThread(() -> { busy = false; state = result; render(); openPendingLink(); });
            } catch (Exception error) {
                runOnUiThread(() -> { busy = false; if (reportErrors) report(error.getMessage()); openPendingLink(); });
            }
        });
    }

    private void submit(Operation operation, boolean roomChanged) { submit(operation, roomChanged, () -> {}); }
    private void submit(Operation operation, boolean roomChanged, Runnable afterwards) {
        if (busy) return;
        busy = true;
        network.submit(() -> {
            try {
                operation.run();
                JSONObject result = repository.state();
                runOnUiThread(() -> {
                    if (isDestroyed()) return;
                    busy = false;
                    state = result;
                    if (roomChanged) configurePush(true);
                    render();
                    afterwards.run();
                    openPendingLink();
                });
            } catch (Exception error) {
                runOnUiThread(() -> { busy = false; report(error.getMessage()); openPendingLink(); });
            }
        });
    }

    private void report(String message) {
        if (!isDestroyed()) Toast.makeText(this, message == null ? "Loop couldn’t finish that request." : message, Toast.LENGTH_LONG).show();
    }

    private void openPendingLink() {
        Intent next = pendingLink;
        pendingLink = null;
        if (!isDestroyed()) open(next);
    }
}
