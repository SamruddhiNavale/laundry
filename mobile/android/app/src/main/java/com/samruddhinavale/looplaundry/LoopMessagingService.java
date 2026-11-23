package com.samruddhinavale.looplaundry;

import android.Manifest;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Build;
import android.util.Log;
import com.google.firebase.messaging.FirebaseMessagingService;
import com.google.firebase.messaging.RemoteMessage;

public final class LoopMessagingService extends FirebaseMessagingService {
    static final String CHANNEL = "laundry-alerts";

    static void channel(Context context) {
        NotificationManager manager = context.getSystemService(NotificationManager.class);
        manager.createNotificationChannel(new NotificationChannel(CHANNEL, "Laundry alerts", NotificationManager.IMPORTANCE_HIGH));
    }

    @Override public void onNewToken(String token) {
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) return;
        try {
            new LoopRepository(this).registerPush(token);
        } catch (Exception error) {
            Log.w("LoopLaundry", "Firebase registration will retry when Loop opens.");
        }
    }

    @Override public void onMessageReceived(RemoteMessage message) {
        if (!getSharedPreferences("loop", MODE_PRIVATE).getBoolean("alerts_enabled", true)) return;
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) return;
        var data = message.getData();
        String room = data.get("room");
        String body = data.get("body");
        String machine = data.get("machine");
        if (room == null || body == null || machine == null) return;
        boolean turn = "turn".equals(data.get("kind"));
        if (turn) {
            try {
                if (Double.parseDouble(data.get("deadline")) <= System.currentTimeMillis() / 1000.0) return;
            } catch (IllegalArgumentException | NullPointerException error) { return; }
        }
        try {
            if (new LoopRepository(this).find(room) == null) return;
        } catch (Exception error) { return; }
        channel(this);
        int id = (room + ':' + (turn ? data.get("turn") : machine)).hashCode();
        Intent intent = new Intent(this, MainActivity.class).setFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP)
                .putExtra("room", room);
        if (!turn) intent.putExtra("machine", machine).putExtra("end", data.get("end"));
        PendingIntent target = PendingIntent.getActivity(this, id, intent, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        Notification notification = new Notification.Builder(this, CHANNEL)
                .setSmallIcon(R.drawable.ic_notification).setContentTitle(turn ? "Your laundry turn" : "Laundry pickup reminder")
                .setContentText(body).setStyle(new Notification.BigTextStyle().bigText(body))
                .setAutoCancel(true).setContentIntent(target).build();
        getSystemService(NotificationManager.class).notify(id, notification);
    }
}
