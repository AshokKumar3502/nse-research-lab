package com.ashok.nselab;

import android.Manifest;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.os.Build;
import androidx.annotation.NonNull;
import androidx.work.Worker;
import androidx.work.WorkerParameters;
import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.HashSet;
import java.util.Set;
import org.json.JSONArray;
import org.json.JSONObject;

/** Reads data/notify.json from the site and shows each new message once as a phone notification. */
public class NotifyWorker extends Worker {
    private static final String CHANNEL = "picks";
    private static final long FIRST_RUN_MS = 6L * 3600 * 1000;    // a new install shows only recent messages
    private static final long MAX_AGE_MS = 24L * 3600 * 1000;

    public NotifyWorker(@NonNull Context c, @NonNull WorkerParameters p) {
        super(c, p);
    }

    @NonNull
    @Override
    public Result doWork() {
        Context ctx = getApplicationContext();
        try {
            HttpURLConnection con = (HttpURLConnection) new URL(
                    MainActivity.HOME + "data/notify.json?t=" + System.currentTimeMillis()).openConnection();
            con.setConnectTimeout(15000);
            con.setReadTimeout(15000);
            con.setUseCaches(false);
            if (con.getResponseCode() != 200) {
                return Result.success();
            }
            StringBuilder sb = new StringBuilder();
            try (BufferedReader r = new BufferedReader(new InputStreamReader(con.getInputStream(), StandardCharsets.UTF_8))) {
                String line;
                while ((line = r.readLine()) != null) {
                    sb.append(line);
                }
            }
            JSONArray msgs = new JSONObject(sb.toString()).optJSONArray("messages");
            if (msgs == null) {
                return Result.success();
            }
            SharedPreferences prefs = ctx.getSharedPreferences("notify", Context.MODE_PRIVATE);
            boolean first = !prefs.contains("seen");
            Set<String> seen = new HashSet<>(prefs.getStringSet("seen", new HashSet<>()));
            Set<String> ids = new HashSet<>();
            long now = System.currentTimeMillis();
            for (int i = 0; i < msgs.length(); i++) {
                JSONObject m = msgs.getJSONObject(i);
                String id = m.optString("id");
                ids.add(id);
                if (id.isEmpty() || seen.contains(id)) {
                    continue;
                }
                seen.add(id);
                long age = now - m.optLong("ts", 0) * 1000;
                if (age > MAX_AGE_MS || (first && age > FIRST_RUN_MS)) {
                    continue;
                }
                show(ctx, id, m.optString("title", "NSE Lab"), m.optString("text"));
            }
            seen.retainAll(ids);
            prefs.edit().putStringSet("seen", seen).apply();
        } catch (Exception e) {
            return Result.success();      // try again at the next check
        }
        return Result.success();
    }

    private static void show(Context ctx, String id, String title, String text) {
        if (Build.VERSION.SDK_INT >= 33
                && ctx.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            return;
        }
        NotificationManager nm = (NotificationManager) ctx.getSystemService(Context.NOTIFICATION_SERVICE);
        nm.createNotificationChannel(new NotificationChannel(CHANNEL, "Stock lists and alerts",
                NotificationManager.IMPORTANCE_DEFAULT));
        Intent open = new Intent(ctx, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        PendingIntent pi = PendingIntent.getActivity(ctx, 0, open, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        Notification n = new Notification.Builder(ctx, CHANNEL)
                .setSmallIcon(R.drawable.ic_notify)
                .setContentTitle(title)
                .setContentText(text)
                .setStyle(new Notification.BigTextStyle().bigText(text))
                .setContentIntent(pi)
                .setAutoCancel(true)
                .build();
        nm.notify(id.hashCode(), n);
    }
}
