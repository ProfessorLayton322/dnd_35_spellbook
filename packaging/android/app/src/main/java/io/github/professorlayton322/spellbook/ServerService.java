package io.github.professorlayton322.spellbook;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.graphics.drawable.Icon;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.util.Log;

import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import java.io.File;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Runs the Python web server as a foreground service, so imports and PDF builds keep
 * going in the background and the app can also be used from the device's web browser.
 */
public class ServerService extends Service {
    enum State { STOPPED, STARTING, RUNNING, FAILED }

    interface Listener {
        void onServerStateChanged(State state, int port, String error);
    }

    private static final String TAG = "SpellbookServer";
    private static final String ACTION_STOP = "io.github.professorlayton322.spellbook.STOP";
    private static final String CHANNEL_ID = "server";
    private static final int NOTIFICATION_ID = 1;
    private static final int PREFERRED_PORT = 8735;

    private static final Handler MAIN = new Handler(Looper.getMainLooper());
    // Python calls run one at a time, so a stop always finishes before the next start.
    private static final ExecutorService PYTHON = Executors.newSingleThreadExecutor();
    // Main-thread state shared with the activity.
    private static final List<Listener> listeners = new ArrayList<>();
    private static State state = State.STOPPED;
    private static int port;
    private static String error;

    /** Set when the app is opened in a web browser; the server then outlives the app's task. */
    static volatile boolean usedFromBrowser;

    static void start(Context context) {
        Intent intent = new Intent(context, ServerService.class);
        if (Build.VERSION.SDK_INT >= 26) {
            context.startForegroundService(intent);
        } else {
            context.startService(intent);
        }
    }

    static void stop(Context context) {
        context.startService(new Intent(context, ServerService.class).setAction(ACTION_STOP));
    }

    static void addListener(Listener listener) {
        listeners.add(listener);
        listener.onServerStateChanged(state, port, error);
    }

    static void removeListener(Listener listener) {
        listeners.remove(listener);
    }

    private static void setState(State newState, int newPort, String newError) {
        state = newState;
        port = newPort;
        error = newError;
        for (Listener listener : new ArrayList<>(listeners)) {
            listener.onServerStateChanged(state, port, error);
        }
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && ACTION_STOP.equals(intent.getAction())) {
            shutDown();
            return START_NOT_STICKY;
        }
        startInForeground();
        if (state == State.STOPPED || state == State.FAILED) {
            setState(State.STARTING, 0, null);
            String runtimeDir = new File(getFilesDir(), "runtime").getAbsolutePath();
            String cacheDir = getCacheDir().getAbsolutePath();
            Context app = getApplicationContext();
            PYTHON.execute(() -> {
                try {
                    if (!Python.isStarted()) {
                        Python.start(new AndroidPlatform(app));
                    }
                    int serverPort = Python.getInstance().getModule("spellbook_builder.mobile")
                            .callAttr("start", runtimeDir, cacheDir, PREFERRED_PORT).toInt();
                    MAIN.post(() -> setState(State.RUNNING, serverPort, null));
                } catch (Throwable failure) {
                    Log.e(TAG, "The server could not start", failure);
                    String message = String.valueOf(failure.getMessage());
                    MAIN.post(() -> setState(State.FAILED, 0, message.length() > 600 ? message.substring(0, 600) + "…" : message));
                }
            });
        }
        return START_NOT_STICKY;
    }

    @Override
    public void onTaskRemoved(Intent rootIntent) {
        // Swiping the app away quits it, unless it is also being used in a browser.
        if (!usedFromBrowser) {
            shutDown();
        }
    }

    private void shutDown() {
        usedFromBrowser = false;
        if (state != State.STOPPED) {
            setState(State.STOPPED, 0, null);
        }
        PYTHON.execute(() -> {
            if (Python.isStarted()) {
                Python.getInstance().getModule("spellbook_builder.mobile").callAttr("stop");
            }
        });
        stopForeground(STOP_FOREGROUND_REMOVE);
        stopSelf();
    }

    private void startInForeground() {
        NotificationManager manager = getSystemService(NotificationManager.class);
        Notification.Builder builder;
        if (Build.VERSION.SDK_INT >= 26) {
            manager.createNotificationChannel(new NotificationChannel(
                    CHANNEL_ID, getString(R.string.notification_channel), NotificationManager.IMPORTANCE_LOW));
            builder = new Notification.Builder(this, CHANNEL_ID);
        } else {
            builder = new Notification.Builder(this).setPriority(Notification.PRIORITY_LOW);
        }
        PendingIntent open = PendingIntent.getActivity(this, 0, new Intent(this, MainActivity.class),
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        PendingIntent stop = PendingIntent.getService(this, 1, new Intent(this, ServerService.class).setAction(ACTION_STOP),
                PendingIntent.FLAG_IMMUTABLE);
        Notification notification = builder
                .setSmallIcon(R.drawable.ic_notification)
                .setContentTitle(getString(R.string.app_name))
                .setContentText(getString(R.string.notification_text))
                .setContentIntent(open)
                .setOngoing(true)
                .addAction(new Notification.Action.Builder(
                        Icon.createWithResource(this, R.drawable.ic_notification), getString(R.string.stop), stop).build())
                .build();
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE);
        } else {
            startForeground(NOTIFICATION_ID, notification);
        }
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
