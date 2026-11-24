package com.ashok.nselab;

import android.Manifest;
import android.app.Activity;
import android.content.pm.PackageManager;
import android.os.Build;
import androidx.work.Constraints;
import androidx.work.ExistingPeriodicWorkPolicy;
import androidx.work.NetworkType;
import androidx.work.OneTimeWorkRequest;
import androidx.work.PeriodicWorkRequest;
import androidx.work.WorkManager;
import java.util.concurrent.TimeUnit;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

/** The NIFTY Option Lab website in an app window. Links to other sites open in the phone's browser. */
public class MainActivity extends Activity {
    static final String HOME = "https://ashokkumar3502.github.io/nse-research-lab/";
    private static final long RELOAD_AFTER_MS = 5 * 60 * 1000;   // fresh data when coming back after 5 minutes
    private WebView web;
    private long pausedAt = 0;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        web = new WebView(this);
        setContentView(web);
        startNotifications();
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setCacheMode(WebSettings.LOAD_DEFAULT);
        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest r) {
                Uri u = r.getUrl();
                if (u.toString().startsWith(HOME)) {
                    return false;
                }
                startActivity(new Intent(Intent.ACTION_VIEW, u));
                return true;
            }

            @Override
            public void onReceivedError(WebView v, WebResourceRequest r, WebResourceError e) {
                if (r.isForMainFrame()) {
                    v.loadDataWithBaseURL(HOME, "<html><body style='font-family:sans-serif;padding:28px;line-height:1.5'>"
                        + "<h2>No internet</h2><p>NIFTY Option Lab needs an internet connection.</p>"
                        + "<p><a href='" + HOME + "'>Try again</a></p></body></html>", "text/html", "utf-8", null);
                }
            }
        });
        if (state != null) {
            web.restoreState(state);
        } else {
            web.loadUrl(HOME);
        }
    }

    /** Ask once for notification permission (Android 13+) and check for new messages about every 15 minutes. */
    private void startNotifications() {
        if (Build.VERSION.SDK_INT >= 33
                && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[] {Manifest.permission.POST_NOTIFICATIONS}, 1);
        }
        Constraints online = new Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build();
        WorkManager wm = WorkManager.getInstance(getApplicationContext());
        wm.enqueueUniquePeriodicWork("notify", ExistingPeriodicWorkPolicy.KEEP,
                new PeriodicWorkRequest.Builder(NotifyWorker.class, 15, TimeUnit.MINUTES).setConstraints(online).build());
        wm.enqueue(new OneTimeWorkRequest.Builder(NotifyWorker.class).setConstraints(online).build());
    }

    @Override
    protected void onSaveInstanceState(Bundle out) {
        super.onSaveInstanceState(out);
        web.saveState(out);
    }

    @Override
    protected void onPause() {
        super.onPause();
        pausedAt = System.currentTimeMillis();
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (pausedAt > 0 && System.currentTimeMillis() - pausedAt > RELOAD_AFTER_MS) {
            web.reload();
        }
    }

    @Override
    public void onBackPressed() {
        if (web.canGoBack()) {
            web.goBack();
        } else {
            super.onBackPressed();
        }
    }
}
