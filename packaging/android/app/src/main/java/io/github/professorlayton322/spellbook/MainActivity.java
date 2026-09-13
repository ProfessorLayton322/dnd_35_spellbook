package io.github.professorlayton322.spellbook;

import android.Manifest;
import android.annotation.SuppressLint;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.Insets;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.view.View;
import android.view.ViewGroup;
import android.view.Window;
import android.view.WindowInsets;
import android.webkit.JsResult;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.PopupMenu;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.Proxy;
import java.net.URL;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Shows the spellbook web app in a WebView and hands exported files to other apps. */
public class MainActivity extends Activity implements ServerService.Listener {
    private static final int REQUEST_SAVE_EXPORT = 1;
    private static final int REQUEST_NOTIFICATIONS = 2;
    private static final Pattern EXPORT_PATH = Pattern.compile("^/spellbooks/[^/]+/files/[^/]+$");
    private static final Pattern ENCODED_FILENAME = Pattern.compile("filename\\*=UTF-8''([^;]+)", Pattern.CASE_INSENSITIVE);
    private static final Pattern PLAIN_FILENAME = Pattern.compile("filename=\"([^\"]+)\"", Pattern.CASE_INSENSITIVE);

    private final ExecutorService files = Executors.newSingleThreadExecutor();
    private WebView webView;
    private View statusPanel;
    private ProgressBar statusProgress;
    private TextView statusText;
    private Button retryButton;
    private int serverPort;
    private boolean serverWasActive;
    /** The export being written to a location chosen in the system's save dialog. */
    private File pendingSave;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);
        webView = findViewById(R.id.web_view);
        statusPanel = findViewById(R.id.status_panel);
        statusProgress = findViewById(R.id.status_progress);
        statusText = findViewById(R.id.status_text);
        retryButton = findViewById(R.id.retry_button);

        drawBehindSystemBars();
        setUpWebView();
        findViewById(R.id.menu_button).setOnClickListener(this::showMenu);
        retryButton.setOnClickListener(view -> retry());

        askForNotificationsOnce();
        ServerService.start(this);
        ServerService.addListener(this);
    }

    @Override
    protected void onDestroy() {
        ServerService.removeListener(this);
        webView.destroy();
        files.shutdown();
        super.onDestroy();
    }

    @Override
    public void onServerStateChanged(ServerService.State state, int port, String error) {
        switch (state) {
            case STARTING:
                serverWasActive = true;
                showStatus(getString(R.string.starting), true, false);
                break;
            case RUNNING:
                serverWasActive = true;
                if (port != serverPort) {
                    serverPort = port;
                    webView.loadUrl(baseUrl());
                }
                break;
            case FAILED:
                showStatus(getString(R.string.start_failed, error), false, true);
                break;
            case STOPPED:
                // Stopped from the notification while this screen is open.
                if (serverWasActive) {
                    finishAndRemoveTask();
                }
                break;
        }
    }

    @SuppressWarnings("deprecation")
    @Override
    public void onBackPressed() {
        // The app is a single screen; like other Android apps, back leaves it running.
        moveTaskToBack(true);
    }

    private String baseUrl() {
        return "http://127.0.0.1:" + serverPort + "/";
    }

    private boolean isAppUrl(Uri uri) {
        return serverPort != 0 && "http".equals(uri.getScheme()) && "127.0.0.1".equals(uri.getHost()) && uri.getPort() == serverPort;
    }

    private void drawBehindSystemBars() {
        Window window = getWindow();
        window.setStatusBarColor(Color.TRANSPARENT);
        window.setNavigationBarColor(Color.TRANSPARENT);
        if (Build.VERSION.SDK_INT >= 30) {
            window.setDecorFitsSystemWindows(false);
        } else {
            window.getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                    | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION);
        }
        View root = findViewById(R.id.root);
        View statusBarSpacer = findViewById(R.id.status_bar_spacer);
        root.setOnApplyWindowInsetsListener((view, insets) -> {
            int left, top, right, bottom;
            if (Build.VERSION.SDK_INT >= 30) {
                Insets bars = insets.getInsets(WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout() | WindowInsets.Type.ime());
                left = bars.left;
                top = bars.top;
                right = bars.right;
                bottom = bars.bottom;
            } else {
                left = insets.getSystemWindowInsetLeft();
                top = insets.getSystemWindowInsetTop();
                right = insets.getSystemWindowInsetRight();
                bottom = insets.getSystemWindowInsetBottom();
            }
            ViewGroup.LayoutParams spacer = statusBarSpacer.getLayoutParams();
            spacer.height = top;
            statusBarSpacer.setLayoutParams(spacer);
            view.setPadding(left, 0, right, bottom);
            return Build.VERSION.SDK_INT >= 30 ? WindowInsets.CONSUMED : insets.consumeSystemWindowInsets();
        });
    }

    @SuppressLint("SetJavaScriptEnabled")
    private void setUpWebView() {
        webView.setBackgroundColor(getColor(R.color.page));
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                if (!isAppUrl(uri)) {
                    openExternally(uri);
                    return true;
                }
                if (EXPORT_PATH.matcher(String.valueOf(uri.getPath())).matches()) {
                    openExport(uri);
                    return true;
                }
                return false;
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                if (isAppUrl(Uri.parse(url)) && retryButton.getVisibility() != View.VISIBLE) {
                    statusPanel.setVisibility(View.GONE);
                    webView.setVisibility(View.VISIBLE);
                }
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) {
                    showStatus(getString(R.string.connection_lost), false, true);
                }
            }
        });

        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onJsAlert(WebView view, String url, String message, JsResult result) {
                new AlertDialog.Builder(MainActivity.this)
                        .setMessage(message)
                        .setPositiveButton(android.R.string.ok, (dialog, which) -> result.confirm())
                        .setOnCancelListener(dialog -> result.cancel())
                        .show();
                return true;
            }

            @Override
            public boolean onJsConfirm(WebView view, String url, String message, JsResult result) {
                new AlertDialog.Builder(MainActivity.this)
                        .setMessage(message)
                        .setPositiveButton(android.R.string.ok, (dialog, which) -> result.confirm())
                        .setNegativeButton(android.R.string.cancel, (dialog, which) -> result.cancel())
                        .setOnCancelListener(dialog -> result.cancel())
                        .show();
                return true;
            }
        });

        webView.setDownloadListener((url, userAgent, contentDisposition, mimeType, contentLength) -> {
            Uri uri = Uri.parse(url);
            if (isAppUrl(uri)) {
                openExport(uri);
            }
        });
    }

    private void showStatus(String message, boolean busy, boolean canRetry) {
        statusText.setText(message);
        statusProgress.setVisibility(busy ? View.VISIBLE : View.GONE);
        retryButton.setVisibility(canRetry ? View.VISIBLE : View.GONE);
        statusPanel.setVisibility(View.VISIBLE);
        webView.setVisibility(View.INVISIBLE);
    }

    private void retry() {
        retryButton.setVisibility(View.GONE);
        showStatus(getString(R.string.starting), true, false);
        ServerService.start(this);
        if (serverPort != 0) {
            webView.loadUrl(baseUrl());
        }
    }

    private void showMenu(View anchor) {
        PopupMenu menu = new PopupMenu(this, anchor);
        menu.getMenuInflater().inflate(R.menu.main, menu.getMenu());
        menu.setOnMenuItemClickListener(item -> {
            int id = item.getItemId();
            if (id == R.id.action_open_browser) {
                openInBrowser();
            } else if (id == R.id.action_reload) {
                if (serverPort != 0) {
                    webView.reload();
                }
            } else if (id == R.id.action_quit) {
                ServerService.stop(this);
                finishAndRemoveTask();
            }
            return true;
        });
        menu.show();
    }

    private void openInBrowser() {
        if (serverPort == 0) {
            return;
        }
        // Keep the selected spellbook but not one-time notices from the last action.
        Uri.Builder target = Uri.parse(baseUrl()).buildUpon();
        String current = webView.getUrl();
        if (current != null && isAppUrl(Uri.parse(current))) {
            String book = Uri.parse(current).getQueryParameter("book");
            if (book != null) {
                target.appendQueryParameter("book", book);
            }
        }
        ServerService.usedFromBrowser = true;
        if (openExternally(target.build())) {
            Toast.makeText(this, R.string.browser_notice, Toast.LENGTH_LONG).show();
        }
    }

    private boolean openExternally(Uri uri) {
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, uri).addCategory(Intent.CATEGORY_BROWSABLE));
            return true;
        } catch (ActivityNotFoundException e) {
            Toast.makeText(this, R.string.no_browser, Toast.LENGTH_LONG).show();
            return false;
        }
    }

    private void openExport(Uri uri) {
        Toast.makeText(this, R.string.preparing_file, Toast.LENGTH_SHORT).show();
        files.execute(() -> {
            try {
                File file = download(uri);
                runOnUiThread(() -> showExportChoices(file));
            } catch (IOException e) {
                runOnUiThread(() -> Toast.makeText(this, getString(R.string.export_failed, e.getMessage()), Toast.LENGTH_LONG).show());
            }
        });
    }

    private File download(Uri uri) throws IOException {
        HttpURLConnection connection = (HttpURLConnection) new URL(uri.toString()).openConnection(Proxy.NO_PROXY);
        try {
            if (connection.getResponseCode() != HttpURLConnection.HTTP_OK) {
                throw new IOException("the app answered " + connection.getResponseCode());
            }
            String name = ExportProvider.safeName(filenameFrom(connection.getHeaderField("Content-Disposition"), uri.getLastPathSegment()));
            File file = ExportProvider.exportFile(this, name);
            try (InputStream in = connection.getInputStream(); OutputStream out = new FileOutputStream(file)) {
                copy(in, out);
            }
            return file;
        } finally {
            connection.disconnect();
        }
    }

    private static String filenameFrom(String disposition, String fallback) {
        if (disposition != null) {
            Matcher encoded = ENCODED_FILENAME.matcher(disposition);
            if (encoded.find()) {
                return Uri.decode(encoded.group(1));
            }
            Matcher plain = PLAIN_FILENAME.matcher(disposition);
            if (plain.find()) {
                return plain.group(1);
            }
        }
        return fallback;
    }

    private void showExportChoices(File file) {
        Uri contentUri = ExportProvider.uriFor(this, file);
        String type = ExportProvider.typeFor(file.getName());
        CharSequence[] choices = {getString(R.string.export_open), getString(R.string.export_save), getString(R.string.export_share)};
        new AlertDialog.Builder(this)
                .setTitle(file.getName())
                .setItems(choices, (dialog, which) -> {
                    if (which == 0) {
                        viewExport(contentUri, type);
                    } else if (which == 1) {
                        saveExport(file, type);
                    } else {
                        Intent send = new Intent(Intent.ACTION_SEND).setType(type).putExtra(Intent.EXTRA_STREAM, contentUri)
                                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
                        startActivity(Intent.createChooser(send, file.getName()));
                    }
                })
                .show();
    }

    private void viewExport(Uri uri, String type) {
        try {
            startActivity(new Intent(Intent.ACTION_VIEW).setDataAndType(uri, type).addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION));
        } catch (ActivityNotFoundException e) {
            Toast.makeText(this, R.string.no_viewer, Toast.LENGTH_LONG).show();
        }
    }

    @SuppressWarnings("deprecation")
    private void saveExport(File file, String type) {
        pendingSave = file;
        Intent intent = new Intent(Intent.ACTION_CREATE_DOCUMENT)
                .addCategory(Intent.CATEGORY_OPENABLE)
                .setType(type)
                .putExtra(Intent.EXTRA_TITLE, file.getName());
        startActivityForResult(intent, REQUEST_SAVE_EXPORT);
    }

    @SuppressWarnings("deprecation")
    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        if (requestCode != REQUEST_SAVE_EXPORT) {
            super.onActivityResult(requestCode, resultCode, data);
            return;
        }
        File file = pendingSave;
        pendingSave = null;
        if (resultCode != RESULT_OK || data == null || data.getData() == null || file == null) {
            return;
        }
        Uri target = data.getData();
        files.execute(() -> {
            try (InputStream in = new FileInputStream(file); OutputStream out = getContentResolver().openOutputStream(target)) {
                if (out == null) {
                    throw new IOException("the location cannot be written");
                }
                copy(in, out);
                runOnUiThread(() -> Toast.makeText(this, R.string.export_saved, Toast.LENGTH_SHORT).show());
            } catch (IOException e) {
                runOnUiThread(() -> Toast.makeText(this, getString(R.string.export_failed, e.getMessage()), Toast.LENGTH_LONG).show());
            }
        });
    }

    private void askForNotificationsOnce() {
        // The notification holds the Stop button for when the app is used from a browser.
        if (Build.VERSION.SDK_INT < 33 || checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED) {
            return;
        }
        SharedPreferences preferences = getPreferences(MODE_PRIVATE);
        if (!preferences.getBoolean("asked_for_notifications", false)) {
            preferences.edit().putBoolean("asked_for_notifications", true).apply();
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, REQUEST_NOTIFICATIONS);
        }
    }

    private static void copy(InputStream in, OutputStream out) throws IOException {
        byte[] buffer = new byte[64 * 1024];
        int read;
        while ((read = in.read(buffer)) != -1) {
            out.write(buffer, 0, read);
        }
    }
}
