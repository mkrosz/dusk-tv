package io.dusktv.home;

import android.content.ActivityNotFoundException;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.drawable.BitmapDrawable;
import android.graphics.drawable.Drawable;
import android.media.tv.TvContract;
import android.media.tv.TvInputInfo;
import android.media.tv.TvInputManager;
import android.net.ConnectivityManager;
import android.net.NetworkInfo;
import android.net.wifi.WifiManager;
import android.os.Build;
import android.provider.Settings;
import android.webkit.JavascriptInterface;
import android.webkit.WebResourceResponse;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.NetworkInterface;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * What the page (web/tv.js) asks the TV, as "DuskAndroid" in JavaScript. Every answer is JSON text, which tv.js
 * parses (androidCall). The calls arrive on WebView's own thread, not the UI thread.
 */
public class Bridge {

    /** Not shown among the apps: launchers (this one and the TV's own) and the store, which has its own tile. */
    private static final String[] HIDDEN = {
        "com.google.android.apps.tv.launcherx", "com.google.android.tvlauncher", "com.google.android.leanbacklauncher",
        "com.android.launcher3", "com.google.android.tungsten.setupwraith"
    };

    private final MainActivity activity;
    private final Context context;
    private final PackageManager pm;
    private final Map<String, byte[]> icons = new HashMap<>();

    Bridge(MainActivity activity) {
        this.activity = activity;
        this.context = activity.getApplicationContext();
        this.pm = activity.getPackageManager();
    }

    // MARK: - Apps

    /** The apps the TV's home lists: [{id, title, icon, color}], by name. */
    @JavascriptInterface
    public String apps() {
        Map<String, JSONObject> seen = new LinkedHashMap<>();
        String self = context.getPackageName();
        for (String category : new String[] {Intent.CATEGORY_LEANBACK_LAUNCHER, Intent.CATEGORY_LAUNCHER}) {
            List<ResolveInfo> found = pm.queryIntentActivities(new Intent(Intent.ACTION_MAIN).addCategory(category), 0);
            for (ResolveInfo r : found) {
                String pkg = r.activityInfo.packageName;
                if (pkg.equals(self) || seen.containsKey(pkg) || hidden(pkg)) continue;
                try {
                    JSONObject app = new JSONObject();
                    app.put("id", pkg);
                    app.put("title", String.valueOf(r.loadLabel(pm)));
                    app.put("icon", MainActivity.ICON_HOST + pkg);
                    app.put("color", "");
                    seen.put(pkg, app);
                } catch (JSONException e) {
                    // left out
                }
            }
        }
        List<JSONObject> list = new ArrayList<>(seen.values());
        Collections.sort(list, new Comparator<JSONObject>() {
            @Override
            public int compare(JSONObject a, JSONObject b) {
                return a.optString("title").compareToIgnoreCase(b.optString("title"));
            }
        });
        return new JSONArray(list).toString();
    }

    private static boolean hidden(String pkg) {
        for (String h : HIDDEN) if (h.equals(pkg)) return true;
        return false;
    }

    /** Opens an app by its package, or an input ("input:<id>", "input:live"); true when Android took it. */
    @JavascriptInterface
    public String launch(String id) {
        Intent intent;
        if (id.equals("input:live")) {
            intent = new Intent(Intent.ACTION_VIEW, TvContract.Channels.CONTENT_URI);
        } else if (id.startsWith("input:")) {
            intent = new Intent(Intent.ACTION_VIEW, TvContract.buildChannelUriForPassthroughInput(id.substring(6)));
        } else {
            intent = pm.getLeanbackLaunchIntentForPackage(id);
            if (intent == null) intent = pm.getLaunchIntentForPackage(id);
        }
        return String.valueOf(start(intent));
    }

    private boolean start(Intent intent) {
        if (intent == null) return false;
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        try {
            activity.startActivity(intent);
            return true;
        } catch (ActivityNotFoundException | SecurityException e) {
            return false;
        }
    }

    /** An app's banner (Android TV's 16:9 tile), or its icon when it has none, as PNG for the page's <img>. */
    WebResourceResponse iconResponse(String pkg) {
        byte[] png;
        synchronized (icons) {
            png = icons.get(pkg);
        }
        if (png == null) {
            Drawable d = null;
            Intent tv = pm.getLeanbackLaunchIntentForPackage(pkg);
            try {
                if (tv != null) d = pm.getActivityBanner(tv);
                if (d == null) d = pm.getApplicationBanner(pkg);
                if (d == null) d = pm.getApplicationIcon(pkg);
            } catch (PackageManager.NameNotFoundException e) {
                d = null;
            }
            if (d == null) return new WebResourceResponse("image/png", null, 404, "Not Found", null, null);
            png = toPng(d);
            synchronized (icons) {
                icons.put(pkg, png);
            }
        }
        return new WebResourceResponse("image/png", null, new ByteArrayInputStream(png));
    }

    private static byte[] toPng(Drawable d) {
        Bitmap bitmap;
        if (d instanceof BitmapDrawable && ((BitmapDrawable) d).getBitmap() != null) {
            bitmap = ((BitmapDrawable) d).getBitmap();
        } else {
            int w = d.getIntrinsicWidth() > 0 ? d.getIntrinsicWidth() : 320;
            int h = d.getIntrinsicHeight() > 0 ? d.getIntrinsicHeight() : 180;
            bitmap = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888);
            Canvas canvas = new Canvas(bitmap);
            d.setBounds(0, 0, w, h);
            d.draw(canvas);
        }
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        bitmap.compress(Bitmap.CompressFormat.PNG, 100, out);
        return out.toByteArray();
    }

    // MARK: - Inputs

    /**
     * The TV's inputs: [{id, key, label, connected}]. One "TV ao vivo" for the tuner, then every HDMI and other
     * passthrough input. Empty on a box (a Chromecast) or where Android doesn't share them.
     */
    @JavascriptInterface
    public String inputs() {
        JSONArray out = new JSONArray();
        TvInputManager tim = (TvInputManager) context.getSystemService(Context.TV_INPUT_SERVICE);
        if (tim == null) return out.toString();
        try {
            List<TvInputInfo> list = tim.getTvInputList();
            boolean live = false;
            int hdmi = 0;
            List<JSONObject> passthrough = new ArrayList<>();
            for (TvInputInfo info : list) {
                if (Build.VERSION.SDK_INT >= 24 && info.isHidden(context)) continue;
                if (!info.isPassthroughInput()) {
                    live = true;
                    continue;
                }
                JSONObject x = new JSONObject();
                x.put("id", "input:" + info.getId());
                x.put("key", "hdmi" + (++hdmi));
                CharSequence label = Build.VERSION.SDK_INT >= 24 ? info.loadCustomLabel(context) : null;
                if (label == null || label.length() == 0) label = info.loadLabel(context);
                x.put("label", label == null ? "HDMI " + hdmi : label.toString());
                boolean connected = true;
                if (Build.VERSION.SDK_INT >= 24) {
                    connected = tim.getInputState(info.getId()) == TvInputManager.INPUT_STATE_CONNECTED;
                }
                x.put("connected", connected);
                passthrough.add(x);
            }
            if (live) {
                JSONObject t = new JSONObject();
                t.put("id", "input:live");
                t.put("key", "live");
                t.put("label", JSONObject.NULL);
                t.put("connected", true);
                out.put(t);
            }
            for (JSONObject x : passthrough) out.put(x);
        } catch (JSONException | RuntimeException e) {
            // no inputs, then
        }
        return out.toString();
    }

    // MARK: - Settings, network, about

    /**
     * The TV's settings: '' the main screen, or 'network', 'account', 'privacy'. {ok, direct}: direct is false when
     * the page asked for isn't there and the main settings opened instead.
     */
    @JavascriptInterface
    public String settings(String kind) {
        String action = null;
        if ("network".equals(kind)) action = Settings.ACTION_WIFI_SETTINGS;
        else if ("account".equals(kind)) action = Settings.ACTION_SYNC_SETTINGS;
        else if ("privacy".equals(kind)) action = Settings.ACTION_PRIVACY_SETTINGS;
        boolean direct = false, ok = false;
        if (action != null) {
            Intent page = new Intent(action);
            if (page.resolveActivity(pm) != null) ok = direct = start(page);
        }
        if (!ok) {
            ok = start(new Intent(Settings.ACTION_SETTINGS));
            direct = action == null && ok;
        }
        try {
            return new JSONObject().put("ok", ok).put("direct", direct).toString();
        } catch (JSONException e) {
            return "{\"ok\":" + ok + ",\"direct\":false}";
        }
    }

    /** {type: 'wifi' | 'ethernet' | 'none', bars: 0-4, ssid}. The network's name needs location access: left out. */
    @JavascriptInterface
    public String network() {
        String type = "none";
        int bars = 0;
        ConnectivityManager cm = (ConnectivityManager) context.getSystemService(Context.CONNECTIVITY_SERVICE);
        NetworkInfo info = cm == null ? null : cm.getActiveNetworkInfo();
        if (info != null && info.isConnected()) {
            if (info.getType() == ConnectivityManager.TYPE_ETHERNET) {
                type = "ethernet";
                bars = 4;
            } else {
                type = "wifi";
                bars = 3;
                WifiManager wifi = (WifiManager) context.getSystemService(Context.WIFI_SERVICE);
                if (wifi != null && wifi.getConnectionInfo() != null) {
                    bars = Math.max(1, WifiManager.calculateSignalLevel(wifi.getConnectionInfo().getRssi(), 5));
                    bars = Math.min(bars, 4);
                }
            }
        }
        try {
            return new JSONObject().put("type", type).put("bars", bars).put("ssid", "").toString();
        } catch (JSONException e) {
            return "{\"type\":\"none\",\"bars\":0,\"ssid\":\"\"}";
        }
    }

    /** The TV's maker and model, Android's version, this app's version and the TV's address, for Settings. */
    @JavascriptInterface
    public String about() {
        JSONObject out = new JSONObject();
        try {
            String maker = Build.MANUFACTURER == null ? "" : Build.MANUFACTURER.trim();
            if (maker.length() > 0) maker = maker.substring(0, 1).toUpperCase() + maker.substring(1);
            out.put("maker", maker);
            out.put("model", Build.MODEL);
            out.put("system", "Android " + Build.VERSION.RELEASE);
            out.put("firmware", Build.ID);
            String version = "";
            try {
                PackageInfo p = pm.getPackageInfo(context.getPackageName(), 0);
                version = p.versionName;
            } catch (PackageManager.NameNotFoundException e) {
                // left empty
            }
            out.put("version", version);
            out.put("ip", address());
        } catch (JSONException e) {
            // what was put stays
        }
        return out.toString();
    }

    private static String address() {
        try {
            for (NetworkInterface ni : Collections.list(NetworkInterface.getNetworkInterfaces())) {
                if (!ni.isUp() || ni.isLoopback()) continue;
                for (InetAddress a : Collections.list(ni.getInetAddresses())) {
                    if (a instanceof Inet4Address && !a.isLoopbackAddress()) return a.getHostAddress();
                }
            }
        } catch (Exception e) {
            // none
        }
        return "";
    }

    /** Back on the first screen. */
    @JavascriptInterface
    public void back() {
        activity.runOnUiThread(new Runnable() {
            @Override
            public void run() {
                activity.leave();
            }
        });
    }

    /** Whether Dusk TV is the TV's home screen (the Home button's app). */
    boolean isDefaultHome() {
        Intent home = new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME);
        ResolveInfo r = pm.resolveActivity(home, PackageManager.MATCH_DEFAULT_ONLY);
        return r != null && r.activityInfo != null && context.getPackageName().equals(r.activityInfo.packageName);
    }
}
